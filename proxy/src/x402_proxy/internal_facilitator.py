# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import secrets
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Literal, Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .vi_receipt_store import (
    VIReceiptStoreUnavailable,
    lookup_vi_receipt_statuses,
    record_vi_decision_snapshot,
    record_vi_receipt_snapshot,
)

logger = logging.getLogger(__name__)

DEFAULT_TRUSTLINE_VERIFY_CHAIN_PATH = "verifiable-intent/verify-chain"
DEFAULT_TRUSTLINE_RETRY_AFTER_SECONDS = 30

_correlation_id_ctx: ContextVar[str] = ContextVar("trustline_correlation_id", default="")

internal_router = APIRouter(
    prefix="/internal/x402-secure/facilitator",
    tags=["x402-secure-internal-facilitator"],
)


def _retry_after_seconds(value: Any) -> int:
    """Seconds to wait from a Retry-After value: delta-seconds or an HTTP-date (RFC 9110)."""
    if value is None:
        return DEFAULT_TRUSTLINE_RETRY_AFTER_SECONDS
    text = str(value).strip()
    try:
        return max(0, int(text))
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return DEFAULT_TRUSTLINE_RETRY_AFTER_SECONDS
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    return max(0, math.ceil((retry_at - datetime.now(timezone.utc)).total_seconds()))


