# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Any, Dict

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("X402_SECURE_INTERNAL_TOKEN", "test-internal-token")
    from x402_proxy import internal_router
    from x402_proxy.vi_receipt_store import reset_vi_receipt_store_for_tests

    reset_vi_receipt_store_for_tests()

    app = FastAPI(title="Internal Facilitator Test")
    app.include_router(internal_router)
    return TestClient(app)


def _auth_headers() -> Dict[str, str]:
    return {"Authorization": "Bearer test-internal-token"}


def _evaluate_payload(*, amount: Any = "10.00") -> Dict[str, Any]:
    return {
        "facilitator": {
            "id": "xrpl-x402-facilitator",
            "network": "xrpl",
            "environment": "test",
        },
        "payment": {
            "protocol": "x402",
            "chain": "xrpl",
            "asset": "XRP",
            "amount": amount,
            "destination": "rMerchantDestination",
            "resource": "https://merchant.example/dataset",
            "payload": {
                "TransactionType": "Payment",
                "Destination": "rMerchantDestination",
            },
            "paymentRequirements": {
                "network": "xrpl",
                "resource": "https://merchant.example/dataset",
            },
        },
        "extensions": {
            "x402Secure": {
                "version": "x402-secure.vi.v1",
                "policy": {
                    "policyId": "pol_xrpl_default",
                    "policyVersion": "v1",
                    "requireVerifiableIntent": True,
                    "requireTrace": True,
                    "reviewMode": "block",
                },
                "verifiableIntent": {
                    "format": "sd-jwt",
                    "profile": "mastercard-vi-v0.1",
                    "presentationRef": "tl://evidence/vi_123",
                    "presentationHash": "sha256:vi_hash",
                },
                "trace": {
                    "riskSession": "risk_sess_1",
                    "traceRef": "tl://evidence/trace_123",
                    "traceHash": "sha256:trace_hash",
                    "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
                    "currentTask": "staging_xrpl_verifiable_intent_payment",
                    "userInstruction": (
                        "Buy the merchant dataset if it costs no more than 20 XRP."
                    ),
                    "reasoningProcess": (
                        "The agent checked that the XRPL payment matches the VI constraints."
                    ),
                    "promptTrace": [
                        {
                            "role": "user",
                            "content": (
                                "Pay the merchant dataset only if it is within the VI limit."
                            ),
                        },
                        {
                            "role": "assistant",
                            "content": "I will bind the payment to the Verifiable Intent.",
                        },
                        {
                            "role": "toolCall",
                            "content": "build_verifiable_intent(chain=xrpl, asset=XRP)",
                        },
                        {"role": "toolResult", "content": "VI presentation created."},
                    ],
                    "toolCalls": [
                        {
                            "name": "build_verifiable_intent",
                            "arguments": {"chain": "xrpl", "asset": "XRP"},
                            "result": "holder_bound_presentation_created",
                        }
                    ],
                    "finalDecision": "Proceed only if X402 Secure allows the VI-bound payment.",
                },
            }
        },
        "merchant": {
            "id": "did:web:merchant.example",
            "origin": "https://merchant.example",
        },
        "buyer": {
            "walletAddress": "rBuyerWallet",
            "agentId": "shopping-agent-1",
        },
    }


def test_internal_evaluate_requires_configured_token(monkeypatch) -> None:
    client = _client(monkeypatch)

    response = client.post("/internal/x402-secure/facilitator/evaluate", json=_evaluate_payload())

    assert response.status_code == 401


def test_internal_auth_fails_closed_without_configured_token(monkeypatch) -> None:
    monkeypatch.delenv("X402_SECURE_INTERNAL_TOKEN", raising=False)
    monkeypatch.delenv("FACILITATOR_INTERNAL_TOKEN", raising=False)
    monkeypatch.delenv("INTERNAL_FACILITATOR_TOKEN", raising=False)
    monkeypatch.delenv("X402_SECURE_INTERNAL_AUTH_DISABLED", raising=False)

    from x402_proxy import internal_router

    app = FastAPI(title="Internal Facilitator Test")
    app.include_router(internal_router)
    client = TestClient(app)

    response = client.post("/internal/x402-secure/facilitator/evaluate", json=_evaluate_payload())

    assert response.status_code == 500
    assert "auth token is not configured" in response.json()["detail"]


