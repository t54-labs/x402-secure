# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import base64
import json
import json as _json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Dict, List, NamedTuple, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from httpx import ASGITransport
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .headers import (
    HeaderError,
    parse_risk_ids,
    parse_x_ap2_evidence,
    parse_x_payment_secure,
    parse_x_verifiable_intent,
)
from .internal_facilitator import InternalPolicy, post_trustline_validation
from .risk_routes import Decision, EvaluateResponse

# Optional deps
try:  # pragma: no cover - optional dependency path
    from eth_utils import keccak  # type: ignore

    HAVE_KECCAK = True
except Exception:  # pragma: no cover - optional dependency path
    HAVE_KECCAK = False

try:  # pragma: no cover - optional dependency path
    from eth_account import Account  # type: ignore
    from eth_account.messages import encode_structured_data  # type: ignore

    HAVE_EIP712 = True
except Exception:  # pragma: no cover - optional dependency path
    HAVE_EIP712 = False

# x402 SDK types/utilities
from x402.encoding import safe_base64_decode, safe_base64_encode
from x402.types import PaymentPayload, PaymentRequirements

logger = logging.getLogger(__name__)

# Debug: store last upstream interactions
LAST_VERIFY: Optional[Dict[str, Any]] = None
LAST_SETTLE: Optional[Dict[str, Any]] = None


# -------------------------------
# Helpers
# -------------------------------


def _now_s() -> int:
    return int(time.time())


def _parse_iso8601(ts: str) -> int:
    return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())


def _b32(hexstr: str) -> bytes:
    h = hexstr[2:] if hexstr.startswith("0x") else hexstr
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


def extract_ap2_policy(payment_requirements: PaymentRequirements) -> AP2Policy:
    extra = getattr(payment_requirements, "extra", {}) or {}
    ap2 = extra.get("ap2") or extra.get("ap2-evidence") or {}
    try:
        return AP2Policy(**ap2)
    except ValidationError as e:  # pragma: no cover - input validation
        raise HTTPException(
            status_code=422,
            detail=f"Invalid AP2 policy in paymentRequirements.extra: {e}",
        )


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
    if policy.requireIntentMandate and not ev.intent_uid:
        raise HTTPException(status_code=422, detail="AP2: intent_uid required")
    if policy.requireCartMandate and not ev.cart_uid:
        raise HTTPException(status_code=422, detail="AP2: cart_uid required")
    if policy.requirePaymentMandate and not ev.payment_uid:
        raise HTTPException(status_code=422, detail="AP2: payment_uid required")
    if policy.requireTrace and not ev.trace_uid:
        raise HTTPException(status_code=422, detail="AP2: trace_uid required")


def verify_origin_binding(
    ev: AP2Evidence, payment_requirements: PaymentRequirements, origin_header: Optional[str]
) -> None:
    if origin_header:
        origin = origin_header
    else:
        parsed = urlparse(payment_requirements.resource)
        origin = f"{parsed.scheme or 'https'}://{parsed.netloc}"
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
    expected = compute_expected_payment_hash(req, payload_obj)
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
        enc = encode_structured_data(typed)  # type: ignore
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
    # Enforce amount <= maxAmountRequired when provided
    pr = payment_requirements
    pp = payment_payload.model_dump(by_alias=True)
    value: Optional[int] = None
    try:
        value = int(pp.get("payload", {}).get("authorization", {}).get("value"))
    except Exception:
        pass
    if value is not None and pr.max_amount_required is not None:
        try:
            max_amt = int(pr.max_amount_required)
            if value > max_amt:
                raise HTTPException(status_code=422, detail="Amount exceeds maxAmountRequired")
        except Exception:
            # If merchant omitted max or non-integer, skip
            pass
    # Asset address congruence is enforced against AP2 evidence; payload may not include it.


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
) -> None:
    policy = extract_ap2_policy(body.paymentRequirements)
    ev = parse_ap2_evidence_b64(ap2_header, body.ap2EvidenceHeader)
    enforce_policy_flags(policy, ev)
    verify_congruence(ev, body.paymentRequirements)
    verify_ttl(ev)
    verify_origin_binding(ev, body.paymentRequirements, origin_header)
    verify_payment_hash_binding(ev, request, body.paymentPayload)
    verify_merchant_identity(policy, body.paymentRequirements)
    payer = _extract_payer_from_payment_payload(body.paymentPayload.model_dump(by_alias=True))
    verify_eip712_signature_if_present(ev, body.paymentRequirements, payer)
    enforce_amount_and_asset(body.paymentPayload, body.paymentRequirements)


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
    debug_enabled: bool = Field(
        default_factory=lambda: (
            os.getenv("PROXY_DEBUG_ENABLED", "1").lower() in {"1", "true", "yes"}
        )
    )
    # Feature flag: optionally enforce risk evaluate at settle (default OFF)
    # TODO(later): Consider enabling by default once prod readiness is confirmed.
    settle_risk_enabled: bool = Field(
        default_factory=lambda: (
            os.getenv("PROXY_SETTLE_RISK_ENABLED", "0").lower() in {"1", "true", "yes"}
        )
    )


