# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import base64
import hmac
import json
import json as _json
import logging
import os
import re
import time
import uuid
from datetime import datetime
from hashlib import sha256
from typing import Any, Dict, List, NamedTuple, Optional
from urllib.parse import unquote, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from httpx import ASGITransport
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import internal_facilitator
from .headers import (
    HeaderError,
    parse_risk_ids,
    parse_x_ap2_evidence,
    parse_x_payment_secure,
    parse_x_verifiable_intent,
)
from .risk_routes import Decision, EvaluateResponse

# Optional deps
try:  # pragma: no cover - optional dependency path
    from eth_utils import keccak  # type: ignore

    HAVE_KECCAK = True
except Exception:  # pragma: no cover - optional dependency path
    HAVE_KECCAK = False

try:  # pragma: no cover - optional dependency path
    from eth_account import Account  # type: ignore
    from eth_account.messages import encode_typed_data  # type: ignore

    HAVE_EIP712 = True
except Exception:  # pragma: no cover - optional dependency path
    HAVE_EIP712 = False

# x402 SDK types/utilities
from x402.encoding import safe_base64_decode, safe_base64_encode
from x402.types import PaymentPayload, PaymentRequirements

logger = logging.getLogger(__name__)

# -------------------------------
# Helpers
# -------------------------------


def _now_s() -> int:
    return int(time.time())


def _parse_iso8601(ts: str) -> int:
    return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())


def _b32(hexstr: str) -> bytes:
    h = hexstr[2:] if hexstr.startswith("0x") else hexstr
    if not re.fullmatch(r"[0-9a-fA-F]{1,64}", h):
        raise ValueError("Expected a bytes32 hex value")
    return bytes.fromhex(h.zfill(64))


def _keccak(data: bytes) -> bytes:
    if HAVE_KECCAK:
        return keccak(data)  # type: ignore
    raise RuntimeError("keccak256 requires eth-utils installed")


def _sha256_lower_origin(origin: str) -> bytes:
    return sha256(origin.strip().lower().encode("utf-8")).digest()


def _canon_b64_json(obj: Any) -> bytes:
    s = json.dumps(obj, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    return base64.b64encode(s.encode("utf-8"))


def _extract_payer_from_payment_payload(pp: Dict[str, Any]) -> Optional[str]:
    for path in (
        ("payload", "authorization", "from"),
        ("payload", "from"),
        ("authorization", "from"),
        ("from",),
        ("payer",),
    ):
        ref: Any = pp
        ok = True
        for key in path:
            if isinstance(ref, dict) and key in ref:
                ref = ref[key]
            else:
                ok = False
                break
        if ok and isinstance(ref, str):
            return ref
    return None


def _env_chain_map() -> Dict[str, int]:
    """Parse PROXY_NETWORK_CHAIN_MAP env into a dict.

    Accepts either JSON (e.g., '{"base":8453,"base-sepolia":84532}') or
    a comma-separated list of pairs (e.g., 'base:8453,base-sepolia:84532').
    """
    default = {"base": 8453, "base-sepolia": 84532}
    raw = os.getenv("PROXY_NETWORK_CHAIN_MAP")
    if not raw:
        return default
    try:
        if raw.strip().startswith("{"):
            parsed = _json.loads(raw)
            return {**default, **{str(k): int(v) for k, v in parsed.items()}}
        # simple "k:v,k:v" form
        out: Dict[str, int] = dict(default)
        for part in raw.split(","):
            if not part.strip():
                continue
            k, v = part.split(":", 1)
            out[k.strip()] = int(v.strip())
        return out
    except Exception:
        return default


def _network_to_chain_id(network: str) -> int:
    mapping = _env_chain_map()
    return mapping.get(network, 0)


# -------------------------------
# Models
# -------------------------------


class ProxyVerifyRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    x402Version: int = Field(1, description="x402 API version")
    paymentPayload: PaymentPayload
    paymentRequirements: PaymentRequirements
    ap2EvidenceHeader: Optional[str] = Field(
        None, description="base64(JSON Evidence) if not in header"
    )
    verifiable_intent: Optional[Dict[str, Any]] = Field(
        default=None,
        alias="verifiableIntent",
        description="Inline Verifiable Intent evidence or reference metadata.",
    )
    ap2_context: Optional[Dict[str, Any]] = Field(
        default=None,
        alias="ap2Context",
        description="AP2 mandate references or inline mandate context.",
    )
    trace_context: Optional[Dict[str, Any]] = Field(
        default=None,
        alias="traceContext",
        description="Agent trace context supplied by SDKs or facilitators.",
    )
    vi_policy: Optional[Dict[str, Any]] = Field(
        default=None,
        alias="viPolicy",
        description="Per-request Verifiable Intent policy overrides.",
    )
    policy: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Generic policy object accepted for facilitator compatibility.",
    )


class VerifyResponse(BaseModel):
    isValid: bool
    payer: str
    invalidReason: Optional[str] = None


class ProxySettleRequest(ProxyVerifyRequest):
    pass


class SettleResponse(BaseModel):
    success: bool
    payer: str
    transaction: Optional[str] = None
    network: Optional[str] = None
    errorReason: Optional[str] = None


class AP2Evidence(BaseModel):
    v: int
    paymentHash: str
    resource: str
    originHash: str
    network: str
    asset: str
    payTo: str
    intent_uid: str
    cart_uid: Optional[str] = None
    payment_uid: Optional[str] = None
    trace_uid: str
    # validity window
    notBefore: Optional[int] = None
    notAfter: Optional[int] = None
    exp: Optional[str] = None  # ISO8601 alternative
    # signature
    sig: Optional[str] = None
    kid: Optional[str] = None


class AP2Policy(BaseModel):
    requireIntentMandate: bool = False
    requireCartMandate: bool = False
    requirePaymentMandate: bool = False
    requireTrace: bool = False
    acceptedMerchantIds: Optional[List[str]] = None


_AP2_POLICY_ALIASES = {
    "require_intent_mandate": "requireIntentMandate",
    "require_cart_mandate": "requireCartMandate",
    "require_payment_mandate": "requirePaymentMandate",
    "require_trace": "requireTrace",
    "accepted_merchant_ids": "acceptedMerchantIds",
}


def extract_ap2_policy(
    payment_requirements: PaymentRequirements, floor: Optional[AP2Policy] = None
) -> AP2Policy:
    extra = payment_requirements.extra or {}
    sources = [extra[alias] for alias in ("ap2", "ap2-evidence") if alias in extra]
    return _merge_ap2_policies(sources, floor)


def _merge_ap2_policies(sources: list, floor: Optional[AP2Policy] = None) -> AP2Policy:
    policy = (floor or AP2Policy()).model_dump()
    for source in sources:
        if not isinstance(source, dict):
            raise HTTPException(status_code=422, detail="Invalid AP2 policy")
        for name, incoming in source.items():
            key = _AP2_POLICY_ALIASES.get(name, name)
            if key == "acceptedMerchantIds":
                if not isinstance(incoming, list) or any(
                    not isinstance(mid, str) or not mid for mid in incoming
                ):
                    raise HTTPException(status_code=422, detail="Invalid AP2 merchant constraints")
                if incoming:
                    allowed = policy[key]
                    policy[key] = sorted(set(incoming) & set(allowed)) if allowed else incoming
                    if not policy[key]:
                        raise HTTPException(
                            status_code=422, detail="Conflicting AP2 merchant constraints"
                        )
            elif key in policy:
                required = _coerce_policy_bool(incoming)
                if required is None:
                    raise HTTPException(status_code=422, detail="Invalid AP2 policy")
                policy[key] |= required
    return AP2Policy(**policy)


