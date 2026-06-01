# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import base64
import hashlib
import json
import re
from copy import deepcopy
from typing import Any, Dict, Optional
from urllib.parse import quote

from opentelemetry.propagate import inject

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def _canonical_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _normalize_sha256(value: str) -> str:
    digest = value.removeprefix("sha256:")
    if not _SHA256_HEX.match(digest):
        raise ValueError("sha256 must be 64 lowercase hex characters")
    return f"sha256:{digest}"


def build_verifiable_intent_header(
    *,
    reference: str,
    sha256_digest: Optional[str] = None,
    evidence: Any = None,
    media_type: str = "application/json",
    size: Optional[int] = None,
) -> Dict[str, str]:
    """Build the X-VERIFIABLE-INTENT reference header."""
    if not reference:
        raise ValueError("reference is required")
    if sha256_digest is None:
        if evidence is None:
            raise ValueError("sha256_digest or evidence is required")
        evidence_bytes = _canonical_bytes(evidence)
        sha256_digest = hashlib.sha256(evidence_bytes).hexdigest()
        if size is None:
            size = len(evidence_bytes)
    normalized_hash = _normalize_sha256(sha256_digest)
    if size is None:
        raise ValueError("size is required when evidence is not provided")
    value = f"vi.v1;ref={reference};sha256={normalized_hash};mt={media_type};sz={int(size)}"
    if len(value) > 4096:
        raise ValueError("X-VERIFIABLE-INTENT exceeds 4096 bytes")
    return {"X-VERIFIABLE-INTENT": value}


def build_ap2_evidence_header(
    *,
    mandate_reference: str,
    mandate_sha256_b64url: str,
    media_type: str = "application/json",
    size: int,
) -> Dict[str, str]:
    """Build the X-AP2-EVIDENCE mandate reference header."""
    if not mandate_reference:
        raise ValueError("mandate_reference is required")
    if not mandate_sha256_b64url:
        raise ValueError("mandate_sha256_b64url is required")
    value = (
        f"evd.v1;mr={mandate_reference};ms={mandate_sha256_b64url};"
        f"mt={media_type};sz={int(size)}"
    )
    if len(value) > 2048:
        raise ValueError("X-AP2-EVIDENCE exceeds 2048 bytes")
    return {"X-AP2-EVIDENCE": value}


def attach_verifiable_intent_policy(
    payment_requirements: Dict[str, Any],
    policy: Dict[str, Any],
) -> Dict[str, Any]:
    """Return a copy of paymentRequirements with policy merged into extra.vi."""
    out = deepcopy(payment_requirements)
    extra = dict(out.get("extra") or {})
    current_policy = dict(extra.get("vi") or {})
    current_policy.update(policy)
    extra["vi"] = current_policy
    out["extra"] = extra
    return out


def build_payment_secure_header(
    agent_trace_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    carrier: Dict[str, str] = {}
    inject(carrier)
    tp = carrier.get("traceparent")
    if not tp:
        raise RuntimeError("No active span context; cannot build X-PAYMENT-SECURE")

    ts = carrier.get("tracestate")
    if agent_trace_context is not None:
        agent_trace_json = json.dumps(
            agent_trace_context, separators=(",", ":"), ensure_ascii=False
        )
        agent_trace_b64 = base64.b64encode(agent_trace_json.encode()).decode()
        ts = quote(agent_trace_b64, safe="")

    value = f"w3c.v1;tp={tp}" + (f";ts={ts}" if ts else "")
    if len(value) > 4096:
        raise RuntimeError("X-PAYMENT-SECURE exceeds 4096 bytes")
    return {"X-PAYMENT-SECURE": value}


def start_client_span(name: str):
    from opentelemetry import trace

    tracer = trace.get_tracer("x402_agent.buyer")
    return tracer.start_as_current_span(name)
