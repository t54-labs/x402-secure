# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import logging
import os
import uuid as _uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, Optional, Union

import httpx
from cachetools import TTLCache
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/risk", tags=["risk-public"])
logger = logging.getLogger(__name__)


class RiskSessionRequest(BaseModel):
    # TODO: Add EIP-8004 DID format support
    # Currently accepts wallet address (0x...), future: did:eip8004:chain:contract:tokenId
    # EIP-8004 enables decentralized AI agent identity via ERC-721 tokens
    agent_did: str
    app_id: Optional[str] = None
    device: Optional[Dict[str, Any]] = None


class RiskSessionResponse(BaseModel):
    sid: str
    expires_at: str


class RiskTraceRequest(BaseModel):
    sid: str
    fingerprint: Optional[Dict[str, Any]] = None
    telemetry: Optional[Dict[str, Any]] = None
    agent_trace: Optional[Dict[str, Any]] = None


class RiskTraceResponse(BaseModel):
    tid: str


class TraceContext(BaseModel):
    tp: str
    ts: Optional[str] = None


class MandateMeta(BaseModel):
    ref: str
    sha256_b64url: str
    mime: str
    size: int


class RiskLevel(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class Decision(str, Enum):
    allow = "allow"
    deny = "deny"
    review = "review"


class PaymentContext(BaseModel):
    """
    Generic payment context supporting multiple protocols.
    protocol: Protocol identifier (e.g., "eip3009", "x402:exact", "solana:transfer", "btc:psbt")
    """

    protocol: str = Field(..., description="Payment protocol identifier")
    version: Optional[Union[str, int]] = Field(None, description="Protocol version")
    network: Optional[str] = Field(
        None, description="Network identifier (e.g., 'base-sepolia', 'solana-mainnet')"
    )
    payload: Dict[str, Any] = Field(default_factory=dict, description="Protocol-specific payload")
    headers: Optional[Dict[str, str]] = Field(None, description="Transport/binding related headers")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class EvaluateRequest(BaseModel):
    sid: str
    trace_context: TraceContext
    payment: PaymentContext
    tid: Optional[str] = None
    mandate: Optional[MandateMeta] = None


class EvaluateResponse(BaseModel):
    decision: Decision
    reasons: list[str] = Field(default_factory=list)
    decision_id: str
    ttl_seconds: int = 300
    used_mandate: bool = False
    warnings: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.low
    extra: Dict[str, Any] = Field(default_factory=dict)


def _risk_engine_url() -> str:
    url = os.getenv("RISK_ENGINE_URL")
    if not url:
        raise HTTPException(status_code=500, detail="RISK_ENGINE_URL not configured")
    return url.rstrip("/")


def _auth_headers() -> Dict[str, str]:
    token = os.getenv("RISK_INTERNAL_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _external_compat_enabled() -> bool:
    compat = os.getenv("RISK_ENGINE_COMPAT", "")
    return compat.lower() in {"trustline", "tl", "on", "true", "1"}


def _adapt_payload_for_external_api(payload: Dict[str, Any], endpoint: str) -> Dict[str, Any]:
    """Adapt payload for external Risk Engine compatibility.

    - Session: agent_did -> agent_id, ensure device exists
    - Trace: fingerprint/telemetry dict -> JSON string (Trustline requires string|null)
    """
    p: Dict[str, Any] = dict(payload)

    # Session create
    if endpoint.endswith("/risk/session"):
        if "agent_did" in p:
            p["agent_id"] = p.pop("agent_did")
        if "agent_id" in p and "device" not in p:
            p["device"] = {"ua": "x402-proxy"}

    # Trace create
    if endpoint.endswith("/risk/trace"):
        for k in ("fingerprint", "telemetry"):
            v = p.get(k)
            if isinstance(v, dict):
                p[k] = json.dumps(v, ensure_ascii=False)

    return p


class _LocalStore:
    def __init__(self) -> None:
        ttl = int(os.getenv("PROXY_LOCAL_RISK_TTL", "900"))
        self.sessions: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=10000, ttl=ttl)
        self.traces: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=10000, ttl=ttl)

    def create_session(self, req: RiskSessionRequest) -> RiskSessionResponse:
        sid = str(_uuid.uuid4())
        exp = datetime.utcnow() + timedelta(seconds=self.sessions.ttl or 900)  # type: ignore[attr-defined]
        self.sessions[sid] = {
            "agent_did": req.agent_did,
            "app_id": req.app_id,
            "device": req.device,
            "expires_at": exp,
        }
        return RiskSessionResponse(sid=sid, expires_at=exp.isoformat() + "Z")

    def create_trace(self, req: RiskTraceRequest) -> RiskTraceResponse:
        if req.sid not in self.sessions:
            raise HTTPException(status_code=404, detail="unknown sid")
        tid = str(_uuid.uuid4())
        self.traces[tid] = {
            "sid": req.sid,
            "fingerprint": req.fingerprint,
            "telemetry": req.telemetry,
            "agent_trace": req.agent_trace,
        }
        return RiskTraceResponse(tid=tid)

    def evaluate(self, req: EvaluateRequest) -> EvaluateResponse:
        # Validate sid exists
        if req.sid not in self.sessions:
            raise HTTPException(status_code=404, detail="unknown sid")
        # Validate tid linkage if provided
        if req.tid:
            if req.tid not in self.traces:
                raise HTTPException(status_code=404, detail="unknown tid")
            if self.traces[req.tid]["sid"] != req.sid:
                raise HTTPException(status_code=400, detail="tid not linked to sid")

            # Log agent trace context
            trace_data = self.traces.get(req.tid, {})
            agent_trace = trace_data.get("agent_trace")
            if agent_trace:
                task = agent_trace.get("task", "N/A")
                events = agent_trace.get("events", [])

                # Count event types
                event_summary = {}
                for e in events:
                    etype = e.get("type", "unknown")
                    event_summary[etype] = event_summary.get(etype, 0) + 1

                # Log summary
                model_config = agent_trace.get("model_config", {})
                logger.info(
                    f"[LOCAL] Agent trace context: tid={req.tid}, task={task}, "
                    f"model={model_config.get('provider')}/{model_config.get('model')}, "
                    f"events={len(events)} ({event_summary})"
                )

        # Log payment context (required field)
        logger.info(f"[LOCAL] Payment context: {req.payment}")
        logger.info(
            f"[LOCAL] Payment context: protocol={req.payment.protocol}, "
            f"network={req.payment.network}, version={req.payment.version}"
        )

        # Simple allow decision for local testing
        return EvaluateResponse(
            decision=Decision.allow,
            reasons=[],
            decision_id=str(_uuid.uuid4()),
            ttl_seconds=300,
            used_mandate=bool(req.mandate),
            warnings=[],
            risk_level=RiskLevel.low,
            extra={},
        )