def parse_ap2_evidence_b64(header_val: Optional[str], body_val: Optional[str]) -> AP2Evidence:
    raw = header_val or body_val
    if not raw:
        raise HTTPException(status_code=422, detail="Missing AP2 evidence")
    try:
        data = json.loads(safe_base64_decode(raw))
        return AP2Evidence(**data)
    except Exception as e:  # pragma: no cover - input validation
        raise HTTPException(status_code=422, detail=f"Invalid AP2 evidence: {e}")


def enforce_policy_flags(policy: AP2Policy, ev: AP2Evidence) -> None:
    if policy.requireIntentMandate and not (ev.intent_uid and any(_b32(ev.intent_uid))):
        raise HTTPException(status_code=422, detail="AP2: intent_uid required")
    if policy.requireCartMandate and not (ev.cart_uid and any(_b32(ev.cart_uid))):
        raise HTTPException(status_code=422, detail="AP2: cart_uid required")
    if policy.requirePaymentMandate and not (ev.payment_uid and any(_b32(ev.payment_uid))):
        raise HTTPException(status_code=422, detail="AP2: payment_uid required")
    if policy.requireTrace and not (ev.trace_uid and any(_b32(ev.trace_uid))):
        raise HTTPException(status_code=422, detail="AP2: trace_uid required")


def verify_origin_binding(
    ev: AP2Evidence, payment_requirements: PaymentRequirements, origin_header: Optional[str]
) -> None:
    resource = httpx.URL(payment_requirements.resource)
    origin = f"{resource.scheme}://{resource.netloc.decode('ascii')}"
    if resource.scheme not in {"http", "https"} or not resource.host:
        raise HTTPException(status_code=422, detail="Invalid AP2 resource origin")
    if origin_header and origin_header.lower() != origin.lower():
        raise HTTPException(status_code=422, detail="AP2: origin differs from payment requirements")
    expected = _sha256_lower_origin(origin)
    if _b32(ev.originHash).hex() != expected.hex():
        raise HTTPException(status_code=422, detail="AP2: originHash mismatch")


def verify_congruence(ev: AP2Evidence, payment_requirements: PaymentRequirements) -> None:
    pr = payment_requirements
    if ev.resource != pr.resource:
        raise HTTPException(status_code=422, detail="AP2: resource mismatch")
    if ev.network != pr.network:
        raise HTTPException(status_code=422, detail="AP2: network mismatch")
    if ev.payTo.lower() != pr.pay_to.lower():
        raise HTTPException(status_code=422, detail="AP2: payTo mismatch")
    asset_req = (pr.asset or "").lower()
    if asset_req and ev.asset.lower() != asset_req:
        raise HTTPException(status_code=422, detail="AP2: asset mismatch")


def verify_ttl(ev: AP2Evidence) -> None:
    now = _now_s()
    if ev.notBefore and now < ev.notBefore:
        raise HTTPException(status_code=422, detail="AP2: notBefore not reached")
    if ev.notAfter and now > ev.notAfter:
        raise HTTPException(status_code=422, detail="AP2: notAfter passed")
    if ev.exp and _now_s() > _parse_iso8601(ev.exp):
        raise HTTPException(status_code=422, detail="AP2: exp passed")


def compute_expected_payment_hash(request: Request, payload_obj: Dict[str, Any]) -> bytes:
    x_payment = request.headers.get("X-PAYMENT")
    if x_payment:
        try:
            # Use x402 safe decoder for robustness (handles padding/urlsafe)
            decoded_str = safe_base64_decode(x_payment)
            decoded = decoded_str.encode("utf-8")
            return _keccak(decoded)
        except Exception:  # pragma: no cover - input validation
            raise HTTPException(status_code=422, detail="Invalid X-PAYMENT header base64")
    try:
        b64 = _canon_b64_json(payload_obj)
        return _keccak(b64)
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=422, detail=f"Cannot canonicalize paymentPayload: {e}")


def verify_payment_hash_binding(
    ev: AP2Evidence, req: Request, payment_payload: PaymentPayload
) -> None:
    payload_obj = payment_payload.model_dump(by_alias=True)
    expected = compute_expected_payment_hash(
        req, getattr(req.state, "payment_payload", payload_obj)
    )
    if _b32(ev.paymentHash) != expected:
        raise HTTPException(status_code=422, detail="AP2: paymentHash mismatch")


def verify_merchant_identity(policy: AP2Policy, pr: PaymentRequirements) -> None:
    if not policy.acceptedMerchantIds:
        return
    host = urlparse(pr.resource).netloc.lower()
    ok = any(
        mid.startswith("did:web:") and mid.split(":", 2)[2].lower() in (host, host.split(":")[0])
        for mid in policy.acceptedMerchantIds
    )
    if not ok:
        raise HTTPException(status_code=422, detail="AP2: merchant identity not accepted")


def verify_eip712_signature_if_present(
    ev: AP2Evidence, pr: PaymentRequirements, payer: Optional[str]
) -> None:
    if not ev.sig:
        return
    if not HAVE_EIP712:
        raise HTTPException(
            status_code=422, detail="EIP-712 verification unavailable; install eth-account"
        )
    chain_id = _network_to_chain_id(pr.network)
    if not chain_id:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unsupported network: {pr.network}. Configure PROXY_NETWORK_CHAIN_MAP to include "
                f"'{pr.network}:<chainId>' or omit EIP-712 signature."
            ),
        )
    domain = {
        "name": "AP2Evidence",
        "version": "1",
        "chainId": chain_id,
        "verifyingContract": ev.payTo,
    }
    types = {
        "EIP712Domain": [
            {"name": "name", "type": "string"},
            {"name": "version", "type": "string"},
            {"name": "chainId", "type": "uint256"},
            {"name": "verifyingContract", "type": "address"},
        ],
        "Evidence": [
            {"name": "paymentHash", "type": "bytes32"},
            {"name": "resource", "type": "string"},
            {"name": "originHash", "type": "bytes32"},
            {"name": "network", "type": "string"},
            {"name": "asset", "type": "address"},
            {"name": "payTo", "type": "address"},
            {"name": "intent_uid", "type": "bytes32"},
            {"name": "cart_uid", "type": "bytes32"},
            {"name": "payment_uid", "type": "bytes32"},
            {"name": "trace_uid", "type": "bytes32"},
            {"name": "notBefore", "type": "uint64"},
            {"name": "notAfter", "type": "uint64"},
        ],
    }

    def to_b32(s: Optional[str]) -> str:
        if not s:
            return "0x" + ("00" * 32)
        return "0x" + _b32(s).hex()

    message = {
        "paymentHash": "0x" + _b32(ev.paymentHash).hex(),
        "resource": ev.resource,
        "originHash": "0x" + _b32(ev.originHash).hex(),
        "network": ev.network,
        "asset": ev.asset,
        "payTo": ev.payTo,
        "intent_uid": to_b32(ev.intent_uid),
        "cart_uid": to_b32(ev.cart_uid),
        "payment_uid": to_b32(ev.payment_uid),
        "trace_uid": to_b32(ev.trace_uid),
        "notBefore": int(ev.notBefore or 0),
        "notAfter": int(ev.notAfter or 0),
    }
    typed = {"types": types, "primaryType": "Evidence", "domain": domain, "message": message}
    try:
        enc = encode_typed_data(full_message=typed)  # type: ignore
        sig_bytes = bytes.fromhex(ev.sig[2:] if ev.sig.startswith("0x") else ev.sig)
        recovered = Account.recover_message(enc, signature=sig_bytes)
        if recovered.lower() != (payer or "").lower():
            raise HTTPException(status_code=422, detail="AP2: signer != payer")
    except HTTPException:
        raise
    except Exception as e:  # pragma: no cover - optional path
        raise HTTPException(status_code=422, detail=f"AP2: EIP-712 signature invalid: {e}")


