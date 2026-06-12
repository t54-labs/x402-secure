# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from typing import Any, Dict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("X402_SECURE_INTERNAL_TOKEN", "test-internal-token")
    from x402_proxy import internal_router

    app = FastAPI(title="Internal Facilitator Test")
    app.include_router(internal_router)
    return TestClient(app)


def _auth_headers() -> Dict[str, str]:
    return {"Authorization": "Bearer test-internal-token"}


class _FakeTrustlineResponse:
    def __init__(self, status_code: int, payload: Dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> Dict[str, Any]:
        return self._payload


@pytest.mark.asyncio
async def test_post_trustline_validation_async_assessment_polls_to_completion(monkeypatch) -> None:
    monkeypatch.setenv("TRUSTLINE_API_URL", "https://api.trustline.test")
    monkeypatch.setenv("TRUSTLINE_INTERNAL_TOKEN", "tl_production_test.secret")
    monkeypatch.setenv("TRUSTLINE_ASYNC_POLL_INTERVAL_S", "0.01")
    monkeypatch.setenv("TRUSTLINE_ASYNC_POLL_TIMEOUT_S", "1")
    calls: list[tuple[str, str, Dict[str, str]]] = []

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout
            self.poll_count = 0

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            json: Dict[str, Any],
            headers: Dict[str, str],
        ) -> _FakeTrustlineResponse:
            calls.append(("post", url, headers))
            assert url == "https://api.trustline.test/api/v1/validation/assess-verifiable-intent-async"
            assert headers["Authorization"] == "Bearer tl_production_test.secret"
            assert headers["Idempotency-Key"].startswith(
                "x402-secure:assess-verifiable-intent-async:"
            )
            assert headers["X-Correlation-Id"] == "risk_sess_1"
            return _FakeTrustlineResponse(
                200,
                {
                    "status": "pending",
                    "trustline_transaction_id": "tl_tx_123",
                    "underwriting_request_id": "uw_req_123",
                    "job_id": "job_123",
                    "idempotency_key": headers["Idempotency-Key"],
                    "poll_url": "/api/v1/underwriting/transactions/tl_tx_123",
                    "progress": {},
                },
            )

        async def get(self, url: str, *, headers: Dict[str, str]) -> _FakeTrustlineResponse:
            calls.append(("get", url, headers))
            assert url == "https://api.trustline.test/api/v1/underwriting/transactions/tl_tx_123"
            self.poll_count += 1
            if self.poll_count == 1:
                return _FakeTrustlineResponse(
                    200,
                    {
                        "trustline_transaction_id": "tl_tx_123",
                        "status": "running",
                        "retry_after_seconds": 0,
                        "progress": {},
                    },
                )
            return _FakeTrustlineResponse(
                200,
                {
                    "trustline_transaction_id": "tl_tx_123",
                    "status": "completed",
                    "decision": "allow",
                    "risk_level": "low",
                    "reasons": [],
                    "warnings": [],
                    "final_result": {
                        "decision": "allow",
                        "risk_level": "low",
                        "vi": {"verified": True},
                    },
                    "progress": {},
                },
            )

    monkeypatch.setattr(
        "x402_proxy.internal_facilitator.httpx.AsyncClient",
        FakeAsyncClient,
    )
    from x402_proxy.internal_facilitator import post_trustline_validation

    result = await post_trustline_validation(
        "assess-verifiable-intent-async",
        {"traceContext": {"riskSession": "risk_sess_1"}},
    )

    assert result["decision"] == "allow"
    assert result["risk_level"] == "low"
    assert result["vi"]["verified"] is True
    assert result["trustline_assessment"]["async"]["trustline_transaction_id"] == "tl_tx_123"
    assert [method for method, _url, _headers in calls] == ["post", "get", "get"]


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
    assert captured["path"] == "assess-verifiable-intent-async"
    payload = captured["payload"]
    assert payload["agentId"] == "shopping-agent-1"
    assert payload["paymentContext"]["chain"] == "xrpl"
    assert payload["paymentContext"]["amount"] == "10"
    assert payload["paymentContext"]["destination"] == "rMerchantDestination"
    assert payload["verifiableIntent"]["presentationRef"] == "tl://evidence/vi_123"
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


def test_internal_evaluate_preserves_normalized_ap2_context(monkeypatch) -> None:
    client = _client(monkeypatch)
    captured: dict = {}
    body = _evaluate_payload()
    body["extensions"]["x402Secure"]["ap2"] = {
        "paymentMandateRef": "urn:t54:ap2:xrpl:payment:inv_123",
        "paymentMandateId": "pm_xrpl_inv_123",
        "paymentMandateHash": "sha256:mandate_hash",
        "paymentMandate": {"paymentMandateId": "pm_xrpl_inv_123"},
        "paymentMessage": {
            "reference": "pm_xrpl_inv_123",
            "contents": {"amount": "10", "asset": "XRP"},
        },
        "normalized": {
            "payment_mandate_id": "pm_xrpl_inv_123",
            "payment_request_id": "inv_123",
            "merchant_agent": "did:web:merchant.example",
            "has_user_authorization": True,
            "has_merchant_authorization": True,
        },
        "riskData": {"source": "xrpl-facilitator-e2e"},
    }

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        captured["payload"] = payload
        return {
            "decision": "allow",
            "decision_id": "dec_ap2",
            "risk_level": "low",
            "binding": payload["binding"],
            "reasons": [],
            "warnings": [],
        }

    monkeypatch.setattr("x402_proxy.internal_facilitator.post_trustline_validation", fake_post)

    response = client.post(
        "/internal/x402-secure/facilitator/evaluate",
        json=body,
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    ap2_context = captured["payload"]["ap2Context"]
    assert ap2_context["paymentMandateRef"] == "urn:t54:ap2:xrpl:payment:inv_123"
    assert ap2_context["paymentMandateId"] == "pm_xrpl_inv_123"
    assert ap2_context["paymentMandateHash"] == "sha256:mandate_hash"
    assert ap2_context["paymentMandate"]["paymentMandateId"] == "pm_xrpl_inv_123"
    assert ap2_context["payment_message"]["reference"] == "pm_xrpl_inv_123"
    assert ap2_context["normalized"]["payment_mandate_id"] == "pm_xrpl_inv_123"
    assert ap2_context["normalized"]["has_user_authorization"] is True
    assert ap2_context["risk_data"]["source"] == "xrpl-facilitator-e2e"


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
