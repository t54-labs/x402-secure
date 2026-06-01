# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
"""
Live XRPL testnet smoke test.

This test spends testnet XRP and is skipped by default. To run it:

    XRPL_LIVE_SMOKE=1 \
    XRPL_TESTNET_SEED=... \
    XRPL_TESTNET_PAY_TO=... \
    uv run pytest tests/test_xrpl_live_smoke.py -q -s
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from x402_secure_client import build_xrpl_payment_payload, encode_xrpl_payment_signature


def _live_env() -> dict[str, str]:
    if os.getenv("XRPL_LIVE_SMOKE") != "1":
        pytest.skip("Set XRPL_LIVE_SMOKE=1 to run the live XRPL smoke test")

    seed = os.getenv("XRPL_TESTNET_SEED")
    pay_to = os.getenv("XRPL_TESTNET_PAY_TO")
    if not seed or not pay_to:
        pytest.skip("XRPL_TESTNET_SEED and XRPL_TESTNET_PAY_TO are required")

    return {
        "seed": seed,
        "pay_to": pay_to,
        "amount": os.getenv("XRPL_TESTNET_AMOUNT_DROPS", "1000"),
        "source_tag": os.getenv("XRPL_TESTNET_SOURCE_TAG", "804681468"),
        "facilitator_url": os.getenv(
            "XRPL_TESTNET_FACILITATOR_URL",
            "https://xrpl-facilitator-testnet.t54.ai",
        ).rstrip("/"),
        "rpc_url": os.getenv(
            "XRPL_TESTNET_RPC_URL",
            "https://s.altnet.rippletest.net:51234/",
        ),
    }


def _build_signed_xrpl_payment_blob(env: dict[str, str], invoice_id: str) -> tuple[str, str]:
    try:
        from xrpl.clients import JsonRpcClient
        from xrpl.models.transactions import Payment
        from xrpl.transaction import autofill_and_sign
        from xrpl.wallet import Wallet
    except ImportError as exc:
        pytest.skip(f"xrpl-py is required for the live XRPL smoke test: {exc}")

    wallet = Wallet.from_seed(env["seed"])
    invoice_hash = hashlib.sha256(invoice_id.encode("utf-8")).hexdigest().upper()
    payment = Payment.from_dict(
        {
            "TransactionType": "Payment",
            "Account": wallet.classic_address,
            "Destination": env["pay_to"],
            "Amount": env["amount"],
            "SourceTag": int(env["source_tag"]),
            "InvoiceID": invoice_hash,
        }
    )
    client = JsonRpcClient(env["rpc_url"])
    try:
        signed = autofill_and_sign(payment, client, wallet)
    except TypeError:
        signed = autofill_and_sign(payment, wallet, client)

    tx_blob = getattr(signed, "tx_blob", None)
    if not tx_blob:
        pytest.fail("xrpl-py did not return a signed tx_blob")
    return wallet.classic_address, tx_blob


def test_live_xrpl_testnet_verify_and_settle_through_proxy(monkeypatch):
    env = _live_env()
    invoice_id = f"x402-secure-live-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    payer, signed_tx_blob = _build_signed_xrpl_payment_blob(env, invoice_id)

    from x402_proxy import risk_router, router

    monkeypatch.setenv("PROXY_LOCAL_RISK", "1")
    monkeypatch.setenv("PROXY_UPSTREAM_VERIFY_URL", f"{env['facilitator_url']}/verify")
    monkeypatch.setenv("PROXY_UPSTREAM_SETTLE_URL", f"{env['facilitator_url']}/settle")

    app = FastAPI(title="Live XRPL x402 smoke")
    app.include_router(risk_router)
    app.include_router(router)
    client = TestClient(app)

    session_response = client.post(
        "/risk/session",
        json={"agent_did": payer, "wallet_address": payer, "app_id": "xrpl-live-smoke"},
    )
    assert session_response.status_code == 200
    sid = session_response.json()["sid"]

    payment_requirements = {
        "scheme": "exact",
        "network": "xrpl:1",
        "asset": "XRP",
        "payTo": env["pay_to"],
        "amount": env["amount"],
        "maxTimeoutSeconds": 600,
        "extra": {
            "sourceTag": int(env["source_tag"]),
            "invoiceId": invoice_id,
        },
    }
    payment_payload = build_xrpl_payment_payload(
        payment_requirements,
        signed_tx_blob=signed_tx_blob,
    )
    payment_signature = encode_xrpl_payment_signature(payment_payload)
    body = {
        "x402Version": 2,
        "paymentPayload": payment_payload,
        "paymentRequirements": payment_requirements,
    }
    headers = {
        "PAYMENT-SIGNATURE": payment_signature,
        "X-PAYMENT-SECURE": (
            "w3c.v1;tp=00-4bf92f3577b34da6a3ce929d0e0e4736-"
            "00f067aa0ba902b7-01"
        ),
        "X-RISK-SESSION": sid,
        "Origin": "https://seller.example",
    }

    verify_response = client.post("/x402/verify", json=body, headers=headers)
    assert verify_response.status_code == 200, verify_response.text
    verify_data = verify_response.json()
    assert verify_data["isValid"] is True, verify_data

    settle_response = client.post("/x402/settle", json=body, headers=headers)
    assert settle_response.status_code == 200, settle_response.text
    settle_data = settle_response.json()
    assert settle_data["success"] is True, settle_data
    assert settle_data["network"] == "xrpl:1"
    assert settle_data["transaction"]