def get_proxy_cfg() -> ProxyRuntimeConfig:
    return ProxyRuntimeConfig()


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
        "risk denied": "RISK_DENIED",
        "risk review": "RISK_REVIEW",
        "unsupported x-verifiable-intent version": "VI_HEADER_UNSUPPORTED",
        "x-verifiable-intent": "VI_HEADER_INVALID",
        "verifiable intent required": "VI_EVIDENCE_MISSING",
        "ap2 payment mandate required": "AP2_PAYMENT_MANDATE_MISSING",
        "trace context required": "TRACE_CONTEXT_MISSING",
        "vi denied": "VI_DENIED",
        "vi review required": "VI_REVIEW_REQUIRED",
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


def _payment_payload_dict(body: ProxyVerifyRequest, request: Request) -> Dict[str, Any]:
    x_payment = request.headers.get("X-PAYMENT")
    if x_payment:
        try:
            decoded = json.loads(safe_base64_decode(x_payment))
            if isinstance(decoded, dict):
                return decoded
        except Exception:
            pass
    return body.paymentPayload.model_dump(by_alias=True, exclude_none=True)


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


def extract_vi_policy(
    payment_requirements: PaymentRequirements,
    *policy_sources: Optional[Dict[str, Any]],
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
        *policy_sources,
    ]
    policy_data: Dict[str, Any] = {}
    for candidate in candidates:
        if isinstance(candidate, dict):
            policy_data.update(candidate)
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
        "protocol": _first_non_empty(payment_payload.get("scheme"), "x402"),
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
    policy = extract_vi_policy(body.paymentRequirements, body.policy, body.vi_policy)
    verifiable_intent = extract_verifiable_intent_evidence(
        vi_header,
        body.verifiable_intent,
    )
    payment_context = build_public_payment_context(body, request, origin)
    trace = trace_context or body.trace_context or {}
    merchant_id = _merchant_id_from_origin(origin, body.paymentRequirements.resource)
    merged_ap2_context = {**(ap2_context or {}), **(body.ap2_context or {})}

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
    return any(
        ap2_context.get(key)
        for key in (
            "paymentMandateRef",
            "payment_mandate_ref",
            "paymentMandateHash",
            "payment_mandate_hash",
            "paymentUid",
            "payment_uid",
        )
    )


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
    if vi_required and not verifiable_intent:
        raise HTTPException(status_code=422, detail="Verifiable Intent required")
    if policy.require_payment_mandate and not _has_ap2_payment_mandate(ap2_context):
        raise HTTPException(status_code=422, detail="AP2 payment mandate required")
    if policy.require_trace and not trace_context:
        raise HTTPException(status_code=422, detail="Trace context required")


def _normalize_vi_decision(value: Any) -> str:
    normalized = str(value or "review").lower()
    if normalized in {"approve", "approved", "accept", "accepted"}:
        return "allow"
    if normalized in {"reject", "rejected", "decline", "declined", "block", "blocked"}:
        return "deny"
    if normalized not in {"allow", "deny", "review"}:
        return "review"
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


