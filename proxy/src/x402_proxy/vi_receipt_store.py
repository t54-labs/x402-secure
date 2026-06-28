# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

try:  # pragma: no cover - exercised in production builds when redis is installed.
    import redis  # type: ignore
except Exception:  # pragma: no cover
    redis = None  # type: ignore

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60
KEY_PREFIX = "x402secure:vi-receipts"


class _TTLStore:
    def get(self, key: str) -> Optional[str]:
        raise NotImplementedError

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError


class _InMemoryTTLStore(_TTLStore):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, tuple[str, float]] = {}

    def _gc(self) -> None:
        now = time.time()
        for key, (_, expires_at) in list(self._values.items())[:200]:
            if expires_at <= now:
                self._values.pop(key, None)

    def get(self, key: str) -> Optional[str]:
        now = time.time()
        with self._lock:
            self._gc()
            current = self._values.get(key)
            if not current:
                return None
            value, expires_at = current
            if expires_at <= now:
                self._values.pop(key, None)
                return None
            return value

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        with self._lock:
            self._gc()
            self._values[key] = (value, time.time() + ttl_seconds)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()


class _RedisTTLStore(_TTLStore):
    def __init__(self, url: str) -> None:
        if redis is None:  # pragma: no cover
            raise RuntimeError("redis_library_missing")
        self._client = redis.Redis.from_url(url, decode_responses=True)
        self._client.ping()

    def get(self, key: str) -> Optional[str]:
        value = self._client.get(key)
        return value if isinstance(value, str) else None

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._client.setex(key, ttl_seconds, value)

    def clear(self) -> None:
        pattern = f"{KEY_PREFIX}:*"
        for key in self._client.scan_iter(match=pattern, count=200):
            self._client.delete(key)


def _ttl_seconds() -> int:
    raw = os.getenv("X402_SECURE_VI_RECEIPT_TTL_SECONDS")
    if not raw:
        return DEFAULT_TTL_SECONDS
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid X402_SECURE_VI_RECEIPT_TTL_SECONDS=%s; using default.", raw)
        return DEFAULT_TTL_SECONDS
    return max(60, value)


def _require_redis() -> bool:
    return os.getenv("X402_SECURE_VI_RECEIPT_REQUIRE_REDIS", "").lower() in {
        "1",
        "true",
        "yes",
    }


def _build_store() -> _TTLStore:
    redis_url = os.getenv("X402_SECURE_VI_RECEIPT_REDIS_URL", "").strip()
    if redis_url:
        try:
            store = _RedisTTLStore(redis_url)
            logger.info("Using Redis VI receipt store.")
            return store
        except Exception as exc:  # pragma: no cover
            if _require_redis():
                raise RuntimeError("vi_receipt_redis_required") from exc
            logger.warning("Redis VI receipt store init failed; using memory store: %s", exc)
    elif _require_redis():
        raise RuntimeError("vi_receipt_redis_url_required")

    logger.info("Using in-memory VI receipt store.")
    return _InMemoryTTLStore()


_store: _TTLStore = _build_store()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _key(kind: str, value: str) -> str:
    return f"{KEY_PREFIX}:{kind}:{value}"


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _pick(source: Mapping[str, Any], *names: str) -> Optional[str]:
    for name in names:
        value = _string(source.get(name))
        if value:
            return value
    return None


def _hash(value: Any) -> Optional[str]:
    text = _string(value)
    return text.upper() if text else None


def _bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes"}:
            return True
        if normalized in {"0", "false", "no"}:
            return False
    return None


def _positive_decision(value: Any) -> bool:
    decision = _string(value)
    if not decision:
        return False
    return decision.lower() in {"allow", "approve", "approved", "success", "recorded"}


def _successful_receipt(value: Any) -> bool:
    status = _string(value)
    return status.lower() in {"success", "succeeded", "settled", "recorded"} if status else False


def _verified_vi(vi: Mapping[str, Any]) -> bool:
    for key in ("verified", "chain_verified", "chainVerified", "verifiableIntent"):
        value = _bool(vi.get(key))
        if value is True:
            return True
    chain = vi.get("chain")
    if isinstance(chain, Mapping):
        return _verified_vi(chain)
    return False


def _load_json(key: str) -> Optional[dict[str, Any]]:
    raw = _store.get(key)
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Ignoring malformed VI receipt store value for key=%s", key)
        return None
    return value if isinstance(value, dict) else None


