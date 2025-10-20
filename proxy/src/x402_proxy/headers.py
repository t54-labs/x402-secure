# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import re
import uuid
from typing import Dict, Optional, Tuple

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_HEX16 = re.compile(r"^[0-9a-f]{16}$")
_HEX2 = re.compile(r"^[0-9a-f]{2}$")


class HeaderError(ValueError):
    pass


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise HeaderError(msg)


def parse_x_payment_secure(value: str) -> Dict[str, str]:
    """Parse X-PAYMENT-SECURE header.

    Format: 'w3c.v1;tp=<traceparent>[;ts=<url-encoded-tracestate>]'
    Fail fast on any deviation.
    """
    if len(value) > 4096:
        raise HeaderError("X-PAYMENT-SECURE too large")
    parts = [p.strip() for p in value.split(";") if p.strip()]
    _require(parts and parts[0] == "w3c.v1", "Unsupported X-PAYMENT-SECURE version")
    kv: Dict[str, str] = {}
    for p in parts[1:]:
        if "=" not in p:
            raise HeaderError("Malformed X-PAYMENT-SECURE segment")
        k, v = p.split("=", 1)
        kv[k] = v
    tp = kv.get("tp")
    _require(tp is not None, "traceparent (tp) required")
    _validate_traceparent(tp)
    ts = kv.get("ts")
    return {"tp": tp, "ts": ts} if ts else {"tp": tp}


def _validate_traceparent(tp: str) -> None:
    # 00-<32hex>-<16hex>-<2hex>
    parts = tp.split("-")
    _require(len(parts) == 4, "traceparent format invalid")
    v, trace_id, span_id, flags = parts
    _require(v == "00", "traceparent version must be 00")
    _require(_HEX32.match(trace_id) is not None, "trace_id invalid")
    _require(_HEX16.match(span_id) is not None, "span_id invalid")
    _require(_HEX2.match(flags) is not None, "flags invalid")
    _require(trace_id != "0" * 32, "trace_id cannot be all zeros")
    _require(span_id != "0" * 16, "span_id cannot be all zeros")


def parse_x_ap2_evidence(value: str) -> Dict[str, str]:
    """Parse X-AP2-EVIDENCE (mandate only).

    Format: 'evd.v1;mr=<ref>;ms=<b64url_sha256>;mt=application/json;sz=<bytes>'
    """
    if len(value) > 2048:
        raise HeaderError("X-AP2-EVIDENCE too large")
    parts = [p.strip() for p in value.split(";") if p.strip()]
    _require(parts and parts[0] == "evd.v1", "Unsupported X-AP2-EVIDENCE version")
    kv: Dict[str, str] = {}
    for p in parts[1:]:
        if "=" not in p:
            raise HeaderError("Malformed X-AP2-EVIDENCE segment")
        k, v = p.split("=", 1)
        kv[k] = v
    mr = kv.get("mr")
    ms = kv.get("ms")
    mt = kv.get("mt")
    sz = kv.get("sz")
    _require(
        mr is not None and ms is not None and mt is not None and sz is not None,
        "Missing required evidence keys",
    )
    _require(mt == "application/json", "mt must be application/json")
    _require(sz.isdigit(), "sz must be decimal size")
    return {"mr": mr, "ms": ms, "mt": mt, "sz": sz}


def parse_risk_ids(
    x_risk_session: Optional[str],
    x_risk_trace: Optional[str],
) -> Tuple[str, Optional[str]]:
    sid = _require_uuid(x_risk_session, name="X-RISK-SESSION")
    tid = _require_uuid_optional(x_risk_trace, name="X-RISK-TRACE")
    return sid, tid


def _require_uuid(value: Optional[str], *, name: str) -> str:
    if not value:
        raise HeaderError(f"{name} required")
    try:
        u = uuid.UUID(value)
    except Exception as e:
        raise HeaderError(f"{name} invalid: {e}")
    _require(u.version in (1, 4), f"{name} must be UUID v1 or v4")
    return str(u)


def _require_uuid_optional(value: Optional[str], *, name: str) -> Optional[str]:
    if value is None or value == "":
        return None
    _ = _require_uuid(value, name=name)
    return value