def enforce_amount_and_asset(
    payment_payload: PaymentPayload, payment_requirements: PaymentRequirements
) -> None:
    auth = payment_payload.model_dump(by_alias=True)["payload"].get("authorization")
    try:
        value = auth["value"]
        limit_value = payment_requirements.max_amount_required
        if any(not re.fullmatch(r"[0-9]{1,78}", str(item)) for item in (value, limit_value)):
            raise ValueError("Invalid atomic amount")
        amount = int(value)
        limit = int(limit_value)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="AP2: invalid authorized amount") from exc
    if not 0 < amount <= limit:
        raise HTTPException(status_code=422, detail="AP2: amount exceeds payment requirements")


async def fetch_and_validate_ap2_context(
    ev: AP2Evidence, risk_engine_url: Optional[str] = None
) -> Dict[str, Any]:
    """
    Fetch trace context and PaymentMandate VC from Risk Engine using UIDs in X-AP2-EVIDENCE.

    This enables deep risk assessment without embedding data in payment payload.
    """
    if not risk_engine_url:
        risk_engine_url = os.getenv("RISK_ENGINE_URL", "http://localhost:8001")

    result = {"trace_context": None, "payment_mandate": None}

    # Fetch trace context using trace_uid
    if ev.trace_uid:
        logger.info(
            f"[PROXY] Fetching trace context for trace_uid={ev.trace_uid} from {risk_engine_url}"
        )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{risk_engine_url}/ap2/trace/{ev.trace_uid}",
                    headers={"Authorization": "Bearer proxy-internal"},  # TODO: proper auth
                )
                logger.info(f"[PROXY] Risk Engine responded with status {resp.status_code}")
                if resp.status_code == 200:
                    result["trace_context"] = resp.json()
                    logger.info(f"[PROXY] Trace context: {result['trace_context']}")
                    logger.info(f"[PROXY] ✅ Fetched trace context for trace_uid={ev.trace_uid}")
                else:
                    logger.warning(
                        "[PROXY] Failed to fetch trace context: status %s, body: %s",
                        resp.status_code,
                        resp.text,
                    )
        except Exception as e:
            logger.warning(f"[PROXY] Exception fetching trace context: {e}")
    else:
        logger.warning("[PROXY] No trace_uid in AP2Evidence, skipping trace context fetch")

    # Fetch PaymentMandate VC using payment_uid
    if ev.payment_uid:
        logger.info(
            "[PROXY] Fetching PaymentMandate for payment_uid=%s from %s",
            ev.payment_uid,
            risk_engine_url,
        )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{risk_engine_url}/ap2/mandate/payment/{ev.payment_uid}",
                    headers={"Authorization": "Bearer proxy-internal"},  # TODO: proper auth
                )
                logger.info(f"[PROXY] Risk Engine responded with status {resp.status_code}")
                if resp.status_code == 200:
                    mandate_data = resp.json()
                    result["payment_mandate"] = mandate_data
                    logger.info(
                        "[PROXY] ✅ Fetched PaymentMandate (risk approval) for payment_uid=%s",
                        ev.payment_uid,
                    )
                else:
                    logger.warning(
                        "[PROXY] Failed to fetch PaymentMandate: status %s, body: %s",
                        resp.status_code,
                        resp.text,
                    )
        except Exception as e:
            logger.warning(f"[PROXY] Exception fetching PaymentMandate: {e}")
    else:
        logger.info("[PROXY] No payment_uid in AP2Evidence, skipping PaymentMandate fetch")

    return result


def verify_ap2(
    request: Request,
    body: ProxyVerifyRequest,
    origin_header: Optional[str],
    ap2_header: Optional[str],
    *,
    required_policy: Optional[AP2Policy] = None,
) -> Optional[AP2Evidence]:
    policy = extract_ap2_policy(body.paymentRequirements, required_policy)
    required = any(policy.model_dump().values())
    if not required and not (ap2_header or body.ap2EvidenceHeader):
        return None
    if ap2_header and body.ap2EvidenceHeader and ap2_header != body.ap2EvidenceHeader:
        raise HTTPException(status_code=422, detail="Conflicting AP2 evidence")
    ev = parse_ap2_evidence_b64(ap2_header, body.ap2EvidenceHeader)
    if required and (not ev.sig or not ev.notAfter):
        raise HTTPException(status_code=422, detail="AP2: signed evidence with expiry required")
    try:
        enforce_policy_flags(policy, ev)
        verify_congruence(ev, body.paymentRequirements)
        verify_ttl(ev)
        verify_origin_binding(ev, body.paymentRequirements, origin_header)
        verify_payment_hash_binding(ev, request, body.paymentPayload)
        verify_merchant_identity(policy, body.paymentRequirements)
        payer = _extract_payer_from_payment_payload(_payment_payload_dict(body, request))
        verify_eip712_signature_if_present(ev, body.paymentRequirements, payer)
        enforce_amount_and_asset(body.paymentPayload, body.paymentRequirements)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid AP2 evidence fields") from exc
    return ev


# -------------------------------
# Proxy router
# -------------------------------


class ProxyRuntimeConfig(BaseModel):
    upstream_verify_url: str = Field(
        default_factory=lambda: os.getenv(
            "PROXY_UPSTREAM_VERIFY_URL", "http://localhost:8001/verify"
        )
    )
    upstream_settle_url: str = Field(
        default_factory=lambda: os.getenv(
            "PROXY_UPSTREAM_SETTLE_URL", "http://localhost:8001/settle"
        )
    )
    timeout_s: float = Field(default_factory=lambda: float(os.getenv("PROXY_TIMEOUT_S", "15")))
    # Optionally evaluate again at settle, in addition to checking the verify authorization.
    settle_risk_enabled: bool = Field(
        default_factory=lambda: (
            os.getenv("PROXY_SETTLE_RISK_ENABLED", "0").lower() in {"1", "true", "yes"}
        )
    )

    authorization_key: Optional[str] = Field(
        default_factory=lambda: os.getenv("PROXY_PAYMENT_AUTHORIZATION_KEY"), repr=False
    )
    merchant_policy: dict = Field(
        default_factory=lambda: json.loads(os.getenv("PROXY_PUBLIC_MERCHANT_POLICY_JSON", "{}"))
    )


def get_proxy_cfg() -> ProxyRuntimeConfig:
    return ProxyRuntimeConfig()


