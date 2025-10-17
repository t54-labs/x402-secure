# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import base64
import json
import logging
import os
import time
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Dict, List, NamedTuple, Optional, Union
import uuid
from urllib.parse import urlparse
import json as _json

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from httpx import ASGITransport
from pydantic import BaseModel, Field, ValidationError
from .headers import (
    HeaderError,
    parse_x_payment_secure,
    parse_x_ap2_evidence,
    parse_risk_ids,
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
    x402Version: int = Field(1, description="x402 API version")
    paymentPayload: PaymentPayload
    paymentRequirements: PaymentRequirements
    ap2EvidenceHeader: Optional[str] = Field(
        None, description="base64(JSON Evidence) if not in header"
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


def verify_payment_hash_binding(ev: AP2Evidence, req: Request, payment_payload: PaymentPayload) -> None:
    payload_obj = payment_payload.model_dump(by_alias=True)
    expected = compute_expected_payment_hash(req, payload_obj)
    if _b32(ev.paymentHash) != expected:
        raise HTTPException(status_code=422, detail="AP2: paymentHash mismatch")


def verify_merchant_identity(policy: AP2Policy, pr: PaymentRequirements) -> None:
    if not policy.acceptedMerchantIds:
        return
    host = urlparse(pr.resource).netloc.lower()
    ok = any(
        mid.startswith("did:web:")
        and mid.split(":", 2)[2].lower() in (host, host.split(":")[0])
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
        raise HTTPException(status_code=422, detail="EIP-712 verification unavailable; install eth-account")
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


def enforce_amount_and_asset(payment_payload: PaymentPayload, payment_requirements: PaymentRequirements) -> None:
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


async def fetch_and_validate_ap2_context(ev: AP2Evidence, risk_engine_url: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetch trace context and PaymentMandate VC from Risk Engine using UIDs in X-AP2-EVIDENCE.
    
    This enables deep risk assessment without embedding data in payment payload.
    """
    if not risk_engine_url:
        risk_engine_url = os.getenv("RISK_ENGINE_URL", "http://localhost:8001")
    
    result = {"trace_context": None, "payment_mandate": None}
    
    # Fetch trace context using trace_uid
    if ev.trace_uid:
        logger.info(f"[PROXY] Fetching trace context for trace_uid={ev.trace_uid} from {risk_engine_url}")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{risk_engine_url}/ap2/trace/{ev.trace_uid}",
                    headers={"Authorization": "Bearer proxy-internal"}  # TODO: proper auth
                )
                logger.info(f"[PROXY] Risk Engine responded with status {resp.status_code}")
                if resp.status_code == 200:
                    result["trace_context"] = resp.json()
                    logger.info(f"[PROXY] Trace context: {result['trace_context']}")
                    logger.info(f"[PROXY] ✅ Fetched trace context for trace_uid={ev.trace_uid}")
                else:
                    logger.warning(f"[PROXY] Failed to fetch trace context: status {resp.status_code}, body: {resp.text}")
        except Exception as e:
            logger.warning(f"[PROXY] Exception fetching trace context: {e}")
    else:
        logger.warning(f"[PROXY] No trace_uid in AP2Evidence, skipping trace context fetch")
    
    # Fetch PaymentMandate VC using payment_uid
    if ev.payment_uid:
        logger.info(f"[PROXY] Fetching PaymentMandate for payment_uid={ev.payment_uid} from {risk_engine_url}")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{risk_engine_url}/ap2/mandate/payment/{ev.payment_uid}",
                    headers={"Authorization": "Bearer proxy-internal"}  # TODO: proper auth
                )
                logger.info(f"[PROXY] Risk Engine responded with status {resp.status_code}")
                if resp.status_code == 200:
                    mandate_data = resp.json()
                    result["payment_mandate"] = mandate_data
                    logger.info(f"[PROXY] ✅ Fetched PaymentMandate (risk approval) for payment_uid={ev.payment_uid}")
                else:
                    logger.warning(f"[PROXY] Failed to fetch PaymentMandate: status {resp.status_code}, body: {resp.text}")
        except Exception as e:
            logger.warning(f"[PROXY] Exception fetching PaymentMandate: {e}")
    else:
        logger.info(f"[PROXY] No payment_uid in AP2Evidence, skipping PaymentMandate fetch")
    
    return result


def verify_ap2(
    request: Request, body: ProxyVerifyRequest, origin_header: Optional[str], ap2_header: Optional[str]
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
        default_factory=lambda: os.getenv("PROXY_UPSTREAM_VERIFY_URL", "http://localhost:8001/verify")
    )
    upstream_settle_url: str = Field(
        default_factory=lambda: os.getenv("PROXY_UPSTREAM_SETTLE_URL", "http://localhost:8001/settle")
    )
    timeout_s: float = Field(default_factory=lambda: float(os.getenv("PROXY_TIMEOUT_S", "15")))
    debug_enabled: bool = Field(
        default_factory=lambda: (os.getenv("PROXY_DEBUG_ENABLED", "1").lower() in {"1", "true", "yes"})
    )
    # Feature flag: optionally enforce risk evaluate at settle (default OFF)
    # TODO(later): Consider enabling by default once prod readiness is confirmed.
    settle_risk_enabled: bool = Field(
        default_factory=lambda: (os.getenv("PROXY_SETTLE_RISK_ENABLED", "0").lower() in {"1", "true", "yes"})
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


def _proxy_local_risk_enabled() -> bool:
    """Check if proxy should use local risk storage instead of forwarding."""
    return os.getenv("PROXY_LOCAL_RISK", "0").lower() in {"1", "true", "yes"}


@router.post("/verify", response_model=VerifyResponse)
async def proxy_verify(
    body: ProxyVerifyRequest,
    request: Request,
    x_ap2_evidence: Optional[str] = Header(None, alias="X-AP2-EVIDENCE"),
    x_payment_secure: Optional[str] = Header(None, alias="X-PAYMENT-SECURE"),
    x_risk_session: Optional[str] = Header(None, alias="X-RISK-SESSION"),
    origin: Optional[str] = Header(None, alias="Origin"),
    cfg: ProxyRuntimeConfig = Depends(get_proxy_cfg),
    response: Response = None,
):
    req_id = uuid.uuid4().hex
    if response is not None:
        response.headers["X-Request-ID"] = req_id
    try:
        # Parse required IDs and trace context; optional mandate
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
                    logger.info(f"[{req_id}] [PROXY] Extracted tid from tracestate: {extracted_tid}")
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
            "version": payment_payload_dict.get("version") or payment_payload_dict.get("x402Version"),
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
        logger.info(f"[{req_id}] [PROXY] ✅ Risk decision: {decision.value} (id={risk_response.decision_id})")

        # Gate forwarding on decision
        if decision == Decision.deny:
            msg = "Risk denied" + (f": {', '.join(risk_response.reasons)}" if risk_response.reasons else "")
            raise HTTPException(status_code=403, detail=msg)
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
    payment_requirements_dict = body.paymentRequirements.model_dump(by_alias=True, exclude_none=True)
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
    payment_requirements_dict = {k: v for k, v in payment_requirements_dict.items() if v is not None}
    
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
    logger.info(f"[{req_id}] PaymentRequirements (cleaned): {json.dumps(payment_requirements_dict, indent=2)}")

    async with httpx.AsyncClient(timeout=cfg.timeout_s, transport=transport, follow_redirects=True) as client:
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
        return _error_response(HTTPException(status_code=resp.status_code, detail=resp.text), req_id)
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
    x_payment_secure: Optional[str] = Header(None, alias="X-PAYMENT-SECURE"),
    x_risk_session: Optional[str] = Header(None, alias="X-RISK-SESSION"),
    origin: Optional[str] = Header(None, alias="Origin"),
    cfg: ProxyRuntimeConfig = Depends(get_proxy_cfg),
    response: Response = None,
):
    req_id = uuid.uuid4().hex
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
                        logger.info(f"[{req_id}] [PROXY] Extracted tid from tracestate: {extracted_tid}")
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
                "version": payment_payload_dict.get("version") or payment_payload_dict.get("x402Version"),
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
            logger.info(f"[{req_id}] [PROXY] ✅ Risk decision: {decision.value} (id={risk_response.decision_id})")

            # Gate forwarding on decision
            if decision == Decision.deny:
                msg = "Risk denied" + (f": {', '.join(risk_response.reasons)}" if risk_response.reasons else "")
                raise HTTPException(status_code=403, detail=msg)
        else:
            # Risk on settle disabled by configuration; do not call Risk Engine.
            logger.info(f"[{req_id}] [PROXY] ⏭️  Skipping /risk/evaluate at /x402/settle (PROXY_SETTLE_RISK_ENABLED=0)")
            if response is not None:
                response.headers["X-Risk-Decision"] = "skipped"
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
    payment_requirements_dict = body.paymentRequirements.model_dump(by_alias=True, exclude_none=True)
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
    payment_requirements_dict = {k: v for k, v in payment_requirements_dict.items() if v is not None}
    
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
    async with httpx.AsyncClient(timeout=cfg.timeout_s, transport=transport, follow_redirects=True) as client:
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
        return _error_response(HTTPException(status_code=resp.status_code, detail=resp.text), req_id)
    data = resp_json or {}
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
