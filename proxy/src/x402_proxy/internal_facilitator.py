# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Literal, Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

DEFAULT_TRUSTLINE_VERIFY_CHAIN_PATH = "verifiable-intent/verify-chain"

internal_router = APIRouter(
    prefix="/internal/x402-secure/facilitator",
    tags=["x402-secure-internal-facilitator"],
)


class FacilitatorInfo(BaseModel):
    id: str = Field(..., description="Hosted facilitator identifier")
    network: str = Field(..., description="Payment network handled by the facilitator")
    environment: Optional[str] = Field(default=None)


class InternalPolicy(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    policy_id: Optional[str] = Field(default=None, alias="policyId")
    policy_version: Optional[str] = Field(default=None, alias="policyVersion")
    require_verifiable_intent: bool = Field(default=False, alias="requireVerifiableIntent")
    require_verified_intent: bool = Field(default=False, alias="requireVerifiedIntent")
    require_payment_mandate: bool = Field(default=False, alias="requirePaymentMandate")
    require_holder_binding: bool = Field(default=False, alias="requireHolderBinding")
    require_trace: bool = Field(default=False, alias="requireTrace")
    accepted_issuers: List[str] = Field(default_factory=list, alias="acceptedIssuers")
    review_mode: Literal["allow", "block", "review"] = Field(default="block", alias="reviewMode")


class VerifiableIntentReference(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    format: Optional[str] = "sd-jwt"
    profile: Optional[str] = "mastercard-vi-v0.1"
    presentation: Optional[str] = Field(default=None, repr=False)
    presentation_ref: Optional[str] = Field(default=None, alias="presentationRef")
    presentation_hash: Optional[str] = Field(default=None, alias="presentationHash")
    claims: Dict[str, Any] = Field(default_factory=dict)
    constraints: Dict[str, Any] = Field(default_factory=dict)
    layers: Dict[str, Any] = Field(default_factory=dict)
    key_binding: Optional[Dict[str, Any]] = Field(default=None, alias="keyBinding")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class VerifiableIntentChainNode(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    format: Optional[str] = None
    sd_jwt: Optional[str] = Field(default=None, alias="sdJwt", repr=False)
    jwt: Optional[str] = Field(default=None, repr=False)


class VerifiableIntentChainReference(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    l1_credential: VerifiableIntentChainNode = Field(alias="l1Credential")
    l2_delegation: VerifiableIntentChainNode = Field(alias="l2Delegation")
    l3_final_action: VerifiableIntentChainNode = Field(alias="l3FinalAction")


class AP2ReferenceSet(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    intent_mandate_ref: Optional[str] = Field(default=None, alias="intentMandateRef")
    cart_mandate_ref: Optional[str] = Field(default=None, alias="cartMandateRef")
    payment_mandate_ref: Optional[str] = Field(default=None, alias="paymentMandateRef")
    intent_mandate_hash: Optional[str] = Field(default=None, alias="intentMandateHash")
    cart_mandate_hash: Optional[str] = Field(default=None, alias="cartMandateHash")
    payment_mandate_hash: Optional[str] = Field(default=None, alias="paymentMandateHash")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TraceReferenceSet(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    risk_session: Optional[str] = Field(default=None, alias="riskSession")
    trace_id: Optional[str] = Field(default=None, alias="traceId")
    trace_ref: Optional[str] = Field(default=None, alias="traceRef")
    trace_hash: Optional[str] = Field(default=None, alias="traceHash")
    traceparent: Optional[str] = None
    tracestate: Optional[str] = None
    current_task: Optional[str] = Field(default=None, alias="currentTask")
    user_instruction: Optional[str] = Field(default=None, alias="userInstruction")
    reasoning_process: Optional[str] = Field(default=None, alias="reasoningProcess")
    prompt_trace: List[Dict[str, Any]] = Field(default_factory=list, alias="promptTrace")
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list, alias="toolCalls")
    final_decision: Optional[str] = Field(default=None, alias="finalDecision")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class X402SecureExtension(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version: str = "x402-secure.vi.v1"
    policy: InternalPolicy = Field(default_factory=InternalPolicy)
    verifiable_intent: Optional[VerifiableIntentReference] = Field(
        default=None,
        alias="verifiableIntent",
    )
    verifiable_intent_chain: Optional[VerifiableIntentChainReference] = Field(
        default=None,
        alias="verifiableIntentChain",
    )
    ap2: Optional[AP2ReferenceSet] = None
    trace: Optional[TraceReferenceSet] = None


class InternalPaymentContext(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    protocol: str = "x402"
    chain: Optional[str] = None
    network: Optional[str] = None
    asset: Optional[str] = None
    issuer: Optional[str] = None
    amount: Optional[Any] = None
    currency: Optional[str] = None
    destination: Optional[str] = None
    pay_to: Optional[str] = Field(default=None, alias="payTo")
    resource: Optional[str] = None
    merchant_origin: Optional[str] = Field(default=None, alias="merchantOrigin")
    payment_hash: Optional[str] = Field(default=None, alias="paymentHash")
    payment_requirements_hash: Optional[str] = Field(
        default=None,
        alias="paymentRequirementsHash",
    )
    payload_hash: Optional[str] = Field(
        default=None,
        alias="payloadHash",
        validation_alias=AliasChoices("payloadHash", "paymentPayloadHash"),
    )
    payload: Dict[str, Any] = Field(default_factory=dict)
    payment_requirements: Dict[str, Any] = Field(
        default_factory=dict,
        alias="paymentRequirements",
    )


class InternalFacilitatorEvaluateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    facilitator: FacilitatorInfo
    payment: InternalPaymentContext
    extensions: Dict[str, Any] = Field(default_factory=dict)
    merchant: Dict[str, Any] = Field(default_factory=dict)
    buyer: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BindingProfileResult(BaseModel):
    payment_bound: bool = Field(..., alias="paymentBound")
    chain: Optional[str] = None
    asset: Optional[str] = None
    issuer: Optional[str] = None
    amount: Optional[str] = None
    destination: Optional[str] = None
    resource: Optional[str] = None
    violations: List[Dict[str, Any]] = Field(default_factory=list)
    hashes: Dict[str, str] = Field(default_factory=dict)
    canonical: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)


class InternalFacilitatorDecisionResponse(BaseModel):
    decision: Literal["allow", "deny", "review"]
    decision_id: str
    risk_level: str = "medium"
    ttl_seconds: int = 300
    vi: Dict[str, Any] = Field(default_factory=dict)
    binding: Dict[str, Any] = Field(default_factory=dict)
    reasons: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    trustline_assessment: Dict[str, Any] = Field(default_factory=dict)


class InternalEvidenceSessionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    facilitator: Optional[FacilitatorInfo] = None
    agent_id: Optional[str] = Field(default=None, alias="agentId")
    wallet_address: Optional[str] = Field(default=None, alias="walletAddress")
    merchant_id: Optional[str] = Field(default=None, alias="merchantId")
    policy: InternalPolicy = Field(default_factory=InternalPolicy)
    required_evidence: List[str] = Field(default_factory=list, alias="requiredEvidence")
    expires_in_seconds: int = Field(default=900, alias="expiresInSeconds")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class InternalEvidenceUploadRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(..., alias="sessionId")
    evidence_type: str = Field(..., alias="evidenceType")
    evidence: Any
    sha256: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class InternalReceiptRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    decision_id: str = Field(..., alias="decisionId")
    evidence_ref: Optional[str] = Field(default=None, alias="evidenceRef")
    settlement_attempt_id: Optional[str] = Field(default=None, alias="settlementAttemptId")
    idempotency_key: Optional[str] = Field(default=None, alias="idempotencyKey")
    payment: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


def _configured_internal_token() -> Optional[str]:
    return (
        os.getenv("X402_SECURE_INTERNAL_TOKEN")
        or os.getenv("FACILITATOR_INTERNAL_TOKEN")
        or os.getenv("INTERNAL_FACILITATOR_TOKEN")
    )


def require_internal_auth(
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_internal_token: Optional[str] = Header(default=None, alias="X-Internal-Token"),
) -> None:
    expected = _configured_internal_token()
    if not expected:
        disabled = os.getenv("X402_SECURE_INTERNAL_AUTH_DISABLED", "").lower()
        if disabled in {"1", "true", "yes"}:
            return
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal facilitator auth token is not configured",
        )
    supplied = x_internal_token
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization.split(" ", 1)[1].strip()
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal token",
        )


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _fingerprint(value: Any) -> str:
    return f"sha256:{hashlib.sha256(_canonical_json(value).encode()).hexdigest()}"


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _decimal_string(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    text = format(dec.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _xrpl_amount_from_payload(payment: InternalPaymentContext) -> Optional[str]:
    raw_amount = payment.payload.get("Amount")
    if isinstance(raw_amount, dict):
        return _decimal_string(raw_amount.get("value"))
    if raw_amount is None:
        return None
    asset = str(payment.asset or payment.currency or "XRP").upper()
    text = str(raw_amount)
    if asset == "XRP" and text.isdigit():
        return _decimal_string(Decimal(text) / Decimal("1000000"))
    return _decimal_string(text)


def _add_violation(
    violations: List[Dict[str, Any]],
    code: str,
    message: str,
    field: Optional[str] = None,
) -> None:
    entry = {"code": code, "message": message}
    if field:
        entry["field"] = field
    violations.append(entry)


def _build_xrpl_binding(payment: InternalPaymentContext) -> BindingProfileResult:
    payload = payment.payload or {}
    amount_object = payload.get("Amount") if isinstance(payload.get("Amount"), dict) else {}
    amount = _decimal_string(payment.amount) or _xrpl_amount_from_payload(payment)
    asset = _first_present(
        payment.asset,
        payment.currency,
        amount_object.get("currency"),
        payload.get("currency"),
        "XRP",
    )
    issuer = _first_present(payment.issuer, amount_object.get("issuer"), payload.get("issuer"))
    destination = _first_present(payment.destination, payment.pay_to, payload.get("Destination"))
    resource = _first_present(payment.resource, payment.merchant_origin)
    hashes = {
        "paymentPayloadHash": payment.payload_hash or _fingerprint(payload),
        "paymentRequirementsHash": payment.payment_requirements_hash
        or _fingerprint(payment.payment_requirements),
    }
    if payment.payment_hash:
        hashes["paymentHash"] = payment.payment_hash

    violations: List[Dict[str, Any]] = []
    if not amount:
        _add_violation(violations, "XRPL_AMOUNT_MISSING", "XRPL amount is required.", "amount")
    if not destination:
        _add_violation(
            violations,
            "XRPL_DESTINATION_MISSING",
            "XRPL destination account is required.",
            "destination",
        )
    if str(asset).upper() != "XRP" and not issuer:
        _add_violation(
            violations,
            "XRPL_ISSUER_MISSING",
            "Issued XRPL currencies require an issuer.",
            "issuer",
        )

    canonical = {
        "chain": "xrpl",
        "asset": asset,
        "issuer": issuer,
        "amount": amount,
        "destination": destination,
        "resource": resource,
        "hashes": hashes,
    }
    return BindingProfileResult(
        paymentBound=not violations,
        chain="xrpl",
        asset=str(asset) if asset else None,
        issuer=str(issuer) if issuer else None,
        amount=amount,
        destination=str(destination) if destination else None,
        resource=str(resource) if resource else None,
        violations=violations,
        hashes=hashes,
        canonical=canonical,
    )


def _build_generic_binding(payment: InternalPaymentContext) -> BindingProfileResult:
    amount = _decimal_string(payment.amount)
    destination = _first_present(payment.destination, payment.pay_to)
    resource = _first_present(payment.resource, payment.merchant_origin)
    chain = _first_present(payment.chain, payment.network)
    asset = _first_present(payment.asset, payment.currency)
    hashes = {
        "paymentPayloadHash": payment.payload_hash or _fingerprint(payment.payload),
        "paymentRequirementsHash": payment.payment_requirements_hash
        or _fingerprint(payment.payment_requirements),
    }
    if payment.payment_hash:
        hashes["paymentHash"] = payment.payment_hash

    violations: List[Dict[str, Any]] = []
    if not amount:
        _add_violation(
            violations,
            "PAYMENT_AMOUNT_MISSING",
            "Payment amount is required.",
            "amount",
        )
    if not destination:
        _add_violation(
            violations,
            "PAYMENT_DESTINATION_MISSING",
            "Payment destination is required.",
            "destination",
        )
    return BindingProfileResult(
        paymentBound=not violations,
        chain=str(chain) if chain else None,
        asset=str(asset) if asset else None,
        issuer=payment.issuer,
        amount=amount,
        destination=str(destination) if destination else None,
        resource=str(resource) if resource else None,
        violations=violations,
        hashes=hashes,
        canonical={
            "chain": chain,
            "asset": asset,
            "issuer": payment.issuer,
            "amount": amount,
            "destination": destination,
            "resource": resource,
            "hashes": hashes,
        },
    )


def build_binding_profile(payment: InternalPaymentContext) -> BindingProfileResult:
    chain = str(_first_present(payment.chain, payment.network, "")).lower()
    if chain == "xrpl":
        return _build_xrpl_binding(payment)
    return _build_generic_binding(payment)


def _extract_extension(extensions: Dict[str, Any]) -> X402SecureExtension:
    raw = extensions.get("x402Secure") or extensions.get("x402_secure") or extensions
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail="extensions.x402Secure must be an object")
    return X402SecureExtension(**raw)


def _trace_context_payload(trace: Optional[TraceReferenceSet]) -> Dict[str, Any]:
    if not trace:
        return {}
    context = trace.model_dump(by_alias=True, exclude_none=True)
    aliases = {
        "riskSession": "risk_session",
        "traceId": "trace_id",
        "traceRef": "trace_ref",
        "traceHash": "trace_hash",
        "currentTask": "current_task",
        "userInstruction": "user_instruction",
        "reasoningProcess": "reasoning_process",
        "promptTrace": "prompt_trace",
        "toolCalls": "tool_calls",
        "finalDecision": "final_decision",
    }
    for alias, field_name in aliases.items():
        if alias in context and field_name not in context:
            context[field_name] = context[alias]
    return context


def _trustline_base_url() -> str:
    return (
        os.getenv("TRUSTLINE_API_URL")
        or os.getenv("TRUSTLINE_BASE_URL")
        or os.getenv("RISK_ENGINE_URL")
        or "http://localhost:8001"
    ).rstrip("/")


def _trustline_validation_path(path: str) -> str:
    prefix = os.getenv("TRUSTLINE_VALIDATION_PREFIX", "/api/v1/validation").strip("/")
    return f"/{prefix}/{path.lstrip('/')}"


def _default_trustline_token() -> Optional[str]:
    return (
        os.getenv("TRUSTLINE_INTERNAL_TOKEN")
        or os.getenv("RISK_INTERNAL_TOKEN")
        or os.getenv("TRUSTLINE_API_KEY")
        or os.getenv("X402_SECURE_TRUSTLINE_TOKEN")
    )


def _json_mapping_from_env(*names: str) -> Dict[str, str]:
    raw = ""
    source = ""
    for name in names:
        raw = os.getenv(name, "").strip()
        if raw:
            source = name
            break
    if not raw:
        return {}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"{source} must be a JSON object") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=500, detail=f"{source} must be a JSON object")

    mapping: Dict[str, str] = {}
    for key, value in parsed.items():
        if value is None:
            continue
        token = str(value).strip()
        if token:
            mapping[str(key)] = token
    return mapping


def _trustline_token_by_org() -> Dict[str, str]:
    return _json_mapping_from_env(
        "X402_SECURE_TRUSTLINE_TOKENS_BY_ORG_JSON",
        "TRUSTLINE_TOKENS_BY_ORG_JSON",
    )


def _trustline_token_by_policy() -> Dict[str, str]:
    return _json_mapping_from_env(
        "X402_SECURE_TRUSTLINE_TOKENS_BY_POLICY_JSON",
        "TRUSTLINE_TOKENS_BY_POLICY_JSON",
        "TRUSTLINE_API_KEYS_BY_POLICY_JSON",
    )


def _extract_policy(payload: Dict[str, Any]) -> Dict[str, Any]:
    policy = payload.get("policy")
    if isinstance(policy, dict):
        return policy
    metadata = payload.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("policy"), dict):
        return metadata["policy"]
    return {}


def _extract_developer_org_id(payload: Dict[str, Any]) -> Optional[str]:
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return None
    for key in ("developerOrgId", "developer_org_id", "organizationId", "organization_id"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_policy_id(payload: Dict[str, Any]) -> Optional[str]:
    policy = _extract_policy(payload)
    for key in ("policyId", "policy_id"):
        value = policy.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _trustline_token_for_payload(payload: Dict[str, Any]) -> Optional[str]:
    org_id = _extract_developer_org_id(payload)
    if org_id:
        token = _trustline_token_by_org().get(org_id)
        if token:
            return token

    policy_id = _extract_policy_id(payload)
    if policy_id:
        token = _trustline_token_by_policy().get(policy_id)
        if token:
            return token

    return _default_trustline_token()


def _trustline_auth_headers(payload: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    token = _trustline_token_for_payload(payload or {})
    return {"Authorization": f"Bearer {token}"} if token else {}


async def post_trustline_validation(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    base_url = _trustline_base_url()
    if base_url.endswith("/validation"):
        url = f"{base_url}/{path.lstrip('/')}"
    else:
        url = f"{base_url}{_trustline_validation_path(path)}"
    async with httpx.AsyncClient(timeout=float(os.getenv("TRUSTLINE_TIMEOUT_S", "15"))) as client:
        response = await client.post(url, json=payload, headers=_trustline_auth_headers(payload))
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Trustline error: {response.text}")
    try:
        return response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Invalid Trustline response: {exc}") from exc


def _trustline_facilitator_verify_chain_path() -> str:
    return (
        os.getenv("TRUSTLINE_FACILITATOR_VERIFY_CHAIN_PATH")
        or os.getenv("TRUSTLINE_VERIFY_CHAIN_PATH")
        or DEFAULT_TRUSTLINE_VERIFY_CHAIN_PATH
    ).strip("/")


def build_trustline_assessment_payload(
    request: InternalFacilitatorEvaluateRequest,
    extension: X402SecureExtension,
    binding: BindingProfileResult,
) -> Dict[str, Any]:
    payment = request.payment
    merchant_origin = _first_present(
        payment.merchant_origin,
        request.merchant.get("origin"),
        request.merchant.get("url"),
    )
    trace_context = _trace_context_payload(extension.trace)
    if request.metadata.get("traceparent") and "traceparent" not in trace_context:
        trace_context["traceparent"] = request.metadata["traceparent"]

    payment_context = {
        "protocol": payment.protocol,
        "chain": binding.chain or payment.chain,
        "network": payment.network or binding.chain,
        "asset": binding.asset or payment.asset or payment.currency,
        "issuer": binding.issuer or payment.issuer,
        "amount": binding.amount or payment.amount,
        "currency": payment.currency or binding.asset,
        "destination": binding.destination or payment.destination or payment.pay_to,
        "merchantId": request.merchant.get("id"),
        "merchantOrigin": merchant_origin,
        "resource": binding.resource or payment.resource,
        "paymentHash": payment.payment_hash,
        "paymentRequirementsHash": binding.hashes.get("paymentRequirementsHash"),
        "payloadHash": binding.hashes.get("paymentPayloadHash"),
        "raw": {
            "facilitator": request.facilitator.model_dump(exclude_none=True),
            "paymentRequirements": payment.payment_requirements,
        },
    }
    vi = (
        extension.verifiable_intent.model_dump(by_alias=True, exclude_none=True)
        if extension.verifiable_intent
        else None
    )
    vi_chain = (
        extension.verifiable_intent_chain.model_dump(by_alias=True, exclude_none=True)
        if extension.verifiable_intent_chain
        else None
    )
    ap2_context = (
        extension.ap2.model_dump(by_alias=True, exclude_none=True) if extension.ap2 else None
    )
    return {
        "agentId": request.buyer.get("agentId")
        or request.buyer.get("agent_id")
        or request.buyer.get("walletAddress")
        or request.buyer.get("wallet_address")
        or f"{request.facilitator.id}:unknown-agent",
        "walletAddress": request.buyer.get("walletAddress") or request.buyer.get("wallet_address"),
        "verifiableIntent": vi,
        "verifiableIntentChain": vi_chain,
        "paymentContext": payment_context,
        "binding": binding.model_dump(by_alias=True, exclude_none=True),
        "ap2Context": ap2_context,
        "traceContext": trace_context,
        "policy": extension.policy.model_dump(by_alias=True),
        "metadata": {
            **request.metadata,
            "source": "x402_secure_internal_facilitator",
            "facilitator": request.facilitator.model_dump(exclude_none=True),
            "merchant": request.merchant,
            "x402SecureVersion": extension.version,
        },
    }


def _effective_decision(
    trustline_decision: str,
    policy: InternalPolicy,
    warnings: List[str],
) -> str:
    normalized = trustline_decision.strip().lower()
    if normalized == "approve":
        normalized = "allow"
    elif normalized == "decline":
        normalized = "deny"
    elif normalized not in {"allow", "deny", "review"}:
        warnings.append(f"Unknown Trustline decision '{trustline_decision}' converted to review.")
        normalized = "review"

    if normalized != "review":
        return normalized
    if policy.review_mode == "allow":
        warnings.append("Trustline returned review; policy reviewMode=allow converted it to allow.")
        return "allow"
    if policy.review_mode == "block":
        warnings.append("Trustline returned review; policy reviewMode=block converted it to deny.")
        return "deny"
    return "review"


def _enforce_required_verified_vi_result(
    decision: str,
    policy: InternalPolicy,
    vi_result: Dict[str, Any],
    reasons: List[str],
    warnings: List[str],
) -> str:
    if not policy.require_verified_intent or bool(vi_result.get("verified")):
        return decision
    reason = "Verified Verifiable Intent required"
    if reason not in reasons:
        reasons.append(reason)
    warnings.append("Policy requireVerifiedIntent denied an unverified Trustline VI result.")
    return "deny"


def _vi_result_from_trustline_response(trustline_response: Dict[str, Any]) -> Dict[str, Any]:
    vi_result = trustline_response.get("vi")
    if isinstance(vi_result, dict):
        return vi_result
    chain = trustline_response.get("chain")
    if not isinstance(chain, dict):
        return {}
    chain_verified = bool(chain.get("verified"))
    constraint_ok = chain.get("constraint_satisfied") is not False
    error_code = chain.get("error_code")
    verified_for_payment = chain_verified and constraint_ok and not error_code
    return {
        "present": bool(chain.get("present")),
        "parsed": bool(chain.get("parsed")),
        "verified": verified_for_payment,
        "chain_verified": chain_verified,
        "constraint_satisfied": chain.get("constraint_satisfied"),
        "profile": chain.get("profile"),
        "error_code": error_code,
        "violations": list(chain.get("violations") or []),
        "not_evaluable": list(chain.get("not_evaluable") or []),
        "label": chain.get("label"),
        "binding": chain.get("binding") or {},
    }


def _append_vi_reasons(
    reasons: List[str],
    vi_result: Dict[str, Any],
) -> None:
    error_code = vi_result.get("error_code")
    if error_code and error_code not in reasons:
        reasons.append(str(error_code))
    for violation in vi_result.get("violations") or []:
        text = violation if isinstance(violation, str) else json.dumps(violation, sort_keys=True)
        if text not in reasons:
            reasons.append(text)


@internal_router.post(
    "/evaluate",
    response_model=InternalFacilitatorDecisionResponse,
    dependencies=[Depends(require_internal_auth)],
)
async def evaluate_facilitator_payment(
    body: InternalFacilitatorEvaluateRequest,
) -> Dict[str, Any]:
    extension = _extract_extension(body.extensions)
    binding = build_binding_profile(body.payment)
    payload = build_trustline_assessment_payload(body, extension, binding)
    trustline_path = _trustline_facilitator_verify_chain_path()
    try:
        trustline_response = await post_trustline_validation(trustline_path, payload)
    except HTTPException as exc:
        logger.warning(
            "[INTERNAL] facilitator=%s trustline_verify_chain_failed status=%s detail=%s",
            body.facilitator.id,
            exc.status_code,
            exc.detail,
        )
        return {
            "decision": "deny",
            "decision_id": f"xs_dec_{uuid.uuid4().hex}",
            "risk_level": "high",
            "ttl_seconds": 60,
            "vi": {},
            "binding": binding.model_dump(by_alias=False, exclude_none=True),
            "reasons": ["Trustline verification unavailable"],
            "warnings": [f"Trustline verification failed closed: {exc.detail}"],
            "trustline_assessment": {"source": trustline_path, "error": str(exc.detail)},
        }
    except httpx.HTTPError as exc:
        logger.warning(
            "[INTERNAL] facilitator=%s trustline_verify_chain_http_error error=%s",
            body.facilitator.id,
            exc,
        )
        return {
            "decision": "deny",
            "decision_id": f"xs_dec_{uuid.uuid4().hex}",
            "risk_level": "high",
            "ttl_seconds": 60,
            "vi": {},
            "binding": binding.model_dump(by_alias=False, exclude_none=True),
            "reasons": ["Trustline verification unavailable"],
            "warnings": [f"Trustline verification failed closed: {exc}"],
            "trustline_assessment": {"source": trustline_path, "error": str(exc)},
        }

    warnings = list(trustline_response.get("warnings") or [])
    reasons = list(trustline_response.get("reasons") or [])
    vi_result = _vi_result_from_trustline_response(trustline_response)
    _append_vi_reasons(reasons, vi_result)
    decision = _effective_decision(
        str(trustline_response.get("decision", "review")),
        extension.policy,
        warnings,
    )
    decision = _enforce_required_verified_vi_result(
        decision,
        extension.policy,
        vi_result,
        reasons,
        warnings,
    )
    logger.info(
        "[INTERNAL] facilitator=%s decision=%s trustline_decision=%s",
        body.facilitator.id,
        decision,
        trustline_response.get("decision"),
    )
    decision_id = (
        trustline_response.get("decision_id")
        or trustline_response.get("decisionId")
        or f"xs_dec_{uuid.uuid4().hex}"
    )
    return {
        "decision": decision,
        "decision_id": decision_id,
        "risk_level": trustline_response.get("risk_level") or ("high" if decision == "deny" else "low"),
        "ttl_seconds": trustline_response.get("ttl_seconds", 300),
        "vi": vi_result,
        "binding": trustline_response.get("binding")
        or binding.model_dump(by_alias=False, exclude_none=True),
        "reasons": reasons,
        "warnings": warnings,
        "trustline_assessment": trustline_response.get("trustline_assessment")
        or {
            "source": trustline_path,
            "verifier_mode": trustline_response.get("verifierMode"),
            "latency_ms": trustline_response.get("latencyMs"),
        },
    }


@internal_router.post(
    "/evidence-session",
    dependencies=[Depends(require_internal_auth)],
)
async def create_facilitator_evidence_session(
    body: InternalEvidenceSessionRequest,
) -> Dict[str, Any]:
    payload = {
        "agentId": body.agent_id,
        "walletAddress": body.wallet_address,
        "merchantId": body.merchant_id,
        "policy": body.policy.model_dump(by_alias=True),
        "requiredEvidence": body.required_evidence or None,
        "expiresInSeconds": body.expires_in_seconds,
        "metadata": {
            **body.metadata,
            "source": "x402_secure_internal_facilitator",
            "facilitator": (
                body.facilitator.model_dump(exclude_none=True) if body.facilitator else None
            ),
        },
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    return await post_trustline_validation("verifiable-intent/evidence-session", payload)


@internal_router.post(
    "/evidence",
    dependencies=[Depends(require_internal_auth)],
)
async def record_facilitator_evidence(body: InternalEvidenceUploadRequest) -> Dict[str, Any]:
    return await post_trustline_validation(
        "verifiable-intent/evidence",
        body.model_dump(by_alias=True, exclude_none=True),
    )


@internal_router.post(
    "/receipt",
    dependencies=[Depends(require_internal_auth)],
)
async def record_facilitator_receipt(body: InternalReceiptRequest) -> Dict[str, Any]:
    payload = body.model_dump(by_alias=True, exclude_none=True)
    payload.setdefault("metadata", {})
    payload["metadata"] = {
        **payload["metadata"],
        "source": "x402_secure_internal_facilitator",
        "receivedAt": datetime.now(timezone.utc).isoformat(),
    }
    return await post_trustline_validation("verifiable-intent-receipt", payload)