InternalPolicy = internal_facilitator.InternalPolicy
post_trustline_validation = internal_facilitator.post_trustline_validation_default_auth
router = APIRouter(prefix="/x402", tags=["x402-proxy"])


def _error_code_from_message(msg: str) -> str:
    m = (msg or "").lower()
    mapping = {
        "originhash mismatch": "AP2_ORIGIN_MISMATCH",
        "resource mismatch": "AP2_RESOURCE_MISMATCH",
        "network mismatch": "AP2_NETWORK_MISMATCH",
        "payto mismatch": "AP2_PAYTO_MISMATCH",
        "asset mismatch": "AP2_ASSET_MISMATCH",
        "notbefore not reached": "AP2_TTL_NOT_BEFORE",
        "notafter passed": "AP2_TTL_EXPIRED",
        "exp passed": "AP2_TTL_EXPIRED",
        "paymenthash mismatch": "AP2_PAYMENT_HASH_MISMATCH",
        "merchant identity not accepted": "AP2_MERCHANT_DENIED",
        "eip-712 verification unavailable": "AP2_SIG_UNAVAILABLE",
        "eip-712 signature invalid": "AP2_SIG_INVALID",
        "signer != payer": "AP2_SIG_PAYER_MISMATCH",
        "missing ap2 evidence": "AP2_EVIDENCE_MISSING",
        "invalid ap2 evidence": "AP2_EVIDENCE_INVALID",
        "unsupported network": "AP2_CHAIN_UNSUPPORTED",
        # New header pipeline
        "x-payment-secure": "TRACE_HEADER_INVALID",
        "traceparent": "TRACE_HEADER_INVALID",
        "x-risk-session": "RISK_SESSION_INVALID",
        "x-risk-trace": "RISK_TRACE_INVALID",
        "x-ap2-evidence": "EVIDENCE_HEADER_INVALID",
        "unsupported x-payment-secure version": "TRACE_HEADER_UNSUPPORTED",
        "unsupported x-ap2-evidence version": "EVIDENCE_HEADER_UNSUPPORTED",
        # Risk outcome gating
        "risk deny": "RISK_DENIED",
        "current risk allow": "PAYMENT_AUTHORIZATION_REQUIRED",
        "risk review": "RISK_REVIEW",
        "vi denied": "VI_DENIED",
        "vi review required": "VI_REVIEW_REQUIRED",
        "unsupported x-verifiable-intent version": "VI_HEADER_UNSUPPORTED",
        "x-verifiable-intent": "VI_HEADER_INVALID",
        "verifiable intent required": "VI_EVIDENCE_MISSING",
        "ap2 payment mandate required": "AP2_PAYMENT_MANDATE_MISSING",
        "trace context required": "TRACE_CONTEXT_MISSING",
    }
    for k, v in mapping.items():
        if k in m:
            return v
    return "UNSPECIFIED"


def _error_response(e: HTTPException, req_id: str) -> JSONResponse:
    msg = e.detail if isinstance(e.detail, str) else str(e.detail)
    code = _error_code_from_message(msg)
    return JSONResponse(
        status_code=e.status_code,
        content={"error": {"code": code, "message": msg}, "request_id": req_id},
    )


def _risk_engine_url() -> str:
    return os.getenv("RISK_ENGINE_URL", "http://localhost:8001")


def _fingerprint_json(value: Any) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return f"sha256:{sha256(canonical.encode('utf-8')).hexdigest()}"


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _unique_json(raw: str | bytes) -> Any:
    def object_pairs(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("Duplicate JSON object key")
            obj[key] = value
        return obj

    def invalid_constant(value):
        raise ValueError(f"Invalid JSON constant: {value}")

    return json.loads(raw, object_pairs_hook=object_pairs, parse_constant=invalid_constant)


def _payment_payload_dict(body: ProxyVerifyRequest, request: Request) -> Dict[str, Any]:
    return getattr(request.state, "payment_payload", None) or body.paymentPayload.model_dump(
        by_alias=True, exclude_none=True
    )


async def _public_payment_ingress(body: ProxyVerifyRequest, request: Request) -> None:
    try:
        raw = _unique_json(await request.body())
        payload = raw["paymentPayload"]
        headers = request.headers.getlist("X-PAYMENT")
        if len(headers) > 1:
            raise ValueError("Multiple X-PAYMENT headers")
        if headers:
            decoded = _unique_json(safe_base64_decode(headers[0]))
            if not isinstance(decoded, dict) or _fingerprint_json(decoded) != _fingerprint_json(
                payload
            ):
                raise ValueError("X-PAYMENT and paymentPayload disagree")
        # Compare the fields shared by the signed payment, the outer request and the quote.
        pr = body.paymentRequirements
        if (
            type(payload.get("x402Version")) is not int
            or payload["x402Version"] != body.x402Version
            or body.x402Version != 1
            or payload.get("network") != pr.network
            or payload.get("scheme") != pr.scheme
            or ("version" in payload and payload["version"] != body.x402Version)
            or ("protocol" in payload and payload["protocol"] not in {"x402", pr.scheme})
        ):
            raise ValueError("Payment and requirements disagree")
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail="Invalid or conflicting payment representation"
        ) from exc
    if pr.scheme == "eip3009" or (
        pr.scheme == "exact" and re.fullmatch(r"0x[0-9a-fA-F]{40}", pr.pay_to)
    ):
        enforce_amount_and_asset(body.paymentPayload, pr)
        authorization = payload["payload"]["authorization"]
        if (
            not isinstance(authorization.get("to"), str)
            or authorization["to"].lower() != pr.pay_to.lower()
        ):
            raise HTTPException(
                status_code=422, detail="Authorized recipient differs from payment requirements"
            )
    request.state.payment_payload = payload


def _payment_requirements_dict(
    payment_requirements: PaymentRequirements,
) -> Dict[str, Any]:
    return payment_requirements.model_dump(by_alias=True, exclude_none=True)


def _merchant_id_from_origin(origin: Optional[str], resource: Optional[str]) -> Optional[str]:
    source = origin or resource
    if not source:
        return None
    parsed = urlparse(source)
    host = parsed.netloc or parsed.path
    if not host:
        return None
    return f"did:web:{host.split(':', 1)[0].lower()}"


_VI_POLICY_ALIASES = {
    "require_verifiable_intent": "requireVerifiableIntent",
    "require_verified_intent": "requireVerifiedIntent",
    "require_payment_mandate": "requirePaymentMandate",
    "require_holder_binding": "requireHolderBinding",
    "require_trace": "requireTrace",
    "accepted_issuers": "acceptedIssuers",
    "review_mode": "reviewMode",
}
_VI_POLICY_BOOL_KEYS = {
    "requireVerifiableIntent",
    "requireVerifiedIntent",
    "requirePaymentMandate",
    "requireHolderBinding",
    "requireTrace",
}
_VI_POLICY_REVIEW_RANK = {"allow": 0, "review": 1, "block": 2}


def _canonical_vi_policy_key(key: str) -> str:
    return _VI_POLICY_ALIASES.get(key, key)


def _coerce_policy_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    if value in (0, 1):
        return bool(value)
    return None