async def assess_public_verifiable_intent(
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    enforce_public_vi_policy_inputs(payload)
    trustline_response = await post_trustline_validation("assess-verifiable-intent", payload)
    warnings = list(trustline_response.get("warnings") or [])
    policy = InternalPolicy(**(payload.get("policy") or {}))
    decision = effective_public_vi_decision(
        trustline_response.get("decision"),
        policy,
        warnings,
    )
    return {
        "decision": decision,
        "decision_id": trustline_response.get("decision_id")
        or trustline_response.get("decisionId")
        or f"xs_vi_{uuid.uuid4().hex}",
        "risk_level": trustline_response.get("risk_level", "medium"),
        "ttl_seconds": trustline_response.get("ttl_seconds", 300),
        "vi": trustline_response.get("vi") or {},
        "binding": trustline_response.get("binding") or payload.get("binding") or {},
        "reasons": trustline_response.get("reasons") or [],
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
    return {
        "decisionId": decision_id,
        "evidenceRef": evidence_ref,
        "settlementAttemptId": settlement_id,
        "idempotencyKey": idempotency_key or settlement_id,
        "payment": {
            **payment_context,
            "settlementStatus": "success" if settle_response.get("success") else "failed",
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


@router.post("/verify", response_model=VerifyResponse)
async def proxy_verify(
    body: ProxyVerifyRequest,
    request: Request,
    x_ap2_evidence: Optional[str] = Header(None, alias="X-AP2-EVIDENCE"),
    x_verifiable_intent: Optional[str] = Header(None, alias="X-VERIFIABLE-INTENT"),
    x_payment_secure: Optional[str] = Header(None, alias="X-PAYMENT-SECURE"),
    x_risk_session: Optional[str] = Header(None, alias="X-RISK-SESSION"),
    x_risk_trace: Optional[str] = Header(None, alias="X-RISK-TRACE"),
    origin: Optional[str] = Header(None, alias="Origin"),
    cfg: ProxyRuntimeConfig = Depends(get_proxy_cfg),
    response: Response = None,
):
    req_id = uuid.uuid4().hex
    if response is not None:
        response.headers["X-Request-ID"] = req_id
    try:
        # Parse required IDs and trace context; optional mandate
        sid, tid = parse_risk_ids(x_risk_session, x_risk_trace)
        if not x_payment_secure:
            raise HeaderError("X-PAYMENT-SECURE required")
        tc = parse_x_payment_secure(x_payment_secure)
        mandate = None
        if x_ap2_evidence:
            mandate = parse_x_ap2_evidence(x_ap2_evidence)

        # Extract tid from tracestate if present
        extracted_tid = tid  # from X-RISK-TRACE header (if exists)
        if not extracted_tid and "ts" in tc:
            try:
                # Decode tracestate to extract tid
                from urllib.parse import unquote

                ts_decoded = unquote(tc["ts"])
                ts_json = json.loads(base64.b64decode(ts_decoded))
                extracted_tid = ts_json.get("tid")
                if extracted_tid:
                    logger.info(
                        f"[{req_id}] [PROXY] Extracted tid from tracestate: {extracted_tid}"
                    )
            except Exception as e:
                logger.debug(f"[{req_id}] Could not extract tid from tracestate: {e}")

        # Construct payment context from paymentPayload
        payment_payload_dict = body.paymentPayload.model_dump(by_alias=True)
        xpay = request.headers.get("X-PAYMENT")
        if xpay:
            try:
                # Decode X-PAYMENT to preserve all fields
                payment_payload_dict = json.loads(safe_base64_decode(xpay))
            except Exception:
                pass  # Fall back to Pydantic serialization

        payment_context = {
            "protocol": payment_payload_dict.get("protocol") or payment_payload_dict.get("scheme"),
            "version": payment_payload_dict.get("version")
            or payment_payload_dict.get("x402Version"),
            "network": payment_payload_dict.get("network"),
            "payload": payment_payload_dict.get("payload", {}),
        }

        evaluate_json: Dict[str, Any] = {
            "sid": sid,
            **({"tid": extracted_tid} if extracted_tid else {}),
            "trace_context": {"tp": tc["tp"], **({"ts": tc["ts"]} if "ts" in tc else {})},
            "payment": payment_context,
        }
        if mandate:
            evaluate_json["mandate"] = {
                "ref": mandate["mr"],
                "sha256_b64url": mandate["ms"],
                "mime": mandate["mt"],
                "size": int(mandate["sz"]),
            }
        # In local mode, use proxy's own /risk/evaluate endpoint (same app)
        if _proxy_local_risk_enabled():
            # Use in-process ASGI transport to call local /risk/evaluate
            rurl = "http://proxy-local"  # Placeholder URL for ASGI transport
            risk_transport = ASGITransport(app=request.app)
            logger.info(f"[{req_id}] [PROXY] ▶ Local /risk/evaluate (in-process)")
        else:
            rurl = _risk_engine_url()
            logger.info(f"[{req_id}] [PROXY] ▶ Risks /risk/evaluate {rurl}")
            # Use in-app transport for local/testing when targeting same host
            risk_transport = None
            try:
                if urlparse(rurl).netloc == urlparse(str(request.url)).netloc:
                    risk_transport = ASGITransport(app=request.app)
            except Exception:
                risk_transport = None
        # Attach internal Bearer token when configured
        headers = {}
        token = os.getenv("RISK_INTERNAL_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=cfg.timeout_s, transport=risk_transport) as client:
            r = await client.post(f"{rurl}/risk/evaluate", json=evaluate_json, headers=headers)
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=r.text)

        # Validate and parse risk response
        try:
            risk_response = EvaluateResponse(**r.json())
            logger.info(f"[{req_id}] [PROXY] Risk response: {risk_response.model_dump_json()}")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Invalid risk response: {str(e)}")

        decision = risk_response.decision
        if response is not None:
            response.headers["X-Risk-Decision"] = decision.value
        if risk_response.decision_id:
            response.headers["X-Risk-Decision-ID"] = risk_response.decision_id
        if risk_response.ttl_seconds:
            response.headers["X-Risk-TTL-Seconds"] = str(risk_response.ttl_seconds)
        logger.info(
            "[%s] [PROXY] ✅ Risk decision: %s (id=%s)",
            req_id,
            decision.value,
            risk_response.decision_id,
        )

        # Gate forwarding on decision
        if decision == Decision.deny:
            msg = "Risk denied" + (
                f": {', '.join(risk_response.reasons)}" if risk_response.reasons else ""
            )
            raise HTTPException(status_code=403, detail=msg)

        trace_context = {
            "traceparent": tc["tp"],
            **({"tracestate": tc["ts"]} if "ts" in tc else {}),
            **({"riskTrace": extracted_tid} if extracted_tid else {}),
            "riskSession": sid,
        }
        vi_policy = extract_vi_policy(body.paymentRequirements, body.policy, body.vi_policy)
        if public_vi_assessment_requested(vi_policy, body, x_verifiable_intent):
            vi_payload = build_public_vi_assessment_payload(
                body,
                request,
                vi_header=x_verifiable_intent,
                trace_context=trace_context,
                ap2_context=_ap2_context_from_mandate_header(mandate),
                risk_session=sid,
                risk_trace=extracted_tid,
                origin=origin,
            )
            vi_assessment = await assess_public_verifiable_intent(vi_payload)
            vi_decision = vi_assessment["decision"]
            if response is not None:
                response.headers["X-VI-Decision"] = vi_decision
                response.headers["X-Risk-Decision"] = vi_decision
                response.headers["X-VI-Decision-ID"] = vi_assessment["decision_id"]
                response.headers["X-Risk-Decision-ID"] = vi_assessment["decision_id"]
                vi_details = vi_assessment.get("vi") or {}
                if "verified" in vi_details:
                    response.headers["X-VI-Verified"] = str(bool(vi_details["verified"])).lower()
                evidence_ref = vi_details.get("evidence_ref") or vi_details.get("evidenceRef")
                if evidence_ref:
                    response.headers["X-VI-Evidence-Ref"] = str(evidence_ref)
            if vi_decision == "deny":
                msg = "VI denied" + (
                    f": {', '.join(vi_assessment['reasons'])}"
                    if vi_assessment.get("reasons")
                    else ""
                )
                raise HTTPException(status_code=403, detail=msg)
            if vi_decision == "review":
                raise HTTPException(status_code=403, detail="VI review required")
    except HTTPException as e:
        return _error_response(e, req_id)
    except HeaderError as e:
        logger.error(f"[{req_id}] [PROXY] Header validation failed (verify): {e}")
        return _error_response(HTTPException(status_code=400, detail=str(e)), req_id)

    # Use in-app transport for test/local stubs when targeting same host
    transport = None
    try:
        if urlparse(cfg.upstream_verify_url).netloc == urlparse(str(request.url)).netloc:
            transport = ASGITransport(app=request.app)
    except Exception:
        transport = None
    # Build request compatible with both facilitator shapes (paymentPayload or paymentHeader)
    xpay = request.headers.get("X-PAYMENT")

    # Preserve original payment payload fields (including ap2Ref) by decoding X-PAYMENT directly
    # instead of using Pydantic model_dump which may strip extra fields
    payment_payload_dict = body.paymentPayload.model_dump(by_alias=True)
    if xpay:
        try:
            # Decode X-PAYMENT to preserve all fields including ap2Ref
            payment_payload_dict = json.loads(safe_base64_decode(xpay))
        except Exception:
            pass  # Fall back to Pydantic serialization

    # Strip custom AP2 fields from PaymentRequirements before forwarding to upstream
    # The proxy has already validated AP2, so upstream (e.g., CDP) only needs standard x402 fields
    payment_requirements_dict = body.paymentRequirements.model_dump(
        by_alias=True, exclude_none=True
    )
    if "extra" in payment_requirements_dict:
        extra = payment_requirements_dict["extra"]
        # Remove ALL custom fields - keep only standard x402 token metadata
        # CDP expects only: name, version (for EIP-3009 signing)
        standard_fields = {}
        if "name" in extra:
            standard_fields["name"] = extra["name"]
        if "version" in extra:
            standard_fields["version"] = extra["version"]
        payment_requirements_dict["extra"] = standard_fields if standard_fields else {}

    # Remove null fields that CDP doesn't like
    payment_requirements_dict = {
        k: v for k, v in payment_requirements_dict.items() if v is not None
    }

    req_json = {
        "x402Version": body.x402Version,
        "paymentPayload": payment_payload_dict,
        "paymentRequirements": payment_requirements_dict,
    }

    try:
        if xpay:
            # Pass through exact header form for facilitators expecting paymentHeader
            req_json["paymentHeader"] = xpay
        else:
            # Fallback: create header from canonicalized payload
            req_json["paymentHeader"] = safe_base64_encode(
                json.dumps(req_json["paymentPayload"], separators=(",", ":"))
            )
    except Exception:
        pass

    # Debug: log what we're sending to upstream
    logger.info(f"[{req_id}] 📤 Forwarding to upstream: {cfg.upstream_verify_url}")
    pr_json = json.dumps(payment_requirements_dict, indent=2)
    logger.info("[%s] PaymentRequirements (cleaned): %s", req_id, pr_json)

    async with httpx.AsyncClient(
        timeout=cfg.timeout_s, transport=transport, follow_redirects=True
    ) as client:
        resp = await client.post(
            cfg.upstream_verify_url,
            json=req_json,
        )
    # Record debug snapshot
    global LAST_VERIFY
    snapshot: Dict[str, Any] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "upstream_url": cfg.upstream_verify_url,
        "status_code": resp.status_code,
        "origin": origin,
        "request_id": req_id,
        "sent_payment_requirements": payment_requirements_dict,  # Debug: what we sent
    }
    try:
        resp_json = resp.json()
    except Exception:
        resp_json = None
    snapshot["json"] = resp_json
    snapshot["text"] = resp.text
    if isinstance(resp_json, dict):
        snapshot["payer"] = resp_json.get("payer")
        snapshot["invalidReason"] = resp_json.get("invalidReason") or resp_json.get("error")
    LAST_VERIFY = snapshot

    if resp.status_code != 200:
        return _error_response(
            HTTPException(status_code=resp.status_code, detail=resp.text), req_id
        )
    data = resp_json or {}
    return VerifyResponse(
        isValid=bool(data.get("isValid")),
        payer=str(data.get("payer", "")),
        invalidReason=data.get("invalidReason") or data.get("error") or None,
    )


@router.post("/settle", response_model=SettleResponse)
async def proxy_settle(
    body: ProxySettleRequest,
    request: Request,
    x_ap2_evidence: Optional[str] = Header(None, alias="X-AP2-EVIDENCE"),
    x_verifiable_intent: Optional[str] = Header(None, alias="X-VERIFIABLE-INTENT"),
    x_payment_secure: Optional[str] = Header(None, alias="X-PAYMENT-SECURE"),
    x_risk_session: Optional[str] = Header(None, alias="X-RISK-SESSION"),
    x_risk_trace: Optional[str] = Header(None, alias="X-RISK-TRACE"),
    x_vi_decision_id: Optional[str] = Header(None, alias="X-VI-DECISION-ID"),
    x_vi_evidence_ref: Optional[str] = Header(None, alias="X-VI-EVIDENCE-REF"),
    x_settlement_attempt_id: Optional[str] = Header(None, alias="X-SETTLEMENT-ATTEMPT-ID"),
    x_idempotency_key: Optional[str] = Header(None, alias="X-IDEMPOTENCY-KEY"),
    origin: Optional[str] = Header(None, alias="Origin"),
    cfg: ProxyRuntimeConfig = Depends(get_proxy_cfg),
    response: Response = None,
):
    req_id = uuid.uuid4().hex
    settle_vi_decision_id = x_vi_decision_id
    settle_vi_evidence_ref = x_vi_evidence_ref
    if response is not None:
        response.headers["X-Request-ID"] = req_id
    try:
        if cfg.settle_risk_enabled:
            # Full risk path (unchanged): require trace headers and call Risk Engine.
            sid, tid = parse_risk_ids(x_risk_session, None)
            if not x_payment_secure:
                raise HeaderError("X-PAYMENT-SECURE required")
            tc = parse_x_payment_secure(x_payment_secure)
            mandate = None
            if x_ap2_evidence:
                mandate = parse_x_ap2_evidence(x_ap2_evidence)

            # Extract tid from tracestate if present
            extracted_tid = tid  # from X-RISK-TRACE header (if exists)
            if not extracted_tid and "ts" in tc:
                try:
                    # Decode tracestate to extract tid
                    from urllib.parse import unquote

                    ts_decoded = unquote(tc["ts"])
                    ts_json = json.loads(base64.b64decode(ts_decoded))
                    extracted_tid = ts_json.get("tid")
                    if extracted_tid:
                        logger.info(
                            f"[{req_id}] [PROXY] Extracted tid from tracestate: {extracted_tid}"
                        )
                except Exception as e:
                    logger.debug(f"[{req_id}] Could not extract tid from tracestate: {e}")

            # Construct payment context from paymentPayload
            payment_payload_dict = body.paymentPayload.model_dump(by_alias=True)
            xpay = request.headers.get("X-PAYMENT")
            if xpay:
                try:
                    # Decode X-PAYMENT to preserve all fields
                    payment_payload_dict = json.loads(safe_base64_decode(xpay))
                except Exception:
                    pass  # Fall back to Pydantic serialization

            payment_context = {
                "protocol": payment_payload_dict.get("protocol")
                or payment_payload_dict.get("scheme"),
                "version": payment_payload_dict.get("version")
                or payment_payload_dict.get("x402Version"),
                "network": payment_payload_dict.get("network"),
                "payload": payment_payload_dict.get("payload", {}),
            }

            evaluate_json: Dict[str, Any] = {
                "sid": sid,
                **({"tid": extracted_tid} if extracted_tid else {}),
                "trace_context": {"tp": tc["tp"], **({"ts": tc["ts"]} if "ts" in tc else {})},
                "payment": payment_context,
            }
            if mandate:
                evaluate_json["mandate"] = {
                    "ref": mandate["mr"],
                    "sha256_b64url": mandate["ms"],
                    "mime": mandate["mt"],
                    "size": int(mandate["sz"]),
                }
            # In local mode, use proxy's own /risk/evaluate endpoint (same app)
            if _proxy_local_risk_enabled():
                # Use in-process ASGI transport to call local /risk/evaluate
                rurl = "http://proxy-local"  # Placeholder URL for ASGI transport
                risk_transport = ASGITransport(app=request.app)
                logger.info(f"[{req_id}] [PROXY] ▶ Local /risk/evaluate (in-process)")
            else:
                rurl = _risk_engine_url()
                logger.info(f"[{req_id}] [PROXY] ▶ Risks /risk/evaluate {rurl}")
                risk_transport = None
                try:
                    if urlparse(rurl).netloc == urlparse(str(request.url)).netloc:
                        risk_transport = ASGITransport(app=request.app)
                except Exception:
                    risk_transport = None
            # Attach internal Bearer token when configured
            headers = {}
            token = os.getenv("RISK_INTERNAL_TOKEN")
            if token:
                headers["Authorization"] = f"Bearer {token}"
            async with httpx.AsyncClient(timeout=cfg.timeout_s, transport=risk_transport) as client:
                r = await client.post(f"{rurl}/risk/evaluate", json=evaluate_json, headers=headers)
            if r.status_code != 200:
                raise HTTPException(status_code=r.status_code, detail=r.text)

            # Validate and parse risk response
            try:
                risk_response = EvaluateResponse(**r.json())
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"Invalid risk response: {str(e)}")

            decision = risk_response.decision
            if response is not None:
                response.headers["X-Risk-Decision"] = decision.value
            if risk_response.decision_id:
                response.headers["X-Risk-Decision-ID"] = risk_response.decision_id
            if risk_response.ttl_seconds:
                response.headers["X-Risk-TTL-Seconds"] = str(risk_response.ttl_seconds)
            logger.info(
                "[%s] [PROXY] ✅ Risk decision: %s (id=%s)",
                req_id,
                decision.value,
                risk_response.decision_id,
            )

            # Gate forwarding on decision
            if decision == Decision.deny:
                msg = "Risk denied" + (
                    f": {', '.join(risk_response.reasons)}" if risk_response.reasons else ""
                )
                raise HTTPException(status_code=403, detail=msg)
        else:
            # Risk on settle disabled by configuration; do not call Risk Engine.
            logger.info(
                "[%s] [PROXY] ⏭️  Skipping /risk/evaluate at /x402/settle "
                "(PROXY_SETTLE_RISK_ENABLED=0)",
                req_id,
            )
            if response is not None:
                response.headers["X-Risk-Decision"] = "skipped"

        vi_policy = extract_vi_policy(body.paymentRequirements, body.policy, body.vi_policy)
        if public_vi_assessment_requested(vi_policy, body, x_verifiable_intent):
            if not settle_vi_decision_id:
                sid, tid = parse_risk_ids(x_risk_session, x_risk_trace)
                if not x_payment_secure:
                    raise HeaderError("X-PAYMENT-SECURE required")
                tc = parse_x_payment_secure(x_payment_secure)
                mandate = parse_x_ap2_evidence(x_ap2_evidence) if x_ap2_evidence else None
                trace_context = {
                    "traceparent": tc["tp"],
                    **({"tracestate": tc["ts"]} if "ts" in tc else {}),
                    **({"riskTrace": tid} if tid else {}),
                    "riskSession": sid,
                }
                vi_payload = build_public_vi_assessment_payload(
                    body,
                    request,
                    vi_header=x_verifiable_intent,
                    trace_context=trace_context,
                    ap2_context=_ap2_context_from_mandate_header(mandate),
                    risk_session=sid,
                    risk_trace=tid,
                    origin=origin,
                )
                vi_assessment = await assess_public_verifiable_intent(vi_payload)
                vi_decision = vi_assessment["decision"]
                settle_vi_decision_id = vi_assessment["decision_id"]
                vi_details = vi_assessment.get("vi") or {}
                settle_vi_evidence_ref = (
                    settle_vi_evidence_ref
                    or vi_details.get("evidence_ref")
                    or vi_details.get("evidenceRef")
                )
                if response is not None:
                    response.headers["X-VI-Decision"] = vi_decision
                    response.headers["X-Risk-Decision"] = vi_decision
                    response.headers["X-VI-Decision-ID"] = settle_vi_decision_id
                    response.headers["X-Risk-Decision-ID"] = settle_vi_decision_id
                    if "verified" in vi_details:
                        response.headers["X-VI-Verified"] = str(
                            bool(vi_details["verified"])
                        ).lower()
                    if settle_vi_evidence_ref:
                        response.headers["X-VI-Evidence-Ref"] = str(settle_vi_evidence_ref)
                if vi_decision == "deny":
                    msg = "VI denied" + (
                        f": {', '.join(vi_assessment['reasons'])}"
                        if vi_assessment.get("reasons")
                        else ""
                    )
                    raise HTTPException(status_code=403, detail=msg)
                if vi_decision == "review":
                    raise HTTPException(status_code=403, detail="VI review required")
    except HTTPException as e:
        return _error_response(e, req_id)
    except HeaderError as e:
        logger.error(f"[{req_id}] [PROXY] Header validation failed (settle): {e}")
        return _error_response(HTTPException(status_code=400, detail=str(e)), req_id)

    transport = None
    try:
        if urlparse(cfg.upstream_settle_url).netloc == urlparse(str(request.url)).netloc:
            transport = ASGITransport(app=request.app)
    except Exception:
        transport = None

    # Strip custom AP2 fields from PaymentRequirements before forwarding to upstream
    payment_requirements_dict = body.paymentRequirements.model_dump(
        by_alias=True, exclude_none=True
    )
    if "extra" in payment_requirements_dict:
        extra = payment_requirements_dict["extra"]
        # Keep only standard x402 token metadata
        standard_fields = {}
        if "name" in extra:
            standard_fields["name"] = extra["name"]
        if "version" in extra:
            standard_fields["version"] = extra["version"]
        payment_requirements_dict["extra"] = standard_fields if standard_fields else {}

    # Remove null fields that CDP doesn't like
    payment_requirements_dict = {
        k: v for k, v in payment_requirements_dict.items() if v is not None
    }

    req_json = {
        "x402Version": body.x402Version,
        "paymentPayload": body.paymentPayload.model_dump(by_alias=True),
        "paymentRequirements": payment_requirements_dict,
    }
    xpay = request.headers.get("X-PAYMENT")
    try:
        if xpay:
            req_json["paymentHeader"] = xpay
        else:
            req_json["paymentHeader"] = safe_base64_encode(
                json.dumps(req_json["paymentPayload"], separators=(",", ":"))
            )
    except Exception:
        pass
    async with httpx.AsyncClient(
        timeout=cfg.timeout_s, transport=transport, follow_redirects=True
    ) as client:
        resp = await client.post(
            cfg.upstream_settle_url,
            json=req_json,
        )
    # Record debug snapshot
    global LAST_SETTLE
    snapshot: Dict[str, Any] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "upstream_url": cfg.upstream_settle_url,
        "status_code": resp.status_code,
        "origin": origin,
        "request_id": req_id,
    }
    try:
        resp_json = resp.json()
    except Exception:
        resp_json = None
    snapshot["json"] = resp_json
    snapshot["text"] = resp.text
    if isinstance(resp_json, dict):
        snapshot["payer"] = resp_json.get("payer")
        snapshot["errorReason"] = resp_json.get("errorReason") or resp_json.get("error")
    LAST_SETTLE = snapshot

    if resp.status_code != 200:
        return _error_response(
            HTTPException(status_code=resp.status_code, detail=resp.text), req_id
        )
    data = resp_json or {}
    if settle_vi_decision_id:
        receipt_payload = build_public_vi_receipt_payload(
            body,
            request,
            decision_id=settle_vi_decision_id,
            evidence_ref=settle_vi_evidence_ref,
            settlement_attempt_id=x_settlement_attempt_id,
            idempotency_key=x_idempotency_key,
            settle_response=data,
            origin=origin,
        )
        try:
            receipt = await post_public_vi_receipt(receipt_payload)
            if response is not None:
                response.headers["X-VI-Receipt-Status"] = "recorded"
                receipt_id = receipt.get("receipt_id") or receipt.get("receiptId")
                if receipt_id:
                    response.headers["X-VI-Receipt-ID"] = str(receipt_id)
        except HTTPException as e:
            logger.warning("[%s] [PROXY] VI receipt post failed: %s", req_id, e.detail)
            if response is not None:
                response.headers["X-VI-Receipt-Status"] = "failed"
    return SettleResponse(
        success=bool(data.get("success")),
        payer=str(data.get("payer", "")),
        transaction=data.get("transaction"),
        network=data.get("network"),
        errorReason=data.get("errorReason") or data.get("error") or None,
    )


@router.get("/debug")
async def proxy_debug(cfg: ProxyRuntimeConfig = Depends(get_proxy_cfg)) -> Dict[str, Any]:
    if getattr(cfg, "debug_enabled", True) is False:
        raise HTTPException(status_code=404, detail="Not Found")
    return {
        "upstream": {
            "verify_url": cfg.upstream_verify_url,
            "settle_url": cfg.upstream_settle_url,
        },
        "last_verify": LAST_VERIFY,
        "last_settle": LAST_SETTLE,
    }


# Optional utility for seller-side middleware that may be added in a later step.
class TokenAmount(NamedTuple):
    amount: int
