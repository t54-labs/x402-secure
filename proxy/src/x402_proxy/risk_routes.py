# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import os
import uuid as _uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, Optional
import json

import httpx
from cachetools import TTLCache
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


router = APIRouter(prefix="/risk", tags=["risk-public"])


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


class EvaluateRequest(BaseModel):
    sid: str
    tid: Optional[str] = None
    trace_context: TraceContext
    mandate: Optional[MandateMeta] = None
    payment: Optional[Dict[str, Any]] = None


class EvaluateResponse(BaseModel):
    decision: str
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
        self.sessions[sid] = {"agent_did": req.agent_did, "app_id": req.app_id, "device": req.device, "expires_at": exp}
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
            
            # Print agent trace context (like Risk Engine does)
            trace_data = self.traces.get(req.tid, {})
            agent_trace = trace_data.get("agent_trace")
            if agent_trace:
                print("\n" + "="*80)
                print(f"📊 [PROXY LOCAL] Agent Trace Context for tid={req.tid}:")
                print("="*80)
                print(f"  Task: {agent_trace.get('task')}")
                print(f"  Parameters: {agent_trace.get('parameters')}")
                print(f"  Environment: {agent_trace.get('environment')}")
                
                # Print model config if present
                model_config = agent_trace.get('model_config')
                if model_config:
                    print(f"  Model Config:")
                    print(f"    Provider: {model_config.get('provider')}")
                    print(f"    Model: {model_config.get('model')}")
                    print(f"    Tools: {', '.join(model_config.get('tools_enabled', []))}")
                
                # Print session context if present
                session_ctx = agent_trace.get('session_context')
                if session_ctx:
                    print(f"  Session Context:")
                    print(f"    Session ID: {session_ctx.get('session_id')}")
                    print(f"    Request ID: {session_ctx.get('request_id')}")
                    print(f"    Agent DID: {session_ctx.get('agent_did')}")
                    if 'client_ip_hash' in session_ctx:
                        print(f"    Client IP (hashed): {session_ctx['client_ip_hash'][:16]}...")
                
                events = agent_trace.get('events', [])
                print(f"  Events: {len(events)} total")
                
                # Show user inputs
                user_inputs = [e for e in events if e.get('type') == 'user_input']
                if user_inputs:
                    print(f"\n  👤 User Inputs ({len(user_inputs)} items):")
                    for i, evt in enumerate(user_inputs, 1):
                        content = evt.get('content', '')[:80]
                        print(f"    {i}. {content}{'...' if len(evt.get('content', '')) > 80 else ''}")
                
                # Show agent outputs
                agent_outputs = [e for e in events if e.get('type') == 'agent_output']
                if agent_outputs:
                    print(f"\n  🤖 Agent Outputs ({len(agent_outputs)} items):")
                    for i, evt in enumerate(agent_outputs, 1):
                        content = evt.get('content', '')[:80]
                        print(f"    {i}. {content}{'...' if len(evt.get('content', '')) > 80 else ''}")
                
                if events:
                    print("\n  Recent events:")
                    for i, evt in enumerate(events[-5:], 1):
                        print(f"    {i}. {evt.get('type')}: {evt.get('name', 'N/A')}")
                print("="*80 + "\n")
        
        # Simple allow decision for local testing
        return EvaluateResponse(
            decision="allow",
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
        
        # Log input payload
        print("\n" + "="*80)
        print("📥 [RISK] POST /risk/session - RAW Input Payload (JSON):")
        print("="*80)
        print(json.dumps(req.model_dump(), indent=2, ensure_ascii=False))
        print("="*80 + "\n")
        
        result = _STORE.create_session(req)
        
        # Log output
        print(f"✅ [RISK] Session created: sid={result.sid}")
        print(f"   Expires at: {result.expires_at}\n")
        
        return result
    
    # ============================================================================
    # Forward Mode: Proxy to external risk engine
    # ============================================================================
    base = _risk_engine_url()
    path = "/risk/session"
    payload = req.model_dump()
    
    # Apply compatibility layer if enabled (e.g., Trustline API)
    if _external_compat_enabled():
        before = payload.copy()
        payload = _adapt_payload_for_external_api(payload, path)
        print(f"[RISK FORWARD] compat=on path={path}")
        print(f"  before: {json.dumps(before, ensure_ascii=False)}")
        print(f"  after : {json.dumps(payload, ensure_ascii=False)}")
    
    # Forward request to external risk engine
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"{base}{path}", json=payload, headers=_auth_headers())
    
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    
    ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise HTTPException(status_code=502, detail="invalid content-type from risk engine")
    
    return JSONResponse(status_code=200, content=r.json())


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
        
        # Log input payload
        print("\n" + "="*80)
        print("📥 [RISK] POST /risk/trace - RAW Input Payload (JSON):")
        print("="*80)
        print(json.dumps(req.model_dump(), indent=2, ensure_ascii=False))
        print("="*80)
        
        # Log formatted summary for readability
        print("\n" + "="*80)
        print("📊 [RISK] /risk/trace - Formatted Summary:")
        print("="*80)
        print(f"  sid: {req.sid}")
        print(f"  fingerprint: {req.fingerprint}")
        print(f"  telemetry: {req.telemetry}")
        
        agent_trace = req.agent_trace
        if agent_trace:
            print(f"\n  📊 Agent Trace:")
            print(f"    task: {agent_trace.get('task')}")
            print(f"    parameters: {agent_trace.get('parameters')}")
            print(f"    environment: {agent_trace.get('environment')}")
            
            # Model config
            model_cfg = agent_trace.get('model_config')
            if model_cfg:
                print(f"    model_config:")
                print(f"      provider: {model_cfg.get('provider')}")
                print(f"      model: {model_cfg.get('model')}")
                print(f"      tools_enabled: {model_cfg.get('tools_enabled')}")
            
            # Session context
            session_ctx = agent_trace.get('session_context')
            if session_ctx:
                print(f"    session_context:")
                print(f"      session_id: {session_ctx.get('session_id')}")
                print(f"      request_id: {session_ctx.get('request_id')}")
                print(f"      agent_did: {session_ctx.get('agent_did')}")
            
            # Events summary
            events = agent_trace.get('events', [])
            if events:
                print(f"    events: {len(events)} total")
                event_types = {}
                for e in events:
                    etype = e.get('type', 'unknown')
                    event_types[etype] = event_types.get(etype, 0) + 1
                for etype, count in event_types.items():
                    print(f"      - {etype}: {count}")
        
        print("="*80 + "\n")
        
        result = _STORE.create_trace(req)
        
        # Log output
        print(f"✅ [RISK] Trace created: tid={result.tid}")
        print(f"   Linked to sid={req.sid}\n")
        
        return result
    
    # ============================================================================
    # Forward Mode: Proxy to external risk engine
    # ============================================================================
    base = _risk_engine_url()
    path = "/risk/trace"
    payload = req.model_dump()
    
    # Apply compatibility layer if enabled (e.g., Trustline API)
    if _external_compat_enabled():
        before = payload.copy()
        payload = _adapt_payload_for_external_api(payload, path)
        print(f"[RISK FORWARD] compat=on path={path}")
        print(f"  before: {json.dumps(before, ensure_ascii=False)}")
        print(f"  after : {json.dumps(payload, ensure_ascii=False)}")
    
    # Forward request to external risk engine
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"{base}{path}", json=payload, headers=_auth_headers())
    
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    
    ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise HTTPException(status_code=502, detail="invalid content-type from risk engine")
    
    # Adapt response: Trustline uses "trace_id", we use "tid"
    response_data = r.json()
    if _external_compat_enabled() and "trace_id" in response_data and "tid" not in response_data:
        response_data["tid"] = response_data["trace_id"]
    
    return JSONResponse(status_code=200, content=response_data)


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
        
        # Log input payload
        print("\n" + "="*80)
        print("📥 [RISK] POST /risk/evaluate - RAW Input Payload (JSON):")
        print("="*80)
        print(json.dumps(req.model_dump(), indent=2, ensure_ascii=False))
        print("="*80)
        
        # Log formatted summary
        print("\n" + "="*80)
        print("📊 [RISK] /risk/evaluate - Formatted Summary:")
        print("="*80)
        print(f"  sid: {req.sid}")
        print(f"  tid: {req.tid}")
        print(f"  trace_context:")
        print(f"    tp (traceparent): {req.trace_context.tp}")
        if req.trace_context.ts:
            print(f"    ts (tracestate): {req.trace_context.ts[:80]}...")
        if req.mandate:
            print(f"  mandate:")
            print(f"    ref: {req.mandate.ref}")
            print(f"    sha256: {req.mandate.sha256_b64url}")
            print(f"    size: {req.mandate.size} bytes")
        if req.payment:
            print(f"  payment: {req.payment}")
        print("="*80 + "\n")
        
        result = _STORE.evaluate(req)
        
        # Log output
        print(f"✅ [RISK] Evaluation complete: decision={result.decision}")
        print(f"   decision_id={result.decision_id}\n")
        
        return result
    
    # ============================================================================
    # Forward Mode: Proxy to external risk engine
    # ============================================================================
    base = _risk_engine_url()
    path = "/risk/evaluate"
    payload = req.model_dump()
    
    # Apply compatibility layer if enabled (e.g., Trustline API)
    if _external_compat_enabled():
        before = payload.copy()
        payload = _adapt_payload_for_external_api(payload, path)
        print(f"[RISK FORWARD] compat=on path={path}")
        print(f"  before: {json.dumps(before, ensure_ascii=False)}")
        print(f"  after : {json.dumps(payload, ensure_ascii=False)}")
    
    # Forward request to external risk engine
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"{base}{path}", json=payload, headers=_auth_headers())
    
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    
    ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise HTTPException(status_code=502, detail="invalid content-type from risk engine")
    
    return JSONResponse(status_code=200, content=r.json())


@router.get("/trace/{tid}")
async def get_trace(tid: str):
    """View stored agent trace context (local mode only)"""
    global _STORE
    if _local_enabled():
        if _STORE is None or tid not in _STORE.traces:
            raise HTTPException(status_code=404, detail="tid not found")
        return JSONResponse(content=_STORE.traces[tid])
    raise HTTPException(status_code=501, detail="Not available in forward mode")