def _local_enabled() -> bool:
    return os.getenv("PROXY_LOCAL_RISK", "0").lower() in {"1", "true", "yes"}


_STORE: Optional[_LocalStore] = None


@router.post("/session", response_model=RiskSessionResponse)
async def create_session(req: RiskSessionRequest):
    """Create a risk session. Supports local storage or forwarding to external risk engine."""
    global _STORE

    # ============================================================================
    # Local Mode: Store session in memory
    # ============================================================================
    if _local_enabled():
        if _STORE is None:
            _STORE = _LocalStore()

        result = _STORE.create_session(req)
        logger.info(f"[LOCAL] Session created: sid={result.sid}, expires_at={result.expires_at}")

        return result

    # ============================================================================
    # Forward Mode: Proxy to external risk engine
    # ============================================================================
    base = _risk_engine_url()
    path = "/risk/session"
    payload = req.model_dump()

    # Apply compatibility layer if enabled (e.g., Trustline API)
    if _external_compat_enabled():
        payload = _adapt_payload_for_external_api(payload, path)

    # Forward request to external risk engine
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"{base}{path}", json=payload, headers=_auth_headers())

    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text)

    ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise HTTPException(status_code=502, detail="invalid content-type from risk engine")

    # Validate response structure
    try:
        response_data = r.json()
        validated = RiskSessionResponse(**response_data)
        return validated
    except Exception as e:
        logger.error(f"Risk engine response validation failed: {e}, response: {response_data}")
        raise HTTPException(status_code=502, detail=f"Invalid response from risk engine: {str(e)}")


