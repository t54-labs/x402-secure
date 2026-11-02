# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
"""VI receipt statuses behind ``/internal/x402-secure/facilitator/vi-receipts/lookup``.

Evaluate stores a decision snapshot, including the payment it evaluated. A settled
receipt inherits that decision's verification only when it carries the same payment
binding. Lookups then report the stored status for a transaction hash or invoice id.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
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


DEFAULT_REDIS_TIMEOUT_SECONDS = 1.0


def _redis_timeout_seconds() -> float:
    raw = os.getenv("X402_SECURE_VI_RECEIPT_REDIS_TIMEOUT_SECONDS")
    if not raw:
        return DEFAULT_REDIS_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "Invalid X402_SECURE_VI_RECEIPT_REDIS_TIMEOUT_SECONDS=%s; using default.", raw
        )
        return DEFAULT_REDIS_TIMEOUT_SECONDS
    return min(max(0.05, value), 30.0)


class _RedisTTLStore(_TTLStore):
    def __init__(self, url: str) -> None:
        if redis is None:  # pragma: no cover
            raise RuntimeError("redis_library_missing")
        from redis.backoff import NoBackoff
        from redis.retry import Retry

        # A Redis that accepts the connection but stops answering must fail fast, not hang:
        # bounded connect and read timeouts, and a single attempt per operation.
        timeout = _redis_timeout_seconds()
        self._client = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
            retry_on_timeout=False,
            retry=Retry(NoBackoff(), 0),
        )

    def ping(self) -> None:
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


class VIReceiptStoreUnavailable(RuntimeError):
    """No shared VI receipt store is configured, or the configured one is unreachable."""


def _build_store() -> Optional[_TTLStore]:
    """Return the configured receipt store, or None when none is usable.

    Receipts must live in a store every worker shares. Dockerfile.production runs four
    uvicorn workers, and a per-process store gives each worker different answers. So Redis
    is the default, and the in-memory store is used only when X402_SECURE_VI_RECEIPT_STORE=
    memory asks for it, for a single process. Nothing falls back silently: without a
    reachable Redis, receipts are not stored and lookups answer 503.
    """
    mode = os.getenv("X402_SECURE_VI_RECEIPT_STORE", "redis").strip().lower() or "redis"
    if mode == "memory":
        logger.warning(
            "Using the in-memory VI receipt store; it is per process, so run a single worker."
        )
        return _InMemoryTTLStore()
    if mode != "redis":
        logger.error(
            "Unknown X402_SECURE_VI_RECEIPT_STORE=%s; VI receipt lookups answer 503.", mode
        )
        return None
    redis_url = os.getenv("X402_SECURE_VI_RECEIPT_REDIS_URL", "").strip()
    if not redis_url:
        logger.error("X402_SECURE_VI_RECEIPT_REDIS_URL is not set; VI receipt lookups answer 503.")
        return None
    try:
        store = _RedisTTLStore(redis_url)
    except Exception as exc:
        logger.error(
            "VI receipt Redis store could not be created (%s); lookups answer 503.",
            type(exc).__name__,
        )
        return None
    try:
        store.ping()
        logger.info("Using Redis VI receipt store.")
    except Exception as exc:
        logger.error(
            "VI receipt Redis store is unreachable (%s); lookups answer 503 until it is.",
            type(exc).__name__,
        )
    return store


_store: Optional[_TTLStore] = _build_store()


def _usable_store() -> _TTLStore:
    if _store is None:
        raise VIReceiptStoreUnavailable("vi_receipt_store_not_configured")
    return _store


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


# Fields that tie a receipt to the payment its decision evaluated, with the names a
# receipt may use for each. The XRPL facilitator sends paymentPayloadHash,
# paymentRequirementsHash and invoiceId under "correlation", and the chain under "payment".
_BINDING_FIELDS: dict[str, tuple[str, ...]] = {
    "paymentPayloadHash": (
        "paymentPayloadHash",
        "payment_payload_hash",
        "payloadHash",
        "payload_hash",
    ),
    "paymentRequirementsHash": ("paymentRequirementsHash", "payment_requirements_hash"),
    "invoiceId": ("invoiceId", "invoice_id"),
    "chain": ("chain",),
    "payer": ("payer", "payerWallet", "payer_wallet", "walletAddress", "wallet_address"),
    "destination": ("destination", "payTo", "pay_to"),
    "amount": ("amount",),
    "asset": ("asset", "currency"),
    "issuer": ("issuer",),
}
_CASE_INSENSITIVE_BINDING_FIELDS = {
    "paymentPayloadHash",
    "paymentRequirementsHash",
    "chain",
    "asset",
}


def _all_values(sources: tuple[Mapping[str, Any], ...], names: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for source in sources:
        for name in names:
            value = _string(source.get(name))
            if value is not None:
                values.append(value)
    return values


def _binding_from_receipt(payload: Mapping[str, Any]) -> dict[str, list[str]]:
    # Every value supplied for each field, from every source and alias. A matching value in
    # one place must not hide a contradicting one in another.
    sources = (_as_dict(payload.get("correlation")), _as_dict(payload.get("payment")), payload)
    evidence: dict[str, list[str]] = {}
    for field, names in _BINDING_FIELDS.items():
        values = _all_values(sources, names)
        if values:
            evidence[field] = values
    return evidence


def _same_binding_value(field: str, left: str, right: str) -> bool:
    if field == "amount":
        try:
            return Decimal(left) == Decimal(right)
        except InvalidOperation:
            return left == right
    if field in _CASE_INSENSITIVE_BINDING_FIELDS:
        return left.lower() == right.lower()
    return left == right


def _check_receipt_binding(
    evaluated: Mapping[str, Any],
    receipt: Mapping[str, list[str]],
) -> tuple[str, list[str]]:
    """Compare a receipt's binding fields with the payment its decision evaluated.

    Returns ``("mismatch", fields)`` when any supplied value of a field differs from the
    evaluated one (or, where nothing was evaluated, from the other supplied values),
    ``("insufficient", [])`` when either side lacks the paymentPayloadHash, and
    ``("bound", [])`` otherwise.
    """
    mismatched = []
    for field, values in receipt.items():
        reference = _string(evaluated.get(field)) or values[0]
        if any(not _same_binding_value(field, value, reference) for value in values):
            mismatched.append(field)
    if mismatched:
        return "mismatch", sorted(mismatched)
    if not _string(evaluated.get("paymentPayloadHash")) or "paymentPayloadHash" not in receipt:
        return "insufficient", []
    return "bound", []


def _load_json(key: str) -> Optional[dict[str, Any]]:
    store = _usable_store()
    try:
        raw = store.get(key)
    except Exception as exc:
        raise VIReceiptStoreUnavailable(type(exc).__name__) from exc
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Ignoring malformed VI receipt store value for key=%s", key)
        return None
    return value if isinstance(value, dict) else None


def _save_json(key: str, value: Mapping[str, Any]) -> None:
    store = _usable_store()
    try:
        store.set(key, _json_dumps(value), _ttl_seconds())
    except Exception as exc:
        raise VIReceiptStoreUnavailable(type(exc).__name__) from exc


def record_vi_decision_snapshot(
    response: Mapping[str, Any],
    payment_binding: Optional[Mapping[str, Any]] = None,
) -> None:
    """Remember an evaluate decision so the receipt that settles it can be checked.

    ``payment_binding`` describes the payment the decision evaluated, keyed like
    ``_BINDING_FIELDS``. A receipt inherits the decision's verification only if it matches.
    A store outage is logged and never fails the evaluation.
    """
    try:
        _record_vi_decision_snapshot(response, payment_binding)
    except VIReceiptStoreUnavailable as exc:
        logger.warning(
            "VI decision snapshot not stored (receipt store unavailable: %s) decision_id=%s",
            exc,
            _pick(response, "decision_id", "decisionId"),
        )


def _record_vi_decision_snapshot(
    response: Mapping[str, Any],
    payment_binding: Optional[Mapping[str, Any]],
) -> None:
    decision_id = _pick(response, "decision_id", "decisionId")
    if not decision_id:
        return

    vi = _as_dict(response.get("vi"))
    evaluated = _as_dict(payment_binding)
    snapshot = {
        "decisionId": decision_id,
        "decision": response.get("decision"),
        "riskLevel": response.get("risk_level") or response.get("riskLevel"),
        "vi": vi,
        "binding": _as_dict(response.get("binding")),
        "paymentBinding": {
            field: value
            for field in _BINDING_FIELDS
            if (value := _string(evaluated.get(field))) is not None
        },
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
    """Store the lookup status for a settled payment's receipt.

    The status carries the decision's verification only when the receipt is bound to the
    payment that decision evaluated (``_check_receipt_binding``). A receipt that contradicts
    the evaluated payment is not stored at all. A store outage is logged and never fails
    the receipt.
    """
    try:
        _record_vi_receipt_snapshot(payload, trustline_response)
    except VIReceiptStoreUnavailable as exc:
        logger.warning(
            "VI receipt not stored (receipt store unavailable: %s) decision_id=%s",
            exc,
            _pick(payload, "decisionId", "decision_id"),
        )


def _record_vi_receipt_snapshot(
    payload: Mapping[str, Any],
    trustline_response: Optional[Mapping[str, Any]],
) -> None:
    decision_id = _pick(payload, "decisionId", "decision_id")
    payment = _as_dict(payload.get("payment"))
    correlation = _as_dict(payload.get("correlation"))
    metadata = _as_dict(payload.get("metadata"))
    trustline = _as_dict(trustline_response)

    tx_hashes = {
        _hash(value)
        for value in _all_values(
            (payment,),
            ("transaction_hash", "transactionHash", "tx_hash", "txHash", "hash", "transaction"),
        )
        + _all_values((payload,), ("transaction_hash", "transactionHash", "txHash", "hash"))
    }
    if len(tx_hashes) > 1:
        logger.warning(
            "VI receipt rejected: it names more than one transaction hash decision_id=%s",
            decision_id,
        )
        return
    tx_hash = next(iter(tx_hashes), None)
    invoice_id = _pick(correlation, "invoiceId", "invoice_id") or _pick(
        payload,
        "invoiceId",
        "invoice_id",
    )

    decision = _load_json(_key("decision", decision_id)) if decision_id else None
    binding_status = "decision_not_found"
    if decision is not None:
        binding_status, mismatched = _check_receipt_binding(
            _as_dict(decision.get("paymentBinding")),
            _binding_from_receipt(payload),
        )
        if binding_status == "mismatch":
            logger.warning(
                "VI receipt rejected: it does not match the payment decision_id=%s "
                "evaluated (fields=%s)",
                decision_id,
                ",".join(mismatched),
            )
            return
    # Only a receipt bound to the evaluated payment may carry that decision's outcome.
    bound = decision if binding_status == "bound" else {}
    vi = _as_dict(bound.get("vi"))
    verifiable_intent = bool(bound.get("verifiableIntent")) and _verified_vi(vi)
    receipt_status = (
        _pick(payment, "status")
        or _pick(trustline, "status")
        or _pick(payload, "status", "receiptStatus", "receipt_status")
    )
    risk_checked = (
        verifiable_intent
        and _positive_decision(bound.get("decision"))
        and _successful_receipt(receipt_status)
    )

    status = {
        "hash": tx_hash,
        "transactionHash": tx_hash,
        "invoiceId": invoice_id,
        "decisionId": decision_id,
        "decision": bound.get("decision"),
        "bindingStatus": binding_status,
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
    # A receipt stored without a transaction hash, or for another one, cannot vouch for
    # the hash the reader supplied: an invoice match alone is not transaction evidence.
    if requested_hash and requested_hash != stored_hash:
        return None
    return status


def lookup_vi_receipt_statuses(
    transactions: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return the stored status for each transaction hash or invoice id that has one.

    Raises VIReceiptStoreUnavailable when there is no usable shared store, so the caller
    can answer 503 instead of an empty, misleading result.
    """
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
    """Give a test a fresh in-memory store, whatever the environment configures."""
    global _store
    _store = _InMemoryTTLStore()


def reload_vi_receipt_store_from_env_for_tests() -> None:
    """Rebuild the store from the environment as at import, to test the defaults."""
    global _store
    _store = _build_store()
