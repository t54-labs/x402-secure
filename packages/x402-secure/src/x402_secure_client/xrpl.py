# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import base64
import json
from typing import Any, Dict


def encode_xrpl_payment_signature(payment_payload: Dict[str, Any]) -> str:
    return base64.b64encode(
        json.dumps(payment_payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).decode("ascii")


def decode_xrpl_payment_signature(payment_signature_b64: str) -> Dict[str, Any]:
    data = json.loads(base64.b64decode(payment_signature_b64).decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("PAYMENT-SIGNATURE must decode to a JSON object")
    return data


def build_xrpl_payment_payload(
    payment_requirements: Dict[str, Any],
    *,
    signed_tx_blob: str,
    x402_version: int = 2,
) -> Dict[str, Any]:
    return {
        "x402Version": x402_version,
        "accepted": dict(payment_requirements),
        "payload": {"signedTxBlob": signed_tx_blob},
    }


def build_xrpl_payment_required_response(
    payment_requirements: Dict[str, Any],
    *,
    error: str = "Payment required",
    x402_version: int = 2,
) -> Dict[str, Any]:
    return {
        "x402Version": x402_version,
        "accepts": [dict(payment_requirements)],
        "error": error,
    }