def test_internal_evaluate_builds_trustline_payload(monkeypatch) -> None:
    client = _client(monkeypatch)
    captured: dict = {}

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        captured["path"] = path
        captured["payload"] = payload
        return {
            "decision": "allow",
            "decision_id": "dec_123",
            "risk_level": "low",
            "ttl_seconds": 300,
            "vi": {
                "present": True,
                "parsed": True,
                "verified": False,
                "evidence_ref": "tl_evd_123",
            },
            "binding": {
                "payment_bound": True,
                "chain": "xrpl",
                "asset": "XRP",
                "amount": "10",
                "destination": "rMerchantDestination",
                "violations": [],
            },
            "reasons": [],
            "warnings": [],
        }

    monkeypatch.setattr("x402_proxy.internal_facilitator.post_trustline_validation", fake_post)

    response = client.post(
        "/internal/x402-secure/facilitator/evaluate",
        json=_evaluate_payload(),
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["decision"] == "allow"
    assert captured["path"] == "verifiable-intent/verify-chain"
    payload = captured["payload"]
    assert payload["agentId"] == "shopping-agent-1"
    assert payload["paymentContext"]["chain"] == "xrpl"
    assert payload["paymentContext"]["amount"] == "10"
    assert payload["paymentContext"]["destination"] == "rMerchantDestination"
    assert payload["verifiableIntent"]["presentationRef"] == "tl://evidence/vi_123"
    assert payload["policy"]["policyId"] == "pol_xrpl_default"
    assert payload["policy"]["policyVersion"] == "v1"
    assert payload["binding"]["paymentBound"] is True
    assert payload["traceContext"]["traceRef"] == "tl://evidence/trace_123"
    assert payload["traceContext"]["traceHash"] == "sha256:trace_hash"
    assert payload["traceContext"]["currentTask"] == "staging_xrpl_verifiable_intent_payment"
    assert payload["traceContext"]["current_task"] == "staging_xrpl_verifiable_intent_payment"
    assert payload["traceContext"]["userInstruction"].startswith("Buy the merchant dataset")
    assert payload["traceContext"]["user_instruction"].startswith("Buy the merchant dataset")
    assert payload["traceContext"]["reasoningProcess"].startswith("The agent checked")
    assert payload["traceContext"]["reasoning_process"].startswith("The agent checked")
    assert payload["traceContext"]["promptTrace"][2]["role"] == "toolCall"
    assert payload["traceContext"]["prompt_trace"][2]["role"] == "toolCall"
    assert payload["traceContext"]["toolCalls"][0]["name"] == "build_verifiable_intent"
    assert payload["traceContext"]["tool_calls"][0]["name"] == "build_verifiable_intent"
    assert payload["traceContext"]["finalDecision"].startswith("Proceed only")
    assert payload["traceContext"]["final_decision"].startswith("Proceed only")


def test_internal_evaluate_forwards_verifiable_intent_chain_byte_exact(monkeypatch) -> None:
    client = _client(monkeypatch)
    body = _evaluate_payload()
    chain = {
        "l1Credential": {
            "format": "sd+jwt",
            "sdJwt": "eyJraWQiOiJpc3N1ZXIifQ.eyJ2Y3QiOiJjYXJkIn0.sig~l1-disclosure~",
        },
        "l2Delegation": {
            "format": "kb-sd-jwt+kb",
            "sdJwt": "eyJraWQiOiJvd25lciJ9.eyJ2Y3QiOiJtYW5kYXRlIn0.sig~l2-disclosure~",
        },
        "l3FinalAction": {
            "format": "kb-sd-jwt",
            "sdJwt": "eyJraWQiOiJhZ2VudCJ9.eyJ0cmFuc2FjdGlvbiI6e319.sig~",
        },
    }
    body["extensions"]["x402Secure"]["verifiableIntentChain"] = chain
    captured: dict = {}

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        captured["path"] = path
        captured["payload"] = payload
        return {
            "decision": "allow",
            "decision_id": "dec_chain",
            "risk_level": "low",
            "binding": payload["binding"],
        }

    monkeypatch.setattr("x402_proxy.internal_facilitator.post_trustline_validation", fake_post)

    response = client.post(
        "/internal/x402-secure/facilitator/evaluate",
        json=body,
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert captured["path"] == "verifiable-intent/verify-chain"
    payload = captured["payload"]
    assert payload["verifiableIntent"]["presentationRef"] == "tl://evidence/vi_123"
    assert payload["verifiableIntentChain"] == chain
    assert (
        payload["verifiableIntentChain"]["l1Credential"]["sdJwt"] == chain["l1Credential"]["sdJwt"]
    )
    assert payload["verifiableIntentChain"]["l2Delegation"]["sdJwt"].endswith("~l2-disclosure~")
    assert payload["verifiableIntentChain"]["l3FinalAction"]["sdJwt"].endswith(".sig~")


def test_internal_evaluate_forwards_payment_payer_as_wallet_address(monkeypatch) -> None:
    client = _client(monkeypatch)
    body = _evaluate_payload()
    body["payment"]["payer"] = "rActualPayerWallet"
    body["buyer"] = {}
    captured: dict = {}

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        captured["path"] = path
        captured["payload"] = payload
        return {
            "decision": "allow",
            "decision_id": "dec_payer",
            "risk_level": "low",
            "binding": payload["binding"],
        }

    monkeypatch.setattr("x402_proxy.internal_facilitator.post_trustline_validation", fake_post)

    response = client.post(
        "/internal/x402-secure/facilitator/evaluate",
        json=body,
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    payload = captured["payload"]
    assert payload["walletAddress"] == "rActualPayerWallet"
    assert payload["agentId"] == "rActualPayerWallet"
    assert payload["paymentContext"]["payer"] == "rActualPayerWallet"
    assert payload["paymentContext"]["payerWallet"] == "rActualPayerWallet"
    assert payload["paymentContext"]["walletAddress"] == "rActualPayerWallet"
    assert payload["paymentContext"]["destination"] == "rMerchantDestination"


def test_internal_evaluate_converts_xrpl_drops_from_payload(monkeypatch) -> None:
    client = _client(monkeypatch)
    body = _evaluate_payload(amount=None)
    body["payment"]["payload"]["Amount"] = "2500000"
    captured: dict = {}

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        captured["payload"] = payload
        return {
            "decision": "allow",
            "decision_id": "dec_456",
            "risk_level": "low",
            "binding": payload["binding"],
        }

    monkeypatch.setattr("x402_proxy.internal_facilitator.post_trustline_validation", fake_post)

    response = client.post(
        "/internal/x402-secure/facilitator/evaluate",
        json=body,
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert captured["payload"]["paymentContext"]["amount"] == "2.5"
    assert captured["payload"]["binding"]["amount"] == "2.5"


def test_internal_evaluate_converts_xrpl_drops_from_context_amount(monkeypatch) -> None:
    client = _client(monkeypatch)
    body = _evaluate_payload(amount="1000")
    body["payment"]["payload"] = {
        "signedTxBlob": "120000228000000024000000016140000000000003E8",
    }
    captured: dict = {}

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        captured["payload"] = payload
        return {
            "decision": "allow",
            "decision_id": "dec_context_drops",
            "risk_level": "low",
            "binding": payload["binding"],
        }

    monkeypatch.setattr("x402_proxy.internal_facilitator.post_trustline_validation", fake_post)

    response = client.post(
        "/internal/x402-secure/facilitator/evaluate",
        json=body,
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert captured["payload"]["paymentContext"]["amount"] == "0.001"
    assert captured["payload"]["binding"]["amount"] == "0.001"


def test_internal_evaluate_extracts_xrpl_issued_currency_amount(monkeypatch) -> None:
    client = _client(monkeypatch)
    body = _evaluate_payload(amount=None)
    body["payment"].pop("asset")
    body["payment"]["payload"]["Amount"] = {
        "currency": "USD",
        "issuer": "rIssuerAccount",
        "value": "12.50",
    }
    captured: dict = {}

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        captured["payload"] = payload
        return {
            "decision": "allow",
            "decision_id": "dec_issued",
            "risk_level": "low",
            "binding": payload["binding"],
        }

    monkeypatch.setattr("x402_proxy.internal_facilitator.post_trustline_validation", fake_post)

    response = client.post(
        "/internal/x402-secure/facilitator/evaluate",
        json=body,
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert captured["payload"]["paymentContext"]["amount"] == "12.5"
    assert captured["payload"]["paymentContext"]["asset"] == "USD"
    assert captured["payload"]["paymentContext"]["issuer"] == "rIssuerAccount"
    assert captured["payload"]["binding"]["asset"] == "USD"
    assert captured["payload"]["binding"]["issuer"] == "rIssuerAccount"
    assert captured["payload"]["binding"]["violations"] == []


def test_internal_evaluate_accepts_camelcase_trustline_decision_id(monkeypatch) -> None:
    client = _client(monkeypatch)

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "decision": "allow",
            "decisionId": "dec_camel",
            "risk_level": "low",
            "binding": payload["binding"],
        }

    monkeypatch.setattr("x402_proxy.internal_facilitator.post_trustline_validation", fake_post)

    response = client.post(
        "/internal/x402-secure/facilitator/evaluate",
        json=_evaluate_payload(),
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["decision_id"] == "dec_camel"


def test_internal_evaluate_maps_fast_chain_response(monkeypatch) -> None:
    client = _client(monkeypatch)
    captured: dict = {}

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        captured["path"] = path
        return {
            "decision": "allow",
            "decisionId": "dec_fast_chain",
            "chain": {
                "present": True,
                "parsed": True,
                "verified": True,
                "constraint_satisfied": True,
                "profile": "https://staging.t54.ai/credentials/agent-pay/vi",
                "violations": [],
                "not_evaluable": [],
                "label": "verifiable_intent:chain_verified",
                "binding": {"payment_bound": True, "chain": "xrpl"},
            },
            "binding": payload["binding"],
            "verifierMode": "enabled",
            "latencyMs": 17,
            "walletGate": {
                "decision": "allow",
                "reason": "insufficient_signal",
                "walletAddress": "rBuyerWallet",
            },
            "tier2Assessment": {
                "status": "queued",
                "trustlineTransactionId": "tl_txn_tier2_123",
            },
        }

    monkeypatch.setattr("x402_proxy.internal_facilitator.post_trustline_validation", fake_post)

    response = client.post(
        "/internal/x402-secure/facilitator/evaluate",
        json=_evaluate_payload(),
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert captured["path"] == "verifiable-intent/verify-chain"
    assert body["decision"] == "allow"
    assert body["decision_id"] == "dec_fast_chain"
    assert body["vi"]["verified"] is True
    assert body["vi"]["chain_verified"] is True
    assert body["vi"]["constraint_satisfied"] is True
    assert body["trustline_assessment"]["source"] == "verifiable-intent/verify-chain"
    assert body["trustline_assessment"]["verifier_mode"] == "enabled"
    assert body["trustline_assessment"]["latency_ms"] == 17
    assert body["trustline_assessment"]["decision_id"] == "dec_fast_chain"
    assert body["trustline_assessment"]["wallet_gate"] == {
        "decision": "allow",
        "reason": "insufficient_signal",
        "walletAddress": "rBuyerWallet",
    }
    assert body["trustline_assessment"]["tier2_assessment"] == {
        "status": "queued",
        "trustlineTransactionId": "tl_txn_tier2_123",
    }


def test_internal_evaluate_denies_fast_chain_constraint_violation(monkeypatch) -> None:
    client = _client(monkeypatch)

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "decision": "deny",
            "decisionId": "dec_fast_constraint",
            "chain": {
                "present": True,
                "parsed": True,
                "verified": True,
                "constraint_satisfied": False,
                "error_code": "VI_CONSTRAINT_EXCEEDED",
                "violations": ["per_transaction_max exceeded"],
                "not_evaluable": [],
                "label": "verifiable_intent:chain_over_delegation",
                "binding": {"payment_bound": True, "chain": "xrpl"},
            },
            "binding": payload["binding"],
        }

    monkeypatch.setattr("x402_proxy.internal_facilitator.post_trustline_validation", fake_post)

    response = client.post(
        "/internal/x402-secure/facilitator/evaluate",
        json=_evaluate_payload(),
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "deny"
    assert body["risk_level"] == "high"
    assert body["vi"]["chain_verified"] is True
    assert body["vi"]["verified"] is False
    assert body["vi"]["constraint_satisfied"] is False
    assert "VI_CONSTRAINT_EXCEEDED" in body["reasons"]
    assert "per_transaction_max exceeded" in body["reasons"]


def test_internal_evaluate_fails_closed_when_trustline_unavailable(
    monkeypatch,
    caplog,
) -> None:
    client = _client(monkeypatch)

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        raise HTTPException(status_code=502, detail="Trustline error: unavailable")

    monkeypatch.setattr("x402_proxy.internal_facilitator.post_trustline_validation", fake_post)

    response = client.post(
        "/internal/x402-secure/facilitator/evaluate",
        json=_evaluate_payload(),
        headers=_auth_headers(),
    )

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"
    assert response.headers["X-Correlation-ID"]
    body = response.json()
    assert body == {
        "reason": "trustline_unavailable",
        "retryable": True,
        "retryAfter": 30,
        "correlationId": response.headers["X-Correlation-ID"],
        "phase": "verify",
        "settlementAttempted": False,
    }
    assert "reason=trustline_unavailable" in caplog.text
    assert "retryable=true" in caplog.text
    assert f"correlation_id={body['correlationId']}" in caplog.text


@pytest.mark.parametrize("status_code", [408, 429, 500, 502, 503, 504])
def test_internal_evaluate_classifies_retryable_trustline_statuses_as_transient(
    monkeypatch,
    status_code: int,
) -> None:
    client = _client(monkeypatch)

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        raise HTTPException(
            status_code=status_code,
            detail="upstream unavailable",
            headers={"Retry-After": "17"},
        )

    monkeypatch.setattr("x402_proxy.internal_facilitator.post_trustline_validation", fake_post)

    response = client.post(
        "/internal/x402-secure/facilitator/evaluate",
        json=_evaluate_payload(),
        headers={**_auth_headers(), "X-Correlation-ID": "corr-transient"},
    )

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "17"
    assert response.headers["X-Correlation-ID"] == "corr-transient"
    assert response.json()["reason"] == "trustline_unavailable"
    assert response.json()["retryable"] is True


def test_internal_evaluate_keeps_an_http_date_retry_after_from_trustline(monkeypatch) -> None:
    client = _client(monkeypatch)
    retry_at = format_datetime(datetime.now(timezone.utc) + timedelta(minutes=10), usegmt=True)

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        raise HTTPException(
            status_code=503,
            detail="upstream unavailable",
            headers={"Retry-After": retry_at},
        )

    monkeypatch.setattr("x402_proxy.internal_facilitator.post_trustline_validation", fake_post)

    response = client.post(
        "/internal/x402-secure/facilitator/evaluate",
        json=_evaluate_payload(),
        headers=_auth_headers(),
    )

    assert response.status_code == 503
    assert 590 <= int(response.headers["Retry-After"]) <= 600
    assert response.json()["retryAfter"] == int(response.headers["Retry-After"])


@pytest.mark.parametrize(
    ("value", "expected"),
    [("17", 17), (17, 17), ("0", 0), ("-5", 0), (None, 30), ("soon", 30), ("", 30)],
)
def test_retry_after_seconds_reads_delta_seconds(value: Any, expected: int) -> None:
    from x402_proxy.internal_facilitator import _retry_after_seconds

    assert _retry_after_seconds(value) == expected


def test_retry_after_seconds_reads_http_dates() -> None:
    from x402_proxy.internal_facilitator import _retry_after_seconds

    now = datetime.now(timezone.utc)
    assert 590 <= _retry_after_seconds(format_datetime(now + timedelta(minutes=10), usegmt=True))
    assert _retry_after_seconds(format_datetime(now + timedelta(minutes=10), usegmt=True)) <= 600
    assert _retry_after_seconds(format_datetime(now - timedelta(minutes=10), usegmt=True)) == 0
    assert _retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT") == 0


def test_internal_evaluate_keeps_trustline_policy_4xx_permanent(monkeypatch) -> None:
    client = _client(monkeypatch)

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        raise HTTPException(status_code=422, detail="policy denied")

    monkeypatch.setattr("x402_proxy.internal_facilitator.post_trustline_validation", fake_post)

    response = client.post(
        "/internal/x402-secure/facilitator/evaluate",
        json=_evaluate_payload(),
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["decision"] == "deny"
    assert response.json()["reasons"] == ["Trustline policy denied"]


def test_internal_evaluate_classifies_trustline_timeout_as_transient(monkeypatch) -> None:
    client = _client(monkeypatch)

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        raise httpx.ReadTimeout("Trustline timed out")

    monkeypatch.setattr("x402_proxy.internal_facilitator.post_trustline_validation", fake_post)

    response = client.post(
        "/internal/x402-secure/facilitator/evaluate",
        json=_evaluate_payload(),
        headers=_auth_headers(),
    )

    assert response.status_code == 503
    assert response.json()["reason"] == "trustline_unavailable"
    assert response.json()["settlementAttempted"] is False


def test_internal_evaluate_propagates_correlation_to_transient_trustline_call(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 502
        headers = {"Retry-After": "30"}

        @staticmethod
        def json() -> dict[str, Any]:
            return {"error": "temporary"}

    class FakeAsyncClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url: str, *, json: dict, headers: dict):
            captured.update(url=url, json=json, headers=headers)
            return FakeResponse()

    monkeypatch.setattr("x402_proxy.internal_facilitator.httpx.AsyncClient", FakeAsyncClient)
    client = _client(monkeypatch)

    response = client.post(
        "/internal/x402-secure/facilitator/evaluate",
        json=_evaluate_payload(),
        headers={**_auth_headers(), "X-Correlation-ID": "corr-gclb-502"},
    )

    assert response.status_code == 503
    assert response.headers["X-Correlation-ID"] == "corr-gclb-502"
    assert captured["headers"]["X-Correlation-ID"] == "corr-gclb-502"
    assert captured["url"].endswith("/api/v1/validation/verifiable-intent/verify-chain")


def test_internal_evaluate_accepts_facilitator_payment_payload_hash(monkeypatch) -> None:
    client = _client(monkeypatch)
    body = _evaluate_payload()
    body["payment"]["paymentPayloadHash"] = "sha256:facilitator_payload_hash"
    captured: dict = {}

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        captured["payload"] = payload
        return {
            "decision": "allow",
            "decision_id": "dec_hash",
            "risk_level": "low",
            "binding": payload["binding"],
        }

    monkeypatch.setattr("x402_proxy.internal_facilitator.post_trustline_validation", fake_post)

    response = client.post(
        "/internal/x402-secure/facilitator/evaluate",
        json=body,
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert captured["payload"]["paymentContext"]["payloadHash"] == "sha256:facilitator_payload_hash"
    assert (
        captured["payload"]["binding"]["hashes"]["paymentPayloadHash"]
        == "sha256:facilitator_payload_hash"
    )


def test_internal_evaluate_blocks_review_when_policy_says_block(monkeypatch) -> None:
    client = _client(monkeypatch)

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "decision": "review",
            "decision_id": "dec_review",
            "risk_level": "medium",
            "reasons": ["Manual review required"],
            "warnings": [],
        }

    monkeypatch.setattr("x402_proxy.internal_facilitator.post_trustline_validation", fake_post)

    response = client.post(
        "/internal/x402-secure/facilitator/evaluate",
        json=_evaluate_payload(),
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["decision"] == "deny"
    assert "reviewMode=block" in data["warnings"][0]


def test_internal_evaluate_denies_unverified_vi_when_required(monkeypatch) -> None:
    client = _client(monkeypatch)
    body = _evaluate_payload()
    body["extensions"]["x402Secure"]["policy"]["requireVerifiedIntent"] = True

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "decision": "allow",
            "decision_id": "dec_unverified",
            "risk_level": "low",
            "vi": {"verified": False, "evidence_ref": "tl_evd_unverified"},
            "binding": payload["binding"],
        }

    monkeypatch.setattr("x402_proxy.internal_facilitator.post_trustline_validation", fake_post)

    response = client.post(
        "/internal/x402-secure/facilitator/evaluate",
        json=body,
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["decision"] == "deny"
    assert "Verified Verifiable Intent required" in data["reasons"]


def test_internal_evaluate_maps_legacy_trustline_decision(monkeypatch) -> None:
    client = _client(monkeypatch)

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "decision": "APPROVE",
            "decision_id": "dec_legacy",
            "risk_level": "low",
        }

    monkeypatch.setattr("x402_proxy.internal_facilitator.post_trustline_validation", fake_post)

    response = client.post(
        "/internal/x402-secure/facilitator/evaluate",
        json=_evaluate_payload(),
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["decision"] == "allow"


def test_internal_receipt_forwards_to_trustline(monkeypatch) -> None:
    client = _client(monkeypatch)
    captured: dict = {}

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        captured["path"] = path
        captured["payload"] = payload
        return {
            "receipt_id": "vi_rcpt_123",
            "decision_id": payload["decisionId"],
            "status": "success",
            "received_at": "2026-06-01T00:00:00Z",
        }

    monkeypatch.setattr("x402_proxy.internal_facilitator.post_trustline_validation", fake_post)

    response = client.post(
        "/internal/x402-secure/facilitator/receipt",
        json={
            "decisionId": "dec_123",
            "evidenceRef": "tl_evd_123",
            "settlementAttemptId": "attempt_1",
            "idempotencyKey": "idem_1",
            "payment": {
                "chain": "xrpl",
                "transaction_hash": "ABC",
                "status": "success",
            },
        },
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert captured["path"] == "verifiable-intent-receipt"
    assert captured["payload"]["settlementAttemptId"] == "attempt_1"
    assert captured["payload"]["metadata"]["source"] == "x402_secure_internal_facilitator"


PAYLOAD_HASH = "sha256:facilitator_payload_hash"
REQUIREMENTS_HASH = "sha256:facilitator_requirements_hash"
INVOICE_ID = "inv_verified"


def _bound_evaluate_payload() -> Dict[str, Any]:
    """An evaluate request carrying the hashes, invoice and payer the XRPL facilitator sends."""
    body = _evaluate_payload()
    body["payment"].update(
        {
            "paymentPayloadHash": PAYLOAD_HASH,
            "paymentRequirementsHash": REQUIREMENTS_HASH,
            "invoiceId": INVOICE_ID,
            "payer": "rBuyerWallet",
        }
    )
    return body


def _facilitator_receipt(
    *,
    transaction_hash: Any = "abc123",
    payment: Dict[str, Any] | None = None,
    correlation: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """A receipt shaped like the one the XRPL facilitator sends after it settles."""
    return {
        "decision_id": "dec_verified",
        "evidence_ref": "tl_evd_verified",
        "payment": {
            "chain": "xrpl",
            "network": "xrpl:testnet",
            "transaction_hash": transaction_hash,
            "status": "success",
            **(payment or {}),
        },
        "correlation": {
            "paymentPayloadHash": PAYLOAD_HASH,
            "paymentRequirementsHash": REQUIREMENTS_HASH,
            "invoiceId": INVOICE_ID,
            **(correlation or {}),
        },
    }


async def _verified_trustline(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if path == "verifiable-intent/verify-chain":
        return {
            "decision": "allow",
            "decision_id": "dec_verified",
            "risk_level": "low",
            "vi": {
                "present": True,
                "parsed": True,
                "verified": True,
                "chain_verified": True,
            },
            "binding": payload["binding"],
        }
    return {
        "receipt_id": "vi_rcpt_verified",
        "decision_id": payload["decisionId"],
        "status": "success",
        "received_at": "2026-06-01T00:00:00Z",
    }


def _lookup(client: TestClient, *transactions: Dict[str, Any]) -> Any:
    return client.post(
        "/internal/x402-secure/facilitator/vi-receipts/lookup",
        json={"transactions": list(transactions)},
        headers=_auth_headers(),
    )


def _evaluate_then_receipt(client: TestClient, receipt: Dict[str, Any]) -> None:
    evaluate = client.post(
        "/internal/x402-secure/facilitator/evaluate",
        json=_bound_evaluate_payload(),
        headers=_auth_headers(),
    )
    assert evaluate.status_code == 200
    assert evaluate.json()["decision_id"] == "dec_verified"
    response = client.post(
        "/internal/x402-secure/facilitator/receipt",
        json=receipt,
        headers=_auth_headers(),
    )
    assert response.status_code == 200


def test_internal_vi_receipt_lookup_returns_verified_settlement(monkeypatch) -> None:
    client = _client(monkeypatch)
    monkeypatch.setattr(
        "x402_proxy.internal_facilitator.post_trustline_validation", _verified_trustline
    )

    _evaluate_then_receipt(client, _facilitator_receipt())

    lookup = _lookup(client, {"hash": "ABC123", "invoiceId": "inv_verified"})
    assert lookup.status_code == 200
    items = lookup.json()["items"]
    assert len(items) == 1
    assert items[0]["hash"] == "ABC123"
    assert items[0]["invoiceId"] == "inv_verified"
    assert items[0]["verifiableIntent"] is True
    assert items[0]["riskChecked"] is True
    assert items[0]["vi"]["chain_verified"] is True
    assert items[0]["bindingStatus"] == "bound"


def test_internal_vi_receipt_binding_uses_the_evaluated_payment(monkeypatch) -> None:
    # Trustline's own "binding" in the verify-chain response may not carry the hashes;
    # the receipt is checked against the payment this proxy evaluated.
    client = _client(monkeypatch)

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        response = await _verified_trustline(path, payload)
        if path == "verifiable-intent/verify-chain":
            response["binding"] = {"paymentBound": True}
        return response

    monkeypatch.setattr("x402_proxy.internal_facilitator.post_trustline_validation", fake_post)

    _evaluate_then_receipt(client, _facilitator_receipt())

    items = _lookup(client, {"hash": "ABC123"}).json()["items"]
    assert len(items) == 1
    assert items[0]["bindingStatus"] == "bound"
    assert items[0]["verifiableIntent"] is True


@pytest.mark.parametrize(
    "mismatch",
    [
        {"correlation": {"paymentPayloadHash": "sha256:another_payload"}},
        {"correlation": {"paymentRequirementsHash": "sha256:another_requirements"}},
        {"correlation": {"invoiceId": "inv_another"}},
        {"payment": {"chain": "base"}},
        {"payment": {"destination": "rSomeoneElse"}},
        {"payment": {"payer": "rAnotherPayer"}},
        {"payment": {"amount": "999"}},
    ],
)
def test_internal_vi_receipt_for_another_payment_is_rejected(
    monkeypatch,
    caplog,
    mismatch: Dict[str, Any],
) -> None:
    # The evaluated decision's id on a receipt for a different payment must not lend that
    # payment the decision's verification.
    client = _client(monkeypatch)
    monkeypatch.setattr(
        "x402_proxy.internal_facilitator.post_trustline_validation", _verified_trustline
    )

    _evaluate_then_receipt(client, _facilitator_receipt(transaction_hash="other1", **mismatch))

    lookup = _lookup(
        client,
        {"hash": "OTHER1"},
        {"invoiceId": INVOICE_ID},
        {"invoiceId": "inv_another"},
    )
    assert lookup.status_code == 200
    assert lookup.json()["items"] == []
    assert "VI receipt rejected" in caplog.text


@pytest.mark.parametrize(
    "contradiction",
    [
        {"correlation": {"chain": "xrpl"}, "payment": {"chain": "base"}},
        {"correlation": {"payer": "rBuyerWallet"}, "payment": {"payer": "rAnotherPayer"}},
        {
            "correlation": {"destination": "rMerchantDestination"},
            "payment": {"destination": "rSomeoneElse"},
        },
        {"correlation": {"amount": "10"}, "payment": {"amount": "999"}},
        {"payment": {"paymentPayloadHash": "sha256:another_payload"}},
        {"payment": {"destination": "rMerchantDestination", "payTo": "rSomeoneElse"}},
        {"payment": {"hash": "OTHER2"}},
    ],
)
def test_internal_vi_receipt_with_contradicting_values_is_rejected(
    monkeypatch,
    caplog,
    contradiction: Dict[str, Any],
) -> None:
    # A value that matches in one place (or alias) must not hide one that contradicts the
    # evaluated payment, or another supplied value, somewhere else in the receipt.
    client = _client(monkeypatch)
    monkeypatch.setattr(
        "x402_proxy.internal_facilitator.post_trustline_validation", _verified_trustline
    )

    _evaluate_then_receipt(client, _facilitator_receipt(transaction_hash="other1", **contradiction))

    lookup = _lookup(client, {"hash": "OTHER1"}, {"hash": "OTHER2"}, {"invoiceId": INVOICE_ID})
    assert lookup.status_code == 200
    assert lookup.json()["items"] == []
    assert "VI receipt rejected" in caplog.text


def test_internal_vi_receipt_without_the_payload_hash_is_not_verified(monkeypatch) -> None:
    client = _client(monkeypatch)
    monkeypatch.setattr(
        "x402_proxy.internal_facilitator.post_trustline_validation", _verified_trustline
    )
    receipt = _facilitator_receipt()
    del receipt["correlation"]["paymentPayloadHash"]

    _evaluate_then_receipt(client, receipt)

    items = _lookup(client, {"hash": "ABC123"}).json()["items"]
    assert len(items) == 1
    assert items[0]["bindingStatus"] == "insufficient"
    assert items[0]["verifiableIntent"] is False
    assert items[0]["riskChecked"] is False
    assert items[0]["decision"] is None
    assert items[0]["vi"] == {}


def test_internal_vi_receipt_lookup_does_not_vouch_for_a_hash_it_never_stored(monkeypatch) -> None:
    # A receipt with an invoice id but no transaction hash says nothing about any hash.
    client = _client(monkeypatch)
    monkeypatch.setattr(
        "x402_proxy.internal_facilitator.post_trustline_validation", _verified_trustline
    )

    _evaluate_then_receipt(client, _facilitator_receipt(transaction_hash=None))

    lookup = _lookup(
        client,
        {"hash": "UNRELATED-1", "invoiceId": INVOICE_ID},
        {"hash": "UNRELATED-2", "invoiceId": INVOICE_ID},
    )
    assert lookup.status_code == 200
    assert lookup.json()["items"] == []

    by_invoice = _lookup(client, {"invoiceId": INVOICE_ID}).json()["items"]
    assert len(by_invoice) == 1
    assert by_invoice[0]["hash"] is None
    assert by_invoice[0]["invoiceId"] == INVOICE_ID


def _store_from_env(monkeypatch, **env: str) -> None:
    for name in ("X402_SECURE_VI_RECEIPT_STORE", "X402_SECURE_VI_RECEIPT_REDIS_URL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    from x402_proxy.vi_receipt_store import reload_vi_receipt_store_from_env_for_tests

    reload_vi_receipt_store_from_env_for_tests()


@pytest.mark.parametrize(
    ("env", "log_text"),
    [
        ({}, "X402_SECURE_VI_RECEIPT_REDIS_URL is not set"),
        ({"X402_SECURE_VI_RECEIPT_STORE": "memcache"}, "Unknown X402_SECURE_VI_RECEIPT_STORE"),
        ({"X402_SECURE_VI_RECEIPT_REDIS_URL": "redis://127.0.0.1:1/0"}, "unreachable"),
    ],
)
def test_internal_vi_receipt_lookup_answers_503_without_a_shared_store(
    monkeypatch,
    caplog,
    env: Dict[str, str],
    log_text: str,
) -> None:
    # Several workers serve these routes, and a per-process fallback would answer each one
    # differently. Without a usable shared store, the lookup says so instead.
    client = _client(monkeypatch)
    _store_from_env(monkeypatch, **env)
    monkeypatch.setattr(
        "x402_proxy.internal_facilitator.post_trustline_validation", _verified_trustline
    )

    # Evaluate and the receipt forward still answer; there is just nowhere to store them.
    _evaluate_then_receipt(client, _facilitator_receipt())

    lookup = _lookup(client, {"hash": "ABC123"})
    assert lookup.status_code == 503
    assert lookup.headers["Retry-After"] == "30"
    assert lookup.json() == {"reason": "vi_receipt_store_unavailable", "retryable": True}
    assert log_text in caplog.text


def test_internal_vi_receipt_in_memory_store_is_opt_in(monkeypatch) -> None:
    client = _client(monkeypatch)
    _store_from_env(monkeypatch, X402_SECURE_VI_RECEIPT_STORE="memory")
    monkeypatch.setattr(
        "x402_proxy.internal_facilitator.post_trustline_validation", _verified_trustline
    )

    _evaluate_then_receipt(client, _facilitator_receipt())

    items = _lookup(client, {"hash": "ABC123"}).json()["items"]
    assert [item["riskChecked"] for item in items] == [True]


class _SilentServer:
    """A TCP server that accepts connections and never answers, like a hung Redis."""

    def __init__(self) -> None:
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(16)
        self._listener.settimeout(0.05)
        self.port = self._listener.getsockname()[1]
        self._connections: list[socket.socket] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _ = self._listener.accept()
            except OSError:
                continue
            self._connections.append(connection)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        for connection in self._connections:
            connection.close()
        self._listener.close()


def _within(seconds: float, action: Any) -> Any:
    """Run ``action`` in a thread and fail, instead of hanging, if it outlasts ``seconds``."""
    outcome: Dict[str, Any] = {}

    def target() -> None:
        try:
            outcome["value"] = action()
        except BaseException as exc:  # re-raised in the test thread below
            outcome["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(seconds)
    assert not worker.is_alive(), f"still blocked after {seconds}s"
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


def test_internal_vi_receipt_store_times_out_on_a_stalled_redis(monkeypatch) -> None:
    # A Redis that accepts the connection but never replies must not hang startup or the
    # routes: each operation times out, and the lookup answers 503.
    server = _SilentServer()
    try:
        client = _client(monkeypatch)
        monkeypatch.setenv("X402_SECURE_VI_RECEIPT_REDIS_TIMEOUT_SECONDS", "0.2")
        url = f"redis://127.0.0.1:{server.port}/0"
        _within(5, lambda: _store_from_env(monkeypatch, X402_SECURE_VI_RECEIPT_REDIS_URL=url))
        monkeypatch.setattr(
            "x402_proxy.internal_facilitator.post_trustline_validation", _verified_trustline
        )

        def evaluate_receipt_and_lookup() -> Any:
            _evaluate_then_receipt(client, _facilitator_receipt())
            return _lookup(client, {"hash": "ABC123"})

        lookup = _within(10, evaluate_receipt_and_lookup)
        assert lookup.status_code == 503
        assert lookup.json()["reason"] == "vi_receipt_store_unavailable"
    finally:
        server.close()


def test_internal_vi_receipt_lookup_keeps_the_event_loop_free(monkeypatch) -> None:
    # Store calls block (Redis is synchronous), so they run in a worker thread: other
    # requests on the same event loop keep being served meanwhile.
    _client(monkeypatch)
    from x402_proxy import internal_router
    from x402_proxy.vi_receipt_store import _InMemoryTTLStore

    class SlowStore(_InMemoryTTLStore):
        def get(self, key: str) -> Any:
            time.sleep(0.3)
            return super().get(key)

    monkeypatch.setattr("x402_proxy.vi_receipt_store._store", SlowStore())
    app = FastAPI()
    app.include_router(internal_router)

    async def lookup_with_heartbeat() -> tuple[int, int]:
        ticks = 0
        done = asyncio.Event()

        async def heartbeat() -> None:
            nonlocal ticks
            while not done.is_set():
                ticks += 1
                await asyncio.sleep(0.02)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy") as http:
            beat = asyncio.create_task(heartbeat())
            response = await http.post(
                "/internal/x402-secure/facilitator/vi-receipts/lookup",
                json={"transactions": [{"hash": "ABC123"}]},
                headers=_auth_headers(),
            )
            done.set()
            await beat
        return response.status_code, ticks

    status, ticks = asyncio.run(lookup_with_heartbeat())
    assert status == 200
    assert ticks >= 5


def test_internal_vi_receipt_lookup_does_not_infer_vi_from_success(monkeypatch) -> None:
    client = _client(monkeypatch)

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "receipt_id": "vi_rcpt_unverified",
            "decision_id": payload["decisionId"],
            "status": "success",
            "received_at": "2026-06-01T00:00:00Z",
        }

    monkeypatch.setattr("x402_proxy.internal_facilitator.post_trustline_validation", fake_post)

    receipt = client.post(
        "/internal/x402-secure/facilitator/receipt",
        json={
            "decisionId": "dec_missing",
            "payment": {
                "chain": "xrpl",
                "transaction_hash": "def456",
                "status": "success",
            },
        },
        headers=_auth_headers(),
    )
    assert receipt.status_code == 200

    lookup = client.post(
        "/internal/x402-secure/facilitator/vi-receipts/lookup",
        json={"transactions": [{"hash": "DEF456"}]},
        headers=_auth_headers(),
    )
    assert lookup.status_code == 200
    items = lookup.json()["items"]
    assert len(items) == 1
    assert items[0]["verifiableIntent"] is False
    assert items[0]["riskChecked"] is False


def test_internal_evidence_session_forwards_policy(monkeypatch) -> None:
    client = _client(monkeypatch)
    captured: dict = {}

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        captured["path"] = path
        captured["payload"] = payload
        return {
            "session_id": "vi_sess_123",
            "required_evidence": ["vi_presentation"],
            "expires_at": "2026-06-01T00:15:00Z",
            "evidence_upload_url": "/api/v1/validation/verifiable-intent/evidence",
            "created_at": "2026-06-01T00:00:00Z",
        }

    monkeypatch.setattr("x402_proxy.internal_facilitator.post_trustline_validation", fake_post)

    response = client.post(
        "/internal/x402-secure/facilitator/evidence-session",
        json={
            "facilitator": {
                "id": "xrpl-x402-facilitator",
                "network": "xrpl",
            },
            "agentId": "shopping-agent-1",
            "walletAddress": "rBuyerWallet",
            "merchantId": "did:web:merchant.example",
            "policy": {"requireVerifiableIntent": True},
            "requiredEvidence": ["vi_presentation"],
        },
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert captured["path"] == "verifiable-intent/evidence-session"
    assert captured["payload"]["policy"]["requireVerifiableIntent"] is True
    assert response.json()["session_id"] == "vi_sess_123"


def test_internal_trustline_auth_headers_prefer_org_token(monkeypatch) -> None:
    monkeypatch.setenv("TRUSTLINE_INTERNAL_TOKEN", "default-token")
    monkeypatch.setenv(
        "X402_SECURE_TRUSTLINE_TOKENS_BY_ORG_JSON",
        '{"org_xrpl_dev":"org-token"}',
    )
    monkeypatch.setenv(
        "X402_SECURE_TRUSTLINE_TOKENS_BY_POLICY_JSON",
        '{"pol_xrpl_default":"policy-token"}',
    )

    from x402_proxy.internal_facilitator import _trustline_auth_headers

    headers = _trustline_auth_headers(
        {
            "metadata": {"developerOrgId": "org_xrpl_dev"},
            "policy": {"policyId": "pol_xrpl_default"},
        }
    )

    assert headers == {"Authorization": "Bearer org-token"}


def test_internal_trustline_auth_headers_use_policy_token(monkeypatch) -> None:
    monkeypatch.setenv("TRUSTLINE_INTERNAL_TOKEN", "default-token")
    monkeypatch.setenv(
        "X402_SECURE_TRUSTLINE_TOKENS_BY_POLICY_JSON",
        '{"pol_xrpl_default":"policy-token"}',
    )

    from x402_proxy.internal_facilitator import _trustline_auth_headers

    headers = _trustline_auth_headers({"policy": {"policyId": "pol_xrpl_default"}})

    assert headers == {"Authorization": "Bearer policy-token"}


def test_public_trustline_auth_headers_ignore_internal_route_maps(monkeypatch) -> None:
    monkeypatch.setenv("TRUSTLINE_INTERNAL_TOKEN", "default-token")
    monkeypatch.setenv(
        "X402_SECURE_TRUSTLINE_TOKENS_BY_ORG_JSON",
        '{"org_xrpl_dev":"org-token"}',
    )
    monkeypatch.setenv(
        "X402_SECURE_TRUSTLINE_TOKENS_BY_POLICY_JSON",
        '{"pol_xrpl_default":"policy-token"}',
    )

    from x402_proxy.internal_facilitator import _trustline_auth_headers

    headers = _trustline_auth_headers(
        {
            "metadata": {"developerOrgId": "org_xrpl_dev"},
            "policy": {"policyId": "pol_xrpl_default"},
        },
        route_by_payload=False,
    )

    assert headers == {"Authorization": "Bearer default-token"}


def test_internal_trustline_auth_headers_fallback_to_default_token(monkeypatch) -> None:
    monkeypatch.setenv("TRUSTLINE_INTERNAL_TOKEN", "default-token")
    monkeypatch.setenv(
        "X402_SECURE_TRUSTLINE_TOKENS_BY_POLICY_JSON",
        '{"pol_other":"other-token"}',
    )

    from x402_proxy.internal_facilitator import _trustline_auth_headers

    headers = _trustline_auth_headers({"policy": {"policyId": "pol_xrpl_default"}})

    assert headers == {"Authorization": "Bearer default-token"}


def test_internal_trustline_auth_headers_reject_invalid_policy_map(monkeypatch) -> None:
    monkeypatch.setenv("X402_SECURE_TRUSTLINE_TOKENS_BY_POLICY_JSON", "not-json")

    from x402_proxy.internal_facilitator import _trustline_auth_headers

    try:
        _trustline_auth_headers({"policy": {"policyId": "pol_xrpl_default"}})
    except HTTPException as exc:
        assert exc.status_code == 500
        assert "must be a JSON object" in str(exc.detail)
    else:  # pragma: no cover
        raise AssertionError("Expected invalid policy-token map to fail closed")