def _trustline_transient_response(
    *,
    correlation_id: str,
    retry_after: int,
) -> JSONResponse:
    return JSONResponse(
        {
            "reason": "trustline_unavailable",
            "retryable": True,
            "retryAfter": retry_after,
            "correlationId": correlation_id,
            "phase": "verify",
            "settlementAttempted": False,
        },
        status_code=503,
        headers={
            "Retry-After": str(retry_after),
            "X-Correlation-ID": correlation_id,
        },
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

    payment_mandate: Optional[Dict[str, Any]] = Field(default=None, alias="paymentMandate")

    intent_mandate_ref: Optional[str] = Field(default=None, alias="intentMandateRef")
    cart_mandate_ref: Optional[str] = Field(default=None, alias="cartMandateRef")
    payment_mandate_ref: Optional[str] = Field(default=None, alias="paymentMandateRef")
    intent_mandate_hash: Optional[str] = Field(default=None, alias="intentMandateHash")
    cart_mandate_hash: Optional[str] = Field(default=None, alias="cartMandateHash")
    payment_mandate_hash: Optional[str] = Field(default=None, alias="paymentMandateHash")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TraceReferenceSet(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    agent_trace: Optional[Dict[str, Any]] = Field(default=None, alias="agentTrace")

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
    amount_unit: Optional[Literal["xrp", "issued", "drops"]] = Field(
        default=None, alias="amountUnit"
    )
    environment: Optional[str] = None
    currency: Optional[str] = None
    destination: Optional[str] = None
    pay_to: Optional[str] = Field(default=None, alias="payTo")
    destination_tag: Optional[int] = Field(
        default=None, alias="destinationTag", ge=0, le=4294967295
    )
    payer: Optional[str] = None
    payer_wallet: Optional[str] = Field(default=None, alias="payerWallet")
    wallet_address: Optional[str] = Field(default=None, alias="walletAddress")
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
    invoice_id: Optional[str] = Field(default=None, alias="invoiceId")


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
    correlation: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ViReceiptLookupTransaction(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    hash: Optional[str] = None
    invoice_id: Optional[str] = Field(default=None, alias="invoiceId")
    raw_memo: Optional[str] = Field(default=None, alias="rawMemo")
    transaction_hash: Optional[str] = Field(default=None, alias="transactionHash")


class ViReceiptLookupRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    transactions: List[ViReceiptLookupTransaction] = Field(default_factory=list)


def _configured_internal_token() -> Optional[str]:
    return (
        os.getenv("X402_SECURE_INTERNAL_TOKEN")
        # MF-2 (code review): the XRPL facilitator caller sends its token from
        # X402_SECURE_INTERNAL_AUTH_TOKEN — accept that name too so the two services don't
        # disagree (else an operator is tempted to flip X402_SECURE_INTERNAL_AUTH_DISABLED).
        or os.getenv("X402_SECURE_INTERNAL_AUTH_TOKEN")
        or os.getenv("FACILITATOR_INTERNAL_TOKEN")
        or os.getenv("INTERNAL_FACILITATOR_TOKEN")
    )


_INTERNAL_ACTIONS = {
    "evaluate": "evaluate",
    "evidence-session": "evidence_session",
    "evidence": "evidence_upload",
    "receipt": "receipt_write",
    "lookup": "receipt_lookup",
}


class InternalServiceScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, repr=False)
    trustline_token: Optional[str] = Field(default=None, min_length=1, repr=False)
    organization: Optional[str] = Field(default=None, min_length=1)
    environment: Optional[str] = Field(default=None, min_length=1)
    policy: Optional[str] = Field(default=None, min_length=1)
    policy_version: Optional[str] = Field(default=None, min_length=1)
    actions: set[
        Literal[
            "evaluate", "evidence_session", "evidence_upload", "receipt_write", "receipt_lookup"
        ]
    ]

    @property
    def receipt_namespace(self) -> str:
        return _fingerprint([self.organization, self.environment, self.policy, self.policy_version])


_service_scope: ContextVar[Optional[InternalServiceScope]] = ContextVar(
    "internal_service_scope", default=None
)


def _internal_service_scopes() -> list[InternalServiceScope]:
    try:
        if _trustline_token_by_org() or _trustline_token_by_policy():
            raise ValueError("Selector-based credential mapping is unsupported")
        configured = os.getenv("X402_SECURE_INTERNAL_SERVICES_JSON")
        if configured is not None:
            registry = json.loads(configured)
            if not isinstance(registry, dict) or not registry:
                raise ValueError("Expected a nonempty service registry")
            scopes = [InternalServiceScope.model_validate(value) for value in registry.values()]
            inbound = set()
            credentials = {}
            for scope in scopes:
                if (
                    not scope.organization
                    or not scope.environment
                    or not scope.policy
                    or not scope.actions
                ):
                    raise ValueError(
                        "Each service needs an organization, environment, policy and actions"
                    )
                if scope.token in inbound or (
                    scope.actions != {"receipt_lookup"} and not scope.trustline_token
                ):
                    raise ValueError("Invalid service credential assignment")
                inbound.add(scope.token)
                if scope.trustline_token:
                    prior = credentials.setdefault(scope.trustline_token, scope.receipt_namespace)
                    if prior != scope.receipt_namespace:
                        raise ValueError("A credential cannot span service scopes")
            return scopes
        expected = _configured_internal_token()
        if not expected:
            raise ValueError("Single-service auth token is missing")
        scope = InternalServiceScope(
            token=expected,
            trustline_token=os.getenv("X402_SECURE_INTERNAL_TRUSTLINE_TOKEN"),
            organization=os.getenv("X402_SECURE_INTERNAL_ORGANIZATION"),
            environment=os.getenv("X402_SECURE_INTERNAL_ENVIRONMENT"),
            policy=os.getenv("X402_SECURE_INTERNAL_POLICY"),
            policy_version=os.getenv("X402_SECURE_INTERNAL_POLICY_VERSION"),
            actions=set(
                os.getenv(
                    "X402_SECURE_INTERNAL_ACTIONS",
                    "evaluate,evidence_session,evidence_upload,receipt_write",
                ).split(",")
            ),
        )
        if scope.actions != {"receipt_lookup"} and not scope.trustline_token:
            raise ValueError("Single-service Trustline credential is missing")
        return [scope]
    except (ValueError, HTTPException) as exc:
        raise HTTPException(
            status_code=503, detail="Invalid internal facilitator service configuration"
        ) from exc


async def require_internal_auth(
    request: Request,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_internal_token: Optional[str] = Header(default=None, alias="X-Internal-Token"),
):
    scopes = _internal_service_scopes()
    supplied = x_internal_token
    if authorization is not None:
        scheme, _, bearer = authorization.partition(" ")
        if scheme.lower() != "bearer" or (supplied and supplied != bearer):
            raise HTTPException(status_code=401, detail="Invalid internal token")
        supplied = bearer
    scope = None
    for candidate in scopes:
        if supplied and secrets.compare_digest(supplied.encode(), candidate.token.encode()):
            scope = candidate
    if scope is None:
        raise HTTPException(status_code=401, detail="Invalid internal token")
    action = _INTERNAL_ACTIONS.get(request.url.path.rsplit("/", 1)[-1])
    if action not in scope.actions:
        raise HTTPException(status_code=403, detail="Internal action outside service scope")
    ctx_token = _service_scope.set(scope)
    try:
        yield
    finally:
        _service_scope.reset(ctx_token)


_ORG_SELECTORS = ("developerOrgId", "developer_org_id", "organizationId", "organization_id")
_POLICY_SELECTORS = ("policyId", "policy_id")
_ENV_SELECTORS = ("environment", "ledgerEnvironment", "ledger_environment")


def _authenticated_scope(payload: Dict[str, Any]) -> InternalServiceScope:
    scope = _service_scope.get()
    if scope is None:
        raise HTTPException(status_code=503, detail="Internal service scope unavailable")
    metadata = payload.get("metadata") or {}
    sources = [
        payload,
        metadata,
        payload.get("payment") or {},
        payload.get("paymentContext") or {},
        metadata.get("facilitator") or {},
        payload.get("correlation") or {},
        _extract_policy(payload),
    ]
    for names, expected in (
        (_ORG_SELECTORS, scope.organization),
        (_POLICY_SELECTORS, scope.policy),
        (("policyVersion", "policy_version"), scope.policy_version),
        (_ENV_SELECTORS, scope.environment),
    ):
        for source in sources:
            for key in names:
                if key in source and source[key] is not None and source[key] != expected:
                    raise HTTPException(
                        status_code=403, detail="Internal selector outside service scope"
                    )
    return scope


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


def _xrpl_amount(value: Any, unit: str) -> Optional[str]:
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0 or not -96 <= amount.adjusted() <= 96:
            return None
        if unit == "drops":
            if amount != amount.to_integral_value():
                return None
            amount /= Decimal(1000000)
        return _decimal_string(amount)
    except (ValueError, InvalidOperation):
        return None


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
    amount_object = payload.get("Amount")
    amount_terms = amount_object if isinstance(amount_object, dict) else {}
    asset, issuer, destination = payment.asset, payment.issuer, payment.destination
    native = asset == "XRP"
    unit = payment.amount_unit
    amount = _xrpl_amount(payment.amount, unit) if unit else None
    resource = _first_present(payment.resource, payment.merchant_origin)
    hashes = {
        "paymentPayloadHash": payment.payload_hash or _fingerprint(payload),
        "paymentRequirementsHash": payment.payment_requirements_hash
        or _fingerprint(payment.payment_requirements),
    }
    if payment.payment_hash:
        hashes["paymentHash"] = payment.payment_hash

    violations: List[Dict[str, Any]] = []
    if not amount or unit not in ({"xrp", "drops"} if native else {"issued"}):
        _add_violation(
            violations,
            "XRPL_AMOUNT_INVALID",
            "Valid XRPL amount and explicit unit are required.",
            "amount",
        )
    if (
        native
        and amount
        and Decimal(amount) * 1000000 != (Decimal(amount) * 1000000).to_integral_value()
    ):
        _add_violation(
            violations, "XRPL_AMOUNT_INVALID", "Native amount must represent whole drops.", "amount"
        )
    if not asset or (native and issuer) or (asset and not native and not issuer):
        _add_violation(
            violations, "XRPL_ASSET_INVALID", "An issued currency requires its issuer.", "asset"
        )
    if not destination:
        _add_violation(
            violations,
            "XRPL_DESTINATION_MISSING",
            "XRPL destination account is required.",
            "destination",
        )
    wrapper_amount = (
        _xrpl_amount(amount_terms.get("value"), "issued")
        if isinstance(amount_object, dict)
        else _xrpl_amount(amount_object, "drops")
    )
    if (
        (
            amount_object is not None
            and (not wrapper_amount or wrapper_amount != amount or bool(amount_terms) == native)
        )
        or any(
            candidate is not None and candidate != asset
            for candidate in (
                payment.currency,
                payload.get("currency"),
                amount_terms.get("currency"),
            )
        )
        or any(
            candidate is not None and candidate != issuer
            for candidate in (
                payload.get("issuer"),
                amount_terms.get("issuer"),
            )
        )
        or any(
            candidate is not None and candidate != destination
            for candidate in (
                payment.pay_to,
                payload.get("Destination"),
            )
        )
        or (
            payload.get("DestinationTag") is not None
            and str(payload["DestinationTag"]) != str(payment.destination_tag)
        )
    ):
        _add_violation(
            violations,
            "XRPL_TERMS_CONFLICT",
            "XRPL sources disagree with the validated payment terms.",
        )

    canonical = {
        "chain": "xrpl",
        "asset": asset,
        "issuer": issuer,
        "amount": amount,
        "amountUnit": "xrp" if native else "issued",
        "destination": destination,
        "destinationTag": payment.destination_tag,
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
    if chain == "xrpl" or chain.startswith("xrpl:"):
        return _build_xrpl_binding(payment)
    return _build_generic_binding(payment)


def _evaluated_payment_binding(
    payment: InternalPaymentContext,
    binding: BindingProfileResult,
) -> Dict[str, Any]:
    """Describe the payment a decision evaluated, for checking the receipt that settles it."""
    return {
        "paymentPayloadHash": binding.hashes.get("paymentPayloadHash"),
        "paymentRequirementsHash": binding.hashes.get("paymentRequirementsHash"),
        "invoiceId": payment.invoice_id,
        "chain": binding.chain,
        "payer": _first_present(payment.payer, payment.payer_wallet, payment.wallet_address),
        "destination": binding.destination,
        "amount": binding.amount,
        "amountUnit": binding.canonical.get("amountUnit"),
        "destinationTag": payment.destination_tag,
        "asset": binding.asset,
        "issuer": binding.issuer,
    }


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


def _trustline_auth_headers(payload: Dict[str, Any], *, scoped: bool) -> Dict[str, str]:
    if scoped:
        token = _authenticated_scope(payload).trustline_token
        if not token:
            raise HTTPException(status_code=503, detail="Scoped Trustline credential is missing")
    else:
        token = _default_trustline_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


async def _post_trustline_validation(
    path: str,
    payload: Dict[str, Any],
    *,
    scoped: bool,
) -> Dict[str, Any]:
    base_url = _trustline_base_url()
    if base_url.endswith("/validation"):
        url = f"{base_url}/{path.lstrip('/')}"
    else:
        url = f"{base_url}{_trustline_validation_path(path)}"
    headers = _trustline_auth_headers(payload, scoped=scoped)
    correlation_id = _correlation_id_ctx.get()
    if correlation_id:
        headers["X-Correlation-ID"] = correlation_id
    try:
        async with httpx.AsyncClient(
            timeout=float(os.getenv("TRUSTLINE_TIMEOUT_S", "15"))
        ) as client:
            response = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError:
        raise
    if response.status_code >= 400:
        retry_after = response.headers.get("Retry-After")
        response_correlation_id = (
            response.headers.get("X-Correlation-ID")
            or response.headers.get("X-Request-ID")
            or correlation_id
        )
        detail = {
            "reason": "trustline_unavailable"
            if response.status_code in {408, 429} or response.status_code >= 500
            else "trustline_policy_denied",
            "upstreamStatus": response.status_code,
            "correlationId": response_correlation_id,
        }
        raise HTTPException(
            status_code=503
            if response.status_code in {408, 429} or response.status_code >= 500
            else response.status_code,
            detail=detail,
            headers={"Retry-After": retry_after} if retry_after else None,
        )
    try:
        return response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Invalid Trustline response: {exc}") from exc


async def post_trustline_validation(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return await _post_trustline_validation(path, payload, scoped=True)


async def post_trustline_validation_default_auth(
    path: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    return await _post_trustline_validation(path, payload, scoped=False)


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
    payer_wallet = _first_present(
        payment.payer_wallet,
        payment.wallet_address,
        payment.payer,
        request.buyer.get("walletAddress"),
        request.buyer.get("wallet_address"),
    )
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
        "environment": payment.environment,
        "asset": binding.asset or payment.asset or payment.currency,
        "issuer": binding.issuer or payment.issuer,
        "amount": binding.amount or payment.amount,
        "amountUnit": binding.canonical.get("amountUnit"),
        "currency": binding.asset,
        "destination": binding.destination or payment.destination or payment.pay_to,
        "destinationTag": payment.destination_tag,
        "invoiceId": payment.invoice_id,
        "merchantId": request.merchant.get("id"),
        "merchantOrigin": merchant_origin,
        "resource": binding.resource or payment.resource,
        "payer": payer_wallet,
        "payerWallet": payer_wallet,
        "walletAddress": payer_wallet,
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
        or payer_wallet
        or f"{request.facilitator.id}:unknown-agent",
        "walletAddress": request.buyer.get("walletAddress")
        or request.buyer.get("wallet_address")
        or payer_wallet,
        "verifiableIntent": vi,
        "verifiableIntentChain": vi_chain,
        "paymentContext": payment_context,
        "binding": binding.model_dump(by_alias=True, exclude_none=True),
        "ap2Context": ap2_context,
        "agentTrace": extension.trace.agent_trace if extension.trace else None,
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
    chain_verified = chain.get("verified") is True
    constraint_ok = (
        chain.get("constraint_satisfied") is None or chain.get("constraint_satisfied") is True
    )
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


def _trustline_assessment_summary(
    trustline_response: Dict[str, Any],
    trustline_path: str,
) -> Dict[str, Any]:
    summary = trustline_response.get("trustline_assessment")
    if isinstance(summary, dict):
        assessment = dict(summary)
    else:
        assessment = {}

    assessment.setdefault("source", trustline_path)
    if trustline_response.get("verifierMode") is not None:
        assessment.setdefault("verifier_mode", trustline_response.get("verifierMode"))
    if trustline_response.get("latencyMs") is not None:
        assessment.setdefault("latency_ms", trustline_response.get("latencyMs"))

    wallet_gate = trustline_response.get("walletGate") or trustline_response.get("wallet_gate")
    if isinstance(wallet_gate, dict):
        assessment.setdefault("wallet_gate", wallet_gate)

    tier2_assessment = trustline_response.get("tier2Assessment") or trustline_response.get(
        "tier2_assessment"
    )
    if isinstance(tier2_assessment, dict):
        assessment.setdefault("tier2_assessment", tier2_assessment)

    decision_id = trustline_response.get("decision_id") or trustline_response.get("decisionId")
    if decision_id is not None:
        assessment.setdefault("decision_id", decision_id)

    return assessment


@internal_router.post(
    "/evaluate",
    response_model=InternalFacilitatorDecisionResponse,
    dependencies=[Depends(require_internal_auth)],
)
async def evaluate_facilitator_payment(
    body: InternalFacilitatorEvaluateRequest,
    request: Request,
) -> Dict[str, Any] | JSONResponse:
    correlation_id = (
        request.headers.get("X-Correlation-ID")
        or request.headers.get("X-Request-ID")
        or uuid.uuid4().hex
    )[:128]
    _correlation_id_ctx.set(correlation_id)
    extension = _extract_extension(body.extensions)
    binding = build_binding_profile(body.payment)
    if binding.chain == "xrpl" and not binding.payment_bound:
        raise HTTPException(status_code=422, detail=binding.violations)
    payload = build_trustline_assessment_payload(body, extension, binding)
    scope = _authenticated_scope(payload)
    trustline_path = _trustline_facilitator_verify_chain_path()
    try:
        trustline_response = await post_trustline_validation(trustline_path, payload)
    except HTTPException as exc:
        if exc.status_code in {408, 429} or exc.status_code >= 500:
            retry_after = _retry_after_seconds(
                (exc.headers or {}).get(
                    "Retry-After",
                    os.getenv(
                        "TRUSTLINE_RETRY_AFTER_SECONDS",
                        str(DEFAULT_TRUSTLINE_RETRY_AFTER_SECONDS),
                    ),
                )
            )
            logger.warning(
                "[INTERNAL] facilitator=%s trustline_verify_chain_transient "
                "status=%s reason=trustline_unavailable retryable=true "
                "retry_after=%s correlation_id=%s",
                body.facilitator.id,
                exc.status_code,
                retry_after,
                correlation_id,
            )
            return _trustline_transient_response(
                correlation_id=correlation_id,
                retry_after=retry_after,
            )
        logger.warning(
            "[INTERNAL] facilitator=%s trustline_verify_chain_denied status=%s correlation_id=%s",
            body.facilitator.id,
            exc.status_code,
            correlation_id,
        )
        return {
            "decision": "deny",
            "decision_id": f"xs_dec_{uuid.uuid4().hex}",
            "risk_level": "high",
            "ttl_seconds": 60,
            "vi": {},
            "binding": binding.model_dump(by_alias=False, exclude_none=True),
            "reasons": ["Trustline policy denied"],
            "warnings": [f"Trustline verification denied with status {exc.status_code}"],
            "trustline_assessment": {
                "source": trustline_path,
                "status": exc.status_code,
                "correlation_id": correlation_id,
            },
        }
    except httpx.HTTPError as exc:
        logger.warning(
            "[INTERNAL] facilitator=%s trustline_verify_chain_transient "
            "error_type=%s reason=trustline_unavailable retryable=true "
            "retry_after=%s correlation_id=%s",
            body.facilitator.id,
            type(exc).__name__,
            DEFAULT_TRUSTLINE_RETRY_AFTER_SECONDS,
            correlation_id,
        )
        retry_after = _retry_after_seconds(
            os.getenv(
                "TRUSTLINE_RETRY_AFTER_SECONDS",
                str(DEFAULT_TRUSTLINE_RETRY_AFTER_SECONDS),
            )
        )
        return _trustline_transient_response(
            correlation_id=correlation_id,
            retry_after=retry_after,
        )

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
    response = {
        "decision": decision,
        "decision_id": decision_id,
        "risk_level": trustline_response.get("risk_level")
        or ("high" if decision == "deny" else "low"),
        "ttl_seconds": trustline_response.get("ttl_seconds", 300),
        "vi": vi_result,
        "binding": trustline_response.get("binding")
        or binding.model_dump(by_alias=False, exclude_none=True),
        "reasons": reasons,
        "warnings": warnings,
        "trustline_assessment": _trustline_assessment_summary(
            trustline_response,
            trustline_path,
        ),
    }
    # The receipt store does blocking I/O; keep it off the event loop.
    await run_in_threadpool(
        record_vi_decision_snapshot,
        response,
        payment_binding=_evaluated_payment_binding(body.payment, binding),
        namespace=scope.receipt_namespace,
    )
    return response


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
    scope = _authenticated_scope(payload)
    response = await post_trustline_validation("verifiable-intent-receipt", payload)
    await run_in_threadpool(
        record_vi_receipt_snapshot, payload, response, namespace=scope.receipt_namespace
    )
    return response


@internal_router.post(
    "/vi-receipts/lookup",
    dependencies=[Depends(require_internal_auth)],
    response_model=None,
)
async def lookup_facilitator_vi_receipts(
    body: ViReceiptLookupRequest,
) -> Dict[str, Any] | JSONResponse:
    scope = _authenticated_scope({})
    transactions = [item.model_dump(by_alias=True, exclude_none=True) for item in body.transactions]
    try:
        items = await run_in_threadpool(
            lookup_vi_receipt_statuses, transactions, namespace=scope.receipt_namespace
        )
    except VIReceiptStoreUnavailable as exc:
        logger.warning("[INTERNAL] vi_receipt_lookup_unavailable reason=%s", exc)
        return JSONResponse(
            {"reason": "vi_receipt_store_unavailable", "retryable": True},
            status_code=503,
            headers={"Retry-After": str(DEFAULT_TRUSTLINE_RETRY_AFTER_SECONDS)},
        )
    return {"items": items}
