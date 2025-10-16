# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import base64
import json
from typing import Any, Dict, Optional
from urllib.parse import quote

from opentelemetry.propagate import inject


def build_payment_secure_header(agent_trace_context: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    carrier: Dict[str, str] = {}
    inject(carrier)
    tp = carrier.get("traceparent")
    if not tp:
        raise RuntimeError("No active span context; cannot build X-PAYMENT-SECURE")

    ts = carrier.get("tracestate")
    if agent_trace_context is not None:
        agent_trace_json = json.dumps(agent_trace_context, separators=(",", ":"), ensure_ascii=False)
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