@router.post("/trace", response_model=RiskTraceResponse)
async def create_trace(req: RiskTraceRequest):
    """Create a trace record linked to a session."""
    global _STORE

    # ============================================================================
    # Local Mode: Store trace in memory
    # ============================================================================
    if _local_enabled():
        if _STORE is None:
            _STORE = _LocalStore()

        result = _STORE.create_trace(req)

        # Log trace creation with summary
        agent_trace = req.agent_trace
        if agent_trace:
            events = agent_trace.get("events", [])
            event_summary = {}
            for e in events:
                etype = e.get("type", "unknown")
                event_summary[etype] = event_summary.get(etype, 0) + 1
            logger.info(
                "[LOCAL] Trace created: tid=%s, sid=%s, events=%d (%s)",
                result.tid,
                req.sid,
                len(events),
                event_summary,
            )
        else:
            logger.info(f"[LOCAL] Trace created: tid={result.tid}, sid={req.sid}")

        return result

    # ============================================================================
    # Forward Mode: Proxy to external risk engine
    # ============================================================================
    base = _risk_engine_url()
    path = "/risk/trace"
    payload = req.model_dump()

    # Apply compatibility layer if enabled (e.g., Trustline API)
    if _external_compat_enabled():
        payload = _adapt_payload_for_external_api(payload, path)

    # Forward request to external risk engine
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"{base}{path}", json=payload, headers=_auth_headers())

    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text)

    ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise HTTPException(status_code=502, detail="invalid content-type from risk engine")

    # Validate response structure
    try:
        response_data = r.json()
        # risk engine returns trace_id, we use tid
        if "trace_id" in response_data and "tid" not in response_data:
            response_data["tid"] = response_data["trace_id"]

        validated = RiskTraceResponse(**response_data)
        return validated
    except Exception as e:
        logger.error(f"Risk engine response validation failed: {e}, response: {response_data}")
        raise HTTPException(status_code=502, detail=f"Invalid response from risk engine: {str(e)}")


@router.post("/evaluate", response_model=EvaluateResponse)
async def evaluate_risk(req: EvaluateRequest):
    """Evaluate risk for a payment request."""
    global _STORE

    # ============================================================================
    # Local Mode: Evaluate risk locally
    # ============================================================================
    if _local_enabled():
        if _STORE is None:
            _STORE = _LocalStore()

        result = _STORE.evaluate(req)

        # Log evaluation result
        logger.info(
            f"[LOCAL] Evaluation: decision={result.decision}, sid={req.sid}, tid={req.tid}, "
            f"decision_id={result.decision_id}, mandate={bool(req.mandate)}"
        )

        return result

    # ============================================================================
    # Forward Mode: Proxy to external risk engine
    # ============================================================================
    base = _risk_engine_url()
    path = "/risk/evaluate"
    payload = req.model_dump()

    # Apply compatibility layer if enabled (e.g., Trustline API)
    if _external_compat_enabled():
        payload = _adapt_payload_for_external_api(payload, path)

    # Forward request to external risk engine
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"{base}{path}", json=payload, headers=_auth_headers())

    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text)

    ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise HTTPException(status_code=502, detail="invalid content-type from risk engine")

    # Validate response structure
    try:
        response_data = r.json()
        validated = EvaluateResponse(**response_data)
        return validated
    except Exception as e:
        logger.error(f"Risk engine response validation failed: {e}, response: {response_data}")
        raise HTTPException(status_code=502, detail=f"Invalid response from risk engine: {str(e)}")


@router.get("/trace/{tid}")
async def get_trace(tid: str):
    """View stored agent trace context (local mode only)"""
    global _STORE
    if _local_enabled():
        if _STORE is None or tid not in _STORE.traces:
            raise HTTPException(status_code=404, detail="tid not found")
        return JSONResponse(content=_STORE.traces[tid])
    raise HTTPException(status_code=501, detail="Not available in forward mode")