def _normalize_policy_issuers(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return []


def _merge_vi_policy_source(
    policy_data: Dict[str, Any],
    candidate: Optional[Dict[str, Any]],
) -> None:
    if not isinstance(candidate, dict):
        return

    for raw_key, value in candidate.items():
        key = _canonical_vi_policy_key(str(raw_key))
        if key in _VI_POLICY_BOOL_KEYS:
            incoming = _coerce_policy_bool(value)
            existing = _coerce_policy_bool(policy_data.get(key, False))
            if incoming is None or existing is None:
                raise HTTPException(status_code=422, detail=f"Invalid VI policy: {key}")
            else:
                policy_data[key] = existing or incoming
            continue

        if key == "reviewMode":
            current = policy_data.get(key)
            incoming = str(value).strip().lower()
            if incoming not in _VI_POLICY_REVIEW_RANK:
                raise HTTPException(status_code=422, detail="Invalid VI policy: reviewMode")
            elif current is None:
                policy_data[key] = incoming
            else:
                current_normalized = str(current).strip().lower()
                if current_normalized not in _VI_POLICY_REVIEW_RANK:
                    policy_data[key] = current
                else:
                    policy_data[key] = max(
                        (current_normalized, incoming),
                        key=lambda item: _VI_POLICY_REVIEW_RANK[item],
                    )
            continue

        if key == "acceptedIssuers":
            existing = _normalize_policy_issuers(policy_data.get(key))
            incoming = _normalize_policy_issuers(value)
            if not existing:
                policy_data[key] = incoming
            elif incoming:
                policy_data[key] = sorted(set(existing) & set(incoming))
                if not policy_data[key]:
                    raise HTTPException(status_code=422, detail="Conflicting VI issuer constraints")
            continue

        if key not in policy_data:
            policy_data[key] = value


def extract_vi_policy(
    payment_requirements: PaymentRequirements,
    *policy_sources: Optional[Dict[str, Any]],
    floor: Optional[Dict[str, Any]] = None,
) -> InternalPolicy:
    extra = getattr(payment_requirements, "extra", {}) or {}
    candidates: List[Optional[Dict[str, Any]]] = [
        extra.get("vi") if isinstance(extra, dict) else None,
        extra.get("verifiableIntent") if isinstance(extra, dict) else None,
        (
            extra.get("x402Secure", {}).get("policy")
            if isinstance(extra, dict) and isinstance(extra.get("x402Secure"), dict)
            else None
        ),
    ]
    policy_data: Dict[str, Any] = {}
    _merge_vi_policy_source(policy_data, floor)
    policy_data.setdefault("reviewMode", "block")
    for candidate in (*candidates, *policy_sources):
        _merge_vi_policy_source(policy_data, candidate)
    try:
        return InternalPolicy(**policy_data)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Invalid VI policy: {e}")


def extract_verifiable_intent_evidence(
    header_value: Optional[str],
    body_value: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    evidence: Dict[str, Any] = {}
    if header_value:
        header_ref = parse_x_verifiable_intent(header_value)
        evidence.update(
            {
                "presentationRef": header_ref["ref"],
                "presentationHash": header_ref["sha256"],
                "metadata": {
                    "referenceHeader": {
                        "mime": header_ref["mt"],
                        "size": int(header_ref["sz"]),
                    }
                },
            }
        )
    if isinstance(body_value, dict):
        metadata = evidence.get("metadata", {})
        body_metadata = body_value.get("metadata")
        if isinstance(body_metadata, dict):
            metadata = {**metadata, **body_metadata}
        evidence.update({k: v for k, v in body_value.items() if k != "metadata"})
        if metadata:
            evidence["metadata"] = metadata
    return evidence or None


def build_public_payment_context(
    body: ProxyVerifyRequest,
    request: Request,
    origin: Optional[str],
) -> Dict[str, Any]:
    payment_payload = _payment_payload_dict(body, request)
    payment_requirements = _payment_requirements_dict(body.paymentRequirements)
    auth = payment_payload.get("payload", {}).get("authorization", {})
    if not isinstance(auth, dict):
        auth = {}

    amount = _first_non_empty(
        auth.get("value"),
        payment_payload.get("value"),
        body.paymentRequirements.max_amount_required,
    )
    destination = _first_non_empty(
        auth.get("to"),
        payment_payload.get("to"),
        body.paymentRequirements.pay_to,
    )
    payer = _extract_payer_from_payment_payload(payment_payload)

    return {
        "protocol": "x402",
        "scheme": payment_payload.get("scheme"),
        "chain": body.paymentRequirements.network,
        "network": body.paymentRequirements.network,
        "asset": body.paymentRequirements.asset,
        "amount": str(amount) if amount is not None else None,
        "currency": payment_requirements.get("extra", {}).get("name")
        if isinstance(payment_requirements.get("extra"), dict)
        else None,
        "destination": destination,
        "payTo": body.paymentRequirements.pay_to,
        "resource": body.paymentRequirements.resource,
        "merchantOrigin": origin,
        "payer": payer,
        "paymentHash": _fingerprint_json(payment_payload),
        "payloadHash": _fingerprint_json(payment_payload),
        "paymentRequirementsHash": _fingerprint_json(payment_requirements),
        "payload": payment_payload,
        "paymentRequirements": payment_requirements,
    }


def build_public_binding(payment_context: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "paymentBound": True,
        "chain": payment_context.get("chain") or payment_context.get("network"),
        "asset": payment_context.get("asset"),
        "amount": payment_context.get("amount"),
        "destination": payment_context.get("destination") or payment_context.get("payTo"),
        "resource": payment_context.get("resource"),
        "violations": [],
        "hashes": {
            "paymentHash": payment_context.get("paymentHash"),
            "paymentPayloadHash": payment_context.get("payloadHash"),
            "paymentRequirementsHash": payment_context.get("paymentRequirementsHash"),
        },
    }


def build_public_vi_assessment_payload(
    body: ProxyVerifyRequest,
    request: Request,
    *,
    vi_header: Optional[str],
    trace_context: Optional[Dict[str, Any]],
    ap2_context: Optional[Dict[str, Any]] = None,
    risk_session: Optional[str],
    risk_trace: Optional[str],
    origin: Optional[str],
) -> Dict[str, Any]:
    policy = getattr(request.state, "vi_policy", None) or extract_vi_policy(
        body.paymentRequirements, body.policy, body.vi_policy
    )
    verifiable_intent = extract_verifiable_intent_evidence(
        vi_header,
        body.verifiable_intent,
    )
    payment_context = build_public_payment_context(body, request, origin)
    trace = trace_context or body.trace_context or {}
    merchant_id = _merchant_id_from_origin(origin, body.paymentRequirements.resource)
    merged_ap2_context = {**(body.ap2_context or {}), **(ap2_context or {})}

    return {
        "agentId": payment_context.get("payer"),
        "walletAddress": payment_context.get("payer"),
        "merchantId": merchant_id,
        "policy": policy.model_dump(by_alias=True),
        "verifiableIntent": verifiable_intent or {},
        "ap2Context": merged_ap2_context,
        "traceContext": trace,
        "paymentContext": payment_context,
        "binding": build_public_binding(payment_context),
        "metadata": {
            "source": "x402_secure_public_gateway",
            "riskSession": risk_session,
            "riskTrace": risk_trace,
            "origin": origin,
        },
    }


def public_vi_assessment_requested(
    policy: InternalPolicy,
    body: ProxyVerifyRequest,
    vi_header: Optional[str],
) -> bool:
    return any(
        [
            policy.require_verifiable_intent,
            policy.require_verified_intent,
            policy.require_payment_mandate,
            policy.require_holder_binding,
            policy.require_trace,
            bool(policy.accepted_issuers),
            bool(body.policy),
            bool(body.vi_policy),
            bool(body.verifiable_intent),
            bool(body.ap2_context),
            bool(vi_header),
        ]
    )


def _has_ap2_payment_mandate(ap2_context: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(ap2_context, dict):
        return False
    return bool(
        _nested_vi_value(
            ap2_context,
            "paymentMandateRef",
            "paymentMandateId",
            "payment_mandate_ref",
            "payment_mandate_id",
            "paymentMandateHash",
            "payment_mandate_hash",
            "paymentUid",
            "payment_uid",
            "payment.id",
            "payment.ref",
            "payment.hash",
            "mandates.payment.id",
            "mandates.payment.ref",
            "mandates.payment.hash",
            "normalized.payment_mandate_id",
            "normalized.paymentMandateId",
            "normalized.payment_mandate_ref",
            "normalized.paymentMandateRef",
            "normalized.payment_mandate_hash",
            "normalized.paymentMandateHash",
        )
    )


def _nested_vi_value(mapping: Dict[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = mapping
        ok = True
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                ok = False
                break
        if ok and current not in (None, ""):
            return current
    return None


def _has_verifiable_intent_evidence(verifiable_intent: Any) -> bool:
    if not isinstance(verifiable_intent, dict) or not verifiable_intent:
        return False
    for key in (
        "presentation",
        "presentationRef",
        "presentationHash",
        "evidenceRef",
        "evidenceHash",
        "credentialId",
    ):
        if verifiable_intent.get(key):
            return True
    layers = verifiable_intent.get("layers")
    if isinstance(layers, list):
        return any(_has_verifiable_intent_evidence(layer) for layer in layers)
    return False


def enforce_public_vi_policy_inputs(payload: Dict[str, Any]) -> None:
    policy = InternalPolicy(**(payload.get("policy") or {}))
    verifiable_intent = payload.get("verifiableIntent")
    ap2_context = payload.get("ap2Context")
    trace_context = payload.get("traceContext")

    vi_required = any(
        [
            policy.require_verifiable_intent,
            policy.require_verified_intent,
            policy.require_holder_binding,
            bool(policy.accepted_issuers),
        ]
    )
    if vi_required and not _has_verifiable_intent_evidence(verifiable_intent):
        raise HTTPException(status_code=422, detail="Verifiable Intent required")
    if policy.require_payment_mandate and not _has_ap2_payment_mandate(ap2_context):
        raise HTTPException(status_code=422, detail="AP2 payment mandate required")
    if policy.require_trace and not trace_context:
        raise HTTPException(status_code=422, detail="Trace context required")


def _normalize_vi_decision(value: Any) -> str:
    normalized = str(value or "").lower()
    if normalized in {"approve", "approved", "accept", "accepted"}:
        return "allow"
    if normalized in {"reject", "rejected", "decline", "declined", "block", "blocked"}:
        return "deny"
    if normalized not in {"allow", "deny", "review"}:
        return "deny"
    return normalized


def _ap2_context_from_mandate_header(mandate: Optional[Dict[str, str]]) -> Dict[str, Any]:
    if not mandate:
        return {}
    return {
        "paymentMandateRef": mandate["mr"],
        "paymentMandateHash": mandate["ms"],
        "paymentMandateMime": mandate["mt"],
        "paymentMandateSize": int(mandate["sz"]),
    }


def effective_public_vi_decision(
    trustline_decision: Any,
    policy: InternalPolicy,
    warnings: List[str],
) -> str:
    normalized = _normalize_vi_decision(trustline_decision)
    if normalized != "review":
        return normalized
    if policy.review_mode == "allow":
        warnings.append("Trustline returned review; policy reviewMode=allow converted it to allow.")
        return "allow"
    if policy.review_mode == "block":
        warnings.append("Trustline returned review; policy reviewMode=block converted it to deny.")
        return "deny"
    return "review"


def enforce_required_verified_vi_result(
    decision: str,
    policy: InternalPolicy,
    vi_result: Dict[str, Any],
    reasons: List[str],
    warnings: List[str],
) -> str:
    if not policy.require_verified_intent or vi_result.get("verified") is True:
        return decision
    reason = "Verified Verifiable Intent required"
    if reason not in reasons:
        reasons.append(reason)
    warnings.append("Policy requireVerifiedIntent denied an unverified Trustline VI result.")
    return "deny"


async def assess_public_verifiable_intent(
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    enforce_public_vi_policy_inputs(payload)
    trustline_response = await post_trustline_validation("assess-verifiable-intent", payload)
    warnings = list(trustline_response.get("warnings") or [])
    policy = InternalPolicy(**(payload.get("policy") or {}))
    raw_vi_result = trustline_response.get("vi")
    vi_result = raw_vi_result if isinstance(raw_vi_result, dict) else {}
    reasons = list(trustline_response.get("reasons") or [])
    decision = effective_public_vi_decision(
        trustline_response.get("decision"),
        policy,
        warnings,
    )
    decision = enforce_required_verified_vi_result(
        decision,
        policy,
        vi_result,
        reasons,
        warnings,
    )
    return {
        "decision": decision,
        "decision_id": trustline_response.get("decision_id")
        or trustline_response.get("decisionId")
        or f"xs_vi_{uuid.uuid4().hex}",
        "risk_level": trustline_response.get("risk_level", "medium"),
        "ttl_seconds": trustline_response.get("ttl_seconds", 300),
        "vi": vi_result,
        "binding": trustline_response.get("binding") or payload.get("binding") or {},
        "reasons": reasons,
        "warnings": warnings,
        "trustline_assessment": trustline_response.get("trustline_assessment") or {},
    }


async def post_public_vi_receipt(payload: Dict[str, Any]) -> Dict[str, Any]:
    return await post_trustline_validation("verifiable-intent-receipt", payload)


def build_public_vi_receipt_payload(
    body: ProxySettleRequest,
    request: Request,
    *,
    decision_id: str,
    evidence_ref: Optional[str],
    settlement_attempt_id: Optional[str],
    idempotency_key: Optional[str],
    settle_response: Dict[str, Any],
    origin: Optional[str],
) -> Dict[str, Any]:
    payment_context = build_public_payment_context(body, request, origin)
    settlement_id = settlement_attempt_id or f"xs_settle_{uuid.uuid4().hex}"
    transaction = (
        settle_response.get("transaction")
        or settle_response.get("txHash")
        or settle_response.get("transactionHash")
    )
    settlement_status = "success" if settle_response.get("success") else "failed"
    return {
        "decisionId": decision_id,
        "evidenceRef": evidence_ref,
        "settlementAttemptId": settlement_id,
        "idempotencyKey": idempotency_key or settlement_id,
        "payment": {
            **payment_context,
            "status": settlement_status,
            "settlementStatus": settlement_status,
            "transaction": transaction,
            "network": settle_response.get("network") or payment_context.get("network"),
            "errorReason": settle_response.get("errorReason") or settle_response.get("error"),
        },
        "metadata": {
            "source": "x402_secure_public_gateway",
            "origin": origin,
        },
    }


def _proxy_local_risk_enabled() -> bool:
    """Check if proxy should use local risk storage instead of forwarding."""
    return os.getenv("PROXY_LOCAL_RISK", "0").lower() in {"1", "true", "yes"}


_AUTHORIZATION_HEADER = "X-PAYMENT-AUTHORIZATION"


def _authorization_key(cfg: ProxyRuntimeConfig) -> bytes:
    key = (cfg.authorization_key or "").encode()
    if len(key) < 32:
        raise HTTPException(status_code=503, detail="Invalid payment authorization configuration")
    return key


def _issue_authorization(cfg: ProxyRuntimeConfig, claims: dict) -> str:
    data = base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode()).decode()
    signature = hmac.digest(_authorization_key(cfg), data.encode(), "sha256").hex()
    return f"{data}.{signature}"


def _read_authorization(cfg: ProxyRuntimeConfig, token: Optional[str], binding: str) -> dict:
    if token and len(token) <= 4096:
        data, _, signature = token.partition(".")
        expected = hmac.digest(_authorization_key(cfg), data.encode(), "sha256").hex()
        if signature.isascii() and hmac.compare_digest(expected, signature):
            claims = json.loads(base64.urlsafe_b64decode(data))
            if claims["binding"] == binding and _now_s() < claims["exp"]:
                return claims
    raise HTTPException(status_code=403, detail="Current risk allow for this payment required")


def _upstream_request(body: ProxyVerifyRequest, request: Request) -> Dict[str, Any]:
    payload = _payment_payload_dict(body, request)
    requirements = _payment_requirements_dict(body.paymentRequirements)
    # The public v1 facilitator adapter forwards EIP-3009 token metadata in extra.
    if "extra" in requirements:
        requirements["extra"] = {
            key: value for key, value in requirements["extra"].items() if key in {"name", "version"}
        }
    return {
        "x402Version": body.x402Version,
        "paymentPayload": payload,
        "paymentRequirements": requirements,
        "paymentHeader": request.headers.get("X-PAYMENT")
        or safe_base64_encode(json.dumps(payload, separators=(",", ":"))),
    }


def _same_host_transport(request: Request, url: str) -> Optional[ASGITransport]:
    if urlparse(url).netloc == request.url.netloc:
        return ASGITransport(app=request.app)
    return None


async def _public_gate_inputs(body: ProxyVerifyRequest, request: Request, cfg: ProxyRuntimeConfig):
    _authorization_key(cfg)
    await _public_payment_ingress(body, request)
    origin = request.headers.get("Origin")
    sid, tid = parse_risk_ids(
        request.headers.get("X-RISK-SESSION"), request.headers.get("X-RISK-TRACE")
    )
    tc = parse_x_payment_secure(request.headers.get("X-PAYMENT-SECURE") or "")
    if tid is None and tc.get("ts"):
        try:
            decoded = json.loads(base64.b64decode(unquote(tc["ts"])))
            tid = decoded.get("tid") if isinstance(decoded, dict) else None
        except (TypeError, ValueError):
            pass
        if tid:
            _, tid = parse_risk_ids(sid, tid)
    ap2_header = request.headers.get("X-AP2-EVIDENCE")
    mandate = (
        parse_x_ap2_evidence(ap2_header)
        if ap2_header and ap2_header.startswith("evd.v1;")
        else None
    )
    signed_ap2 = None if mandate else ap2_header
    try:
        floor_ap2 = _merge_ap2_policies([cfg.merchant_policy.get("ap2", {})])
        floor_vi = cfg.merchant_policy.get("vi", {})
        allow_mandate_reference = cfg.merchant_policy.get("allowAp2MandateReference", False)
        if (
            cfg.merchant_policy.keys() - {"ap2", "vi", "allowAp2MandateReference"}
            or not isinstance(floor_vi, dict)
            or type(allow_mandate_reference) is not bool
        ):
            raise ValueError("Invalid public merchant policy")
    except (ValueError, HTTPException) as exc:
        raise HTTPException(status_code=503, detail="Invalid public merchant policy") from exc
    request.state.vi_policy = policy = extract_vi_policy(
        body.paymentRequirements, body.policy, body.vi_policy, floor=floor_vi
    )
    if policy.require_payment_mandate and not allow_mandate_reference:
        floor_ap2.requirePaymentMandate = True
    if policy.require_trace and not tid:
        raise HTTPException(status_code=422, detail="Trace context required")
    evidence = verify_ap2(request, body, origin, signed_ap2, required_policy=floor_ap2)
    context = build_public_payment_context(body, request, origin)
    vi_header = request.headers.get("X-VERIFIABLE-INTENT")
    trace_context = {
        "traceparent": tc["tp"],
        **({"tracestate": tc["ts"]} if "ts" in tc else {}),
        **({"riskTrace": tid} if tid else {}),
        "riskSession": sid,
    }
    ap2_context = _ap2_context_from_mandate_header(mandate)
    if evidence and evidence.sig and evidence.payment_uid:
        ap2_context["paymentMandateId"] = evidence.payment_uid
    vi_payload = None
    if public_vi_assessment_requested(policy, body, vi_header):
        vi_payload = build_public_vi_assessment_payload(
            body,
            request,
            vi_header=vi_header,
            trace_context=trace_context,
            ap2_context=ap2_context,
            risk_session=sid,
            risk_trace=tid,
            origin=origin,
        )
        enforce_public_vi_policy_inputs(vi_payload)
    evaluate_json = {
        "sid": sid,
        "tid": tid,
        "trace_context": tc,
        "payment": {
            "protocol": context["scheme"],
            "version": body.x402Version,
            "network": context["network"],
            "payload": context["payload"]["payload"],
            "extra": {
                "paymentContext": {key: value for key, value in context.items() if key != "payload"}
            },
        },
    }
    if mandate:
        evaluate_json["mandate"] = {
            "ref": mandate["mr"],
            "sha256_b64url": mandate["ms"],
            "mime": mandate["mt"],
            "size": int(mandate["sz"]),
        }
    binding = _fingerprint_json(
        {
            "risk": evaluate_json,
            "vi": vi_payload,
            "policy": cfg.merchant_policy,
            "ap2Evidence": evidence.model_dump() if evidence else None,
            "upstream": [cfg.upstream_verify_url, cfg.upstream_settle_url],
        }
    )
    return binding, evaluate_json, vi_payload


async def _evaluate_public_risk(
    request: Request, cfg: ProxyRuntimeConfig, payload: dict
) -> EvaluateResponse:
    if _proxy_local_risk_enabled():
        url = "http://proxy-local"
        transport = ASGITransport(app=request.app)
    else:
        url = _risk_engine_url()
        transport = _same_host_transport(request, url)
    token = os.getenv("RISK_INTERNAL_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(timeout=cfg.timeout_s, transport=transport) as client:
        result = await client.post(f"{url}/risk/evaluate", json=payload, headers=headers)
    if result.status_code != 200:
        raise HTTPException(status_code=result.status_code, detail="Risk evaluation failed")
    try:
        risk = EvaluateResponse.model_validate(result.json())
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Invalid risk response") from exc
    if risk.decision != Decision.allow:
        raise HTTPException(status_code=403, detail=f"Risk {risk.decision.value}")
    if risk.ttl_seconds <= 0:
        raise HTTPException(status_code=403, detail="Risk allow has expired")
    return risk


async def _forward_public_payment(
    body: ProxyVerifyRequest, request: Request, cfg: ProxyRuntimeConfig, url: str
) -> dict:
    transport = _same_host_transport(request, url)
    async with httpx.AsyncClient(timeout=cfg.timeout_s, transport=transport) as client:
        result = await client.post(url, json=_upstream_request(body, request))
    if result.status_code != 200:
        raise HTTPException(
            status_code=result.status_code, detail="Upstream payment request failed"
        )
    try:
        outcome = result.json()
        if isinstance(outcome, dict):
            return outcome
    except ValueError:
        pass
    raise HTTPException(status_code=502, detail="Invalid upstream payment response")


def _public_decision_headers(response: Response, claims: dict) -> None:
    response.headers["X-Risk-Decision"] = "allow"
    response.headers["X-Risk-Decision-ID"] = claims.get("vi_id") or claims["risk_id"]
    response.headers["X-Risk-TTL-Seconds"] = str(max(0, claims["exp"] - _now_s()))
    if claims.get("vi_id"):
        response.headers["X-VI-Decision"] = "allow"
        response.headers["X-VI-Decision-ID"] = claims["vi_id"]
        if claims.get("vi_verified") is not None:
            response.headers["X-VI-Verified"] = str(claims["vi_verified"]).lower()
        if claims.get("evidence_ref"):
            response.headers["X-VI-Evidence-Ref"] = claims["evidence_ref"]


@router.post("/verify", response_model=VerifyResponse)
async def proxy_verify(
    body: ProxyVerifyRequest,
    request: Request,
    response: Response,
    cfg: ProxyRuntimeConfig = Depends(get_proxy_cfg),
):
    req_id = uuid.uuid4().hex
    response.headers["X-Request-ID"] = req_id
    try:
        binding, evaluate_json, vi_payload = await _public_gate_inputs(body, request, cfg)
        started = _now_s()
        risk = await _evaluate_public_risk(request, cfg, evaluate_json)
        claims = {
            "binding": binding,
            "risk_id": risk.decision_id,
            "exp": started + risk.ttl_seconds,
        }
        if vi_payload is not None:
            started = _now_s()
            assessment = await assess_public_verifiable_intent(vi_payload)
            if assessment["decision"] == "deny":
                raise HTTPException(status_code=403, detail="VI denied")
            if assessment["decision"] != "allow":
                raise HTTPException(status_code=403, detail="VI review required")
            details = assessment["vi"]
            ttl = assessment["ttl_seconds"]
            if type(ttl) is not int or ttl <= 0:
                raise HTTPException(status_code=502, detail="Invalid VI authorization TTL")
            claims.update(
                exp=min(claims["exp"], started + ttl),
                vi_id=assessment["decision_id"],
                vi_verified=details.get("verified") is True if "verified" in details else None,
                evidence_ref=details.get("evidence_ref") or details.get("evidenceRef"),
            )
        data = await _forward_public_payment(body, request, cfg, cfg.upstream_verify_url)
        if type(data.get("isValid")) is not bool:
            raise HTTPException(status_code=502, detail="Invalid upstream verification outcome")
        if data["isValid"]:
            if _now_s() >= claims["exp"]:
                raise HTTPException(status_code=403, detail="Payment authorization expired")
            response.headers[_AUTHORIZATION_HEADER] = _issue_authorization(cfg, claims)
            _public_decision_headers(response, claims)
        return VerifyResponse(
            isValid=data["isValid"],
            payer=str(data.get("payer") or ""),
            invalidReason=data.get("invalidReason") or data.get("error"),
        )
    except HTTPException as exc:
        return _error_response(exc, req_id)
    except HeaderError as exc:
        return _error_response(HTTPException(status_code=400, detail=str(exc)), req_id)
    except httpx.HTTPError:
        return _error_response(
            HTTPException(status_code=502, detail="Payment verification unavailable"), req_id
        )


@router.post("/settle", response_model=SettleResponse)
async def proxy_settle(
    body: ProxySettleRequest,
    request: Request,
    response: Response,
    cfg: ProxyRuntimeConfig = Depends(get_proxy_cfg),
):
    req_id = uuid.uuid4().hex
    response.headers["X-Request-ID"] = req_id
    try:
        binding, evaluate_json, _ = await _public_gate_inputs(body, request, cfg)
        token = request.headers.get(_AUTHORIZATION_HEADER)
        claims = _read_authorization(cfg, token, binding)
        if cfg.settle_risk_enabled:
            await _evaluate_public_risk(request, cfg, evaluate_json)
            claims = _read_authorization(cfg, token, binding)
        data = await _forward_public_payment(body, request, cfg, cfg.upstream_settle_url)
        if type(data.get("success")) is not bool:
            raise HTTPException(status_code=502, detail="Invalid upstream settlement outcome")
        status = data.get("status")
        if status is not None and (
            not isinstance(status, str)
            or status not in ({"success", "settled"} if data["success"] else {"failed", "declined"})
        ):
            raise HTTPException(status_code=502, detail="Upstream settlement outcome is unknown")
        refs = [
            data[key]
            for key in ("transaction", "txHash", "transactionHash")
            if data.get(key) is not None
        ]
        if (
            any(not isinstance(ref, str) or not ref.strip() for ref in refs)
            or len(set(refs)) > 1
            or (data["success"] and (not refs or data.get("errorReason") or data.get("error")))
            or (
                data.get("network") is not None
                and data["network"] != body.paymentRequirements.network
            )
        ):
            raise HTTPException(status_code=502, detail="Invalid upstream settlement binding")
        transaction = refs[0] if refs else None
    except HTTPException as exc:
        return _error_response(exc, req_id)
    except HeaderError as exc:
        return _error_response(HTTPException(status_code=400, detail=str(exc)), req_id)
    except httpx.HTTPError:
        return _error_response(
            HTTPException(status_code=502, detail="Upstream settlement outcome is unknown"), req_id
        )

    _public_decision_headers(response, claims)
    if claims.get("vi_id"):
        receipt_payload = build_public_vi_receipt_payload(
            body,
            request,
            decision_id=claims["vi_id"],
            evidence_ref=claims.get("evidence_ref"),
            settlement_attempt_id=request.headers.get("X-SETTLEMENT-ATTEMPT-ID"),
            idempotency_key=request.headers.get("X-IDEMPOTENCY-KEY"),
            settle_response=data,
            origin=request.headers.get("Origin"),
        )
        try:
            receipt = await post_public_vi_receipt(receipt_payload)
            response.headers["X-VI-Receipt-Status"] = "recorded"
            receipt_id = receipt.get("receipt_id") or receipt.get("receiptId")
            if receipt_id:
                response.headers["X-VI-Receipt-ID"] = str(receipt_id)
        except Exception:
            logger.warning("[%s] VI receipt post failed", req_id, exc_info=True)
            response.headers["X-VI-Receipt-Status"] = "failed"
    return SettleResponse(
        success=data["success"],
        payer=str(data.get("payer") or ""),
        transaction=transaction,
        network=data.get("network"),
        errorReason=data.get("errorReason") or data.get("error"),
    )


# Optional utility for seller-side middleware that may be added in a later step.
class TokenAmount(NamedTuple):
    amount: int