def _save_json(key: str, value: Mapping[str, Any]) -> None:
    _store.set(key, _json_dumps(value), _ttl_seconds())


def record_vi_decision_snapshot(response: Mapping[str, Any]) -> None:
    decision_id = _pick(response, "decision_id", "decisionId")
    if not decision_id:
        return

    vi = _as_dict(response.get("vi"))
    snapshot = {
        "decisionId": decision_id,
        "decision": response.get("decision"),
        "riskLevel": response.get("risk_level") or response.get("riskLevel"),
        "vi": vi,
        "binding": _as_dict(response.get("binding")),
        "trustlineAssessment": _as_dict(
            response.get("trustline_assessment") or response.get("trustlineAssessment")
        ),
        "verifiableIntent": _verified_vi(vi),
        "updatedAt": _now(),
    }
    _save_json(_key("decision", decision_id), snapshot)


def record_vi_receipt_snapshot(
    payload: Mapping[str, Any],
    trustline_response: Optional[Mapping[str, Any]] = None,
) -> None:
    decision_id = _pick(payload, "decisionId", "decision_id")
    payment = _as_dict(payload.get("payment"))
    correlation = _as_dict(payload.get("correlation"))
    metadata = _as_dict(payload.get("metadata"))
    trustline = _as_dict(trustline_response)

    tx_hash = _hash(
        _pick(
            payment,
            "transaction_hash",
            "transactionHash",
            "tx_hash",
            "txHash",
            "hash",
            "transaction",
        )
        or _pick(payload, "transaction_hash", "transactionHash", "txHash", "hash")
    )
    invoice_id = _pick(correlation, "invoiceId", "invoice_id") or _pick(
        payload,
        "invoiceId",
        "invoice_id",
    )

    decision = _load_json(_key("decision", decision_id)) if decision_id else None
    vi = _as_dict((decision or {}).get("vi"))
    verifiable_intent = bool((decision or {}).get("verifiableIntent")) and _verified_vi(vi)
    receipt_status = (
        _pick(payment, "status")
        or _pick(trustline, "status")
        or _pick(payload, "status", "receiptStatus", "receipt_status")
    )
    risk_checked = (
        verifiable_intent
        and _positive_decision((decision or {}).get("decision"))
        and _successful_receipt(receipt_status)
    )

    status = {
        "hash": tx_hash,
        "transactionHash": tx_hash,
        "invoiceId": invoice_id,
        "decisionId": decision_id,
        "decision": (decision or {}).get("decision"),
        "status": receipt_status,
        "receiptStatus": trustline.get("status") or receipt_status,
        "receiptId": trustline.get("receipt_id") or trustline.get("receiptId"),
        "evidenceRef": payload.get("evidenceRef")
        or payload.get("evidence_ref")
        or trustline.get("evidence_ref")
        or trustline.get("evidenceRef"),
        "vi": vi,
        "verifiableIntent": verifiable_intent,
        "riskChecked": risk_checked,
        "receivedAt": metadata.get("receivedAt") or trustline.get("received_at") or _now(),
    }

    if tx_hash:
        _save_json(_key("tx", tx_hash), status)
    if invoice_id:
        _save_json(_key("invoice", invoice_id), status)


def _lookup_one(query: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    requested_hash = _hash(
        _pick(query, "hash", "txHash", "tx_hash", "transactionHash", "transaction_hash")
    )
    invoice_id = _pick(query, "invoiceId", "invoice_id")

    status = _load_json(_key("tx", requested_hash)) if requested_hash else None
    if not status and invoice_id:
        status = _load_json(_key("invoice", invoice_id))
    if not status:
        return None

    stored_hash = _hash(status.get("hash") or status.get("transactionHash"))
    if requested_hash and stored_hash and requested_hash != stored_hash:
        return None
    if requested_hash and not stored_hash:
        status["hash"] = requested_hash
        status["transactionHash"] = requested_hash
    return status


def lookup_vi_receipt_statuses(
    transactions: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for transaction in transactions:
        status = _lookup_one(transaction)
        if not status:
            continue
        identity = _hash(status.get("hash")) or _string(status.get("invoiceId"))
        if identity and identity in seen:
            continue
        if identity:
            seen.add(identity)
        items.append(status)
    return items


def reset_vi_receipt_store_for_tests() -> None:
    _store.clear()
