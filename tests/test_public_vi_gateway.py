from __future__ import annotations

from typing import Any, Dict, Optional

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from x402_proxy.headers import HeaderError, parse_x_verifiable_intent
from x402_proxy.routes import (
    ProxyVerifyRequest,
    build_public_payment_context,
    build_public_vi_assessment_payload,
    extract_verifiable_intent_evidence,
    extract_vi_policy,
)

_VI_HASH = "sha256:" + ("a" * 64)
_TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


def _request(headers: Optional[Dict[str, str]] = None) -> Request:
    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/x402/verify",
            "scheme": "http",
            "server": ("testserver", 80),
            "headers": raw_headers,
        }
    )


def _body(*, extra: Optional[Dict[str, Any]] = None, **overrides: Any) -> ProxyVerifyRequest:
    data: Dict[str, Any] = {
        "x402Version": 1,
        "paymentPayload": {
            "x402Version": 1,
            "scheme": "exact",
            "network": "base-sepolia",
            "payload": {
                "signature": "0x" + ("d" * 130),
                "authorization": {
                    "from": "0x" + ("b" * 40),
                    "to": "0x" + ("c" * 40),
                    "value": "1000000",
                    "validAfter": "0",
                    "validBefore": str(2**256 - 1),
                    "nonce": "0x" + ("0" * 64),
                },
            },
        },
        "paymentRequirements": {
            "scheme": "exact",
            "network": "base-sepolia",
            "maxAmountRequired": "1000000",
            "resource": "https://merchant.example/data",
            "description": "Paid data API",
            "mimeType": "application/json",
            "payTo": "0x" + ("c" * 40),
            "maxTimeoutSeconds": 60,
            "asset": "0x" + ("a" * 40),
            "extra": extra or {},
        },
    }
    data.update(overrides)
    return ProxyVerifyRequest.model_validate(data)


def _body_json(body: ProxyVerifyRequest) -> Dict[str, Any]:
    return body.model_dump(by_alias=True, exclude_none=True, mode="json")


def _proxy_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("PROXY_LOCAL_RISK", "1")
    monkeypatch.setenv("PROXY_UPSTREAM_VERIFY_URL", "http://testserver/upstream/verify")
    monkeypatch.setenv("PROXY_UPSTREAM_SETTLE_URL", "http://testserver/upstream/settle")

    from x402_proxy import risk_router, router

    app = FastAPI(title="Public VI Gateway Test")
    app.include_router(risk_router)
    app.include_router(router)

    @app.post("/upstream/verify")
    async def upstream_verify(payload: Dict[str, Any]) -> Dict[str, Any]:
        return {"isValid": True, "payer": "0x" + ("b" * 40)}

    @app.post("/upstream/settle")
    async def upstream_settle(payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "success": True,
            "payer": "0x" + ("b" * 40),
            "transaction": "0x" + ("e" * 64),
            "network": "base-sepolia",
        }

    return TestClient(app)


def _risk_session(client: TestClient) -> str:
    response = client.post(
        "/risk/session",
        json={
            "agent_did": "shopping-agent-1",
            "wallet_address": "0x" + ("b" * 40),
        },
    )
    assert response.status_code == 200
    return response.json()["sid"]


def test_parse_x_verifiable_intent_reference_header() -> None:
    parsed = parse_x_verifiable_intent(
        f"vi.v1;ref=tl://evidence/vi_123;sha256={_VI_HASH};mt=application/sd-jwt;sz=321"
    )

    assert parsed == {
        "ref": "tl://evidence/vi_123",
        "sha256": _VI_HASH,
        "mt": "application/sd-jwt",
        "sz": "321",
    }


def test_parse_x_verifiable_intent_rejects_non_sha256_hex() -> None:
    with pytest.raises(HeaderError):
        parse_x_verifiable_intent(
            "vi.v1;ref=tl://evidence/vi_123;sha256=sha256:nothex;mt=application/json;sz=1"
        )


def test_extract_verifiable_intent_evidence_merges_reference_and_body() -> None:
    evidence = extract_verifiable_intent_evidence(
        f"vi.v1;ref=tl://evidence/header;sha256={_VI_HASH};mt=application/json;sz=10",
        {
            "presentationRef": "tl://evidence/body",
            "claims": {"purpose": "market-data"},
            "metadata": {"sessionId": "vi_sess_1"},
        },
    )

    assert evidence is not None
    assert evidence["presentationRef"] == "tl://evidence/body"
    assert evidence["presentationHash"] == _VI_HASH
    assert evidence["claims"]["purpose"] == "market-data"
    assert evidence["metadata"]["referenceHeader"]["mime"] == "application/json"
    assert evidence["metadata"]["sessionId"] == "vi_sess_1"


def test_extract_vi_policy_reads_payment_requirements_and_overrides() -> None:
    policy = extract_vi_policy(
        _body(
            extra={
                "vi": {
                    "requireVerifiableIntent": True,
                    "requirePaymentMandate": True,
                    "reviewMode": "review",
                }
            }
        ).paymentRequirements,
        {"reviewMode": "block"},
    )

    assert policy.require_verifiable_intent is True
    assert policy.require_payment_mandate is True
    assert policy.review_mode == "block"


def test_extract_vi_policy_rejects_invalid_review_mode() -> None:
    with pytest.raises(HTTPException) as exc:
        extract_vi_policy(_body().paymentRequirements, {"reviewMode": "bad"})

    assert exc.value.status_code == 422
    assert "Invalid VI policy" in str(exc.value.detail)


def test_build_public_payment_context_hashes_payload_and_requirements() -> None:
    context = build_public_payment_context(_body(), _request(), "https://merchant.example")

    assert context["chain"] == "base-sepolia"
    assert context["protocol"] == "x402"
    assert context["scheme"] == "exact"
    assert context["amount"] == "1000000"
    assert context["destination"] == "0x" + ("c" * 40)
    assert context["payer"] == "0x" + ("b" * 40)
    assert context["payloadHash"].startswith("sha256:")
    assert context["paymentRequirementsHash"].startswith("sha256:")


def test_build_public_vi_assessment_payload_matches_trustline_shape() -> None:
    body = _body(
        extra={"vi": {"requireVerifiableIntent": True, "reviewMode": "block"}},
        verifiableIntent={
            "presentationRef": "tl://evidence/body",
            "claims": {"purpose": "market-data"},
        },
        ap2Context={"paymentMandateRef": "tl://mandate/payment_1"},
    )

    payload = build_public_vi_assessment_payload(
        body,
        _request(),
        vi_header=(
            f"vi.v1;ref=tl://evidence/header;sha256={_VI_HASH};" "mt=application/json;sz=10"
        ),
        trace_context={"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"},
        risk_session="risk_sess_1",
        risk_trace="risk_trace_1",
        origin="https://merchant.example",
    )

    assert payload["policy"]["requireVerifiableIntent"] is True
    assert payload["merchantId"] == "did:web:merchant.example"
    assert payload["verifiableIntent"]["presentationRef"] == "tl://evidence/body"
    assert payload["ap2Context"]["paymentMandateRef"] == "tl://mandate/payment_1"
    assert payload["binding"]["paymentBound"] is True
    assert payload["binding"]["hashes"]["paymentPayloadHash"].startswith("sha256:")
    assert payload["metadata"]["source"] == "x402_secure_public_gateway"


def test_proxy_verify_calls_trustline_when_vi_policy_required(monkeypatch) -> None:
    client = _proxy_client(monkeypatch)
    sid = _risk_session(client)
    captured: Dict[str, Any] = {}

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        captured["path"] = path
        captured["payload"] = payload
        return {
            "decision": "allow",
            "decision_id": "vi_dec_1",
            "risk_level": "low",
            "vi": {"verified": True, "evidence_ref": "tl_evd_1"},
            "binding": payload["binding"],
        }

    monkeypatch.setattr("x402_proxy.routes.post_trustline_validation", fake_post)

    response = client.post(
        "/x402/verify",
        json=_body_json(
            _body(
                extra={"vi": {"requireVerifiableIntent": True, "reviewMode": "block"}},
                verifiableIntent={"presentationRef": "tl://evidence/body"},
            )
        ),
        headers={
            "X-PAYMENT-SECURE": f"w3c.v1;tp={_TRACEPARENT}",
            "X-RISK-SESSION": sid,
            "Origin": "https://merchant.example",
        },
    )

    assert response.status_code == 200
    assert response.headers["x-vi-decision"] == "allow"
    assert response.headers["x-vi-decision-id"] == "vi_dec_1"
    assert response.headers["x-risk-decision"] == "allow"
    assert response.headers["x-risk-decision-id"] == "vi_dec_1"
    assert response.headers["x-vi-verified"] == "true"
    assert response.headers["x-vi-evidence-ref"] == "tl_evd_1"
    assert captured["path"] == "assess-verifiable-intent"
    assert captured["payload"]["policy"]["requireVerifiableIntent"] is True
    assert captured["payload"]["paymentContext"]["paymentRequirementsHash"].startswith("sha256:")


def test_proxy_verify_fails_fast_when_required_vi_missing(monkeypatch) -> None:
    client = _proxy_client(monkeypatch)
    sid = _risk_session(client)

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        raise AssertionError("Trustline should not be called when required VI is missing")

    monkeypatch.setattr("x402_proxy.routes.post_trustline_validation", fake_post)

    response = client.post(
        "/x402/verify",
        json=_body_json(
            _body(extra={"vi": {"requireVerifiableIntent": True, "reviewMode": "block"}})
        ),
        headers={
            "X-PAYMENT-SECURE": f"w3c.v1;tp={_TRACEPARENT}",
            "X-RISK-SESSION": sid,
            "Origin": "https://merchant.example",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VI_EVIDENCE_MISSING"


def test_proxy_verify_fails_fast_when_required_vi_has_claims_only(monkeypatch) -> None:
    client = _proxy_client(monkeypatch)
    sid = _risk_session(client)

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        raise AssertionError("Trustline should not be called when VI evidence is claims-only")

    monkeypatch.setattr("x402_proxy.routes.post_trustline_validation", fake_post)

    response = client.post(
        "/x402/verify",
        json=_body_json(
            _body(
                extra={"vi": {"requireVerifiableIntent": True, "reviewMode": "block"}},
                verifiableIntent={"claims": {"purpose": "market-data"}},
            )
        ),
        headers={
            "X-PAYMENT-SECURE": f"w3c.v1;tp={_TRACEPARENT}",
            "X-RISK-SESSION": sid,
            "Origin": "https://merchant.example",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VI_EVIDENCE_MISSING"


def test_proxy_verify_fails_fast_when_verified_vi_missing(monkeypatch) -> None:
    client = _proxy_client(monkeypatch)
    sid = _risk_session(client)

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        raise AssertionError("Trustline should not be called when verified VI is missing")

    monkeypatch.setattr("x402_proxy.routes.post_trustline_validation", fake_post)

    response = client.post(
        "/x402/verify",
        json=_body_json(_body(extra={"vi": {"requireVerifiedIntent": True}})),
        headers={
            "X-PAYMENT-SECURE": f"w3c.v1;tp={_TRACEPARENT}",
            "X-RISK-SESSION": sid,
            "Origin": "https://merchant.example",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VI_EVIDENCE_MISSING"


def test_proxy_verify_maps_ap2_evidence_header_into_trustline_payload(monkeypatch) -> None:
    client = _proxy_client(monkeypatch)
    sid = _risk_session(client)
    captured: Dict[str, Any] = {}

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        captured["payload"] = payload
        return {"decision": "allow", "decision_id": "vi_dec_ap2", "binding": payload["binding"]}

    monkeypatch.setattr("x402_proxy.routes.post_trustline_validation", fake_post)

    response = client.post(
        "/x402/verify",
        json=_body_json(
            _body(
                extra={
                    "vi": {
                        "requireVerifiableIntent": True,
                        "requirePaymentMandate": True,
                    }
                },
                verifiableIntent={"presentationRef": "tl://evidence/body"},
            )
        ),
        headers={
            "X-PAYMENT-SECURE": f"w3c.v1;tp={_TRACEPARENT}",
            "X-RISK-SESSION": sid,
            "X-AP2-EVIDENCE": (
                "evd.v1;mr=tl://mandate/payment_1;ms=b64urlhash;" "mt=application/json;sz=10"
            ),
            "Origin": "https://merchant.example",
        },
    )

    assert response.status_code == 200
    assert captured["payload"]["ap2Context"]["paymentMandateRef"] == "tl://mandate/payment_1"
    assert captured["payload"]["ap2Context"]["paymentMandateHash"] == "b64urlhash"


def test_proxy_verify_blocks_trustline_review_with_block_policy(monkeypatch) -> None:
    client = _proxy_client(monkeypatch)
    sid = _risk_session(client)

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "decision": "review",
            "decision_id": "vi_dec_review",
            "reasons": ["Manual review required"],
        }

    monkeypatch.setattr("x402_proxy.routes.post_trustline_validation", fake_post)

    response = client.post(
        "/x402/verify",
        json=_body_json(
            _body(
                extra={"vi": {"requireVerifiableIntent": True, "reviewMode": "block"}},
                verifiableIntent={"presentationRef": "tl://evidence/body"},
            )
        ),
        headers={
            "X-PAYMENT-SECURE": f"w3c.v1;tp={_TRACEPARENT}",
            "X-RISK-SESSION": sid,
            "Origin": "https://merchant.example",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "VI_DENIED"


def test_proxy_settle_ignores_decision_header_without_vi_assessment(monkeypatch) -> None:
    client = _proxy_client(monkeypatch)

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        raise AssertionError("client-supplied decision ids must not create VI receipts")

    monkeypatch.setattr("x402_proxy.routes.post_trustline_validation", fake_post)

    response = client.post(
        "/x402/settle",
        json=_body_json(_body()),
        headers={
            "X-VI-DECISION-ID": "vi_dec_1",
            "X-VI-EVIDENCE-REF": "tl_evd_1",
            "X-SETTLEMENT-ATTEMPT-ID": "settle_attempt_1",
            "X-IDEMPOTENCY-KEY": "idem_1",
            "Origin": "https://merchant.example",
        },
    )

    assert response.status_code == 200
    assert "x-vi-receipt-status" not in response.headers


def test_proxy_settle_without_vi_does_not_call_trustline(monkeypatch) -> None:
    client = _proxy_client(monkeypatch)

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        raise AssertionError("Trustline should not be called for non-VI settle")

    monkeypatch.setattr("x402_proxy.routes.post_trustline_validation", fake_post)

    response = client.post(
        "/x402/settle",
        json=_body_json(_body()),
        headers={"Origin": "https://merchant.example"},
    )

    assert response.status_code == 200
    assert "x-vi-receipt-status" not in response.headers


def test_proxy_settle_receipt_failure_is_non_blocking(monkeypatch) -> None:
    client = _proxy_client(monkeypatch)
    sid = _risk_session(client)
    calls = []

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        calls.append(path)
        if path == "assess-verifiable-intent":
            return {
                "decision": "allow",
                "decision_id": "vi_dec_1",
                "vi": {"verified": True, "evidence_ref": "tl_evd_1"},
                "binding": payload["binding"],
            }
        raise HTTPException(status_code=502, detail="Trustline unavailable")

    monkeypatch.setattr("x402_proxy.routes.post_trustline_validation", fake_post)

    response = client.post(
        "/x402/settle",
        json=_body_json(
            _body(
                extra={"vi": {"requireVerifiableIntent": True, "reviewMode": "block"}},
                verifiableIntent={"presentationRef": "tl://evidence/body"},
            )
        ),
        headers={
            "X-PAYMENT-SECURE": f"w3c.v1;tp={_TRACEPARENT}",
            "X-RISK-SESSION": sid,
            "Origin": "https://merchant.example",
        },
    )

    assert response.status_code == 200
    assert response.headers["x-vi-receipt-status"] == "failed"
    assert response.json()["success"] is True
    assert calls == ["assess-verifiable-intent", "verifiable-intent-receipt"]


def test_proxy_settle_receipt_network_failure_is_non_blocking(monkeypatch) -> None:
    client = _proxy_client(monkeypatch)
    sid = _risk_session(client)
    calls = []

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        calls.append(path)
        if path == "assess-verifiable-intent":
            return {
                "decision": "allow",
                "decision_id": "vi_dec_1",
                "vi": {"verified": True, "evidence_ref": "tl_evd_1"},
                "binding": payload["binding"],
            }
        raise httpx.ConnectError("Trustline connection failed")

    monkeypatch.setattr("x402_proxy.routes.post_trustline_validation", fake_post)

    response = client.post(
        "/x402/settle",
        json=_body_json(
            _body(
                extra={"vi": {"requireVerifiableIntent": True, "reviewMode": "block"}},
                verifiableIntent={"presentationRef": "tl://evidence/body"},
            )
        ),
        headers={
            "X-PAYMENT-SECURE": f"w3c.v1;tp={_TRACEPARENT}",
            "X-RISK-SESSION": sid,
            "Origin": "https://merchant.example",
        },
    )

    assert response.status_code == 200
    assert response.headers["x-vi-receipt-status"] == "failed"
    assert response.json()["success"] is True
    assert calls == ["assess-verifiable-intent", "verifiable-intent-receipt"]


def test_proxy_settle_assesses_vi_when_policy_present_without_decision_header(
    monkeypatch,
) -> None:
    client = _proxy_client(monkeypatch)
    sid = _risk_session(client)
    calls = []

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        calls.append((path, payload))
        if path == "assess-verifiable-intent":
            return {
                "decision": "allow",
                "decision_id": "vi_dec_settle",
                "vi": {"verified": True, "evidence_ref": "tl_evd_settle"},
                "binding": payload["binding"],
            }
        return {
            "receipt_id": "vi_rcpt_settle",
            "decision_id": payload["decisionId"],
            "status": "success",
        }

    monkeypatch.setattr("x402_proxy.routes.post_trustline_validation", fake_post)

    response = client.post(
        "/x402/settle",
        json=_body_json(
            _body(
                extra={"vi": {"requireVerifiableIntent": True, "reviewMode": "block"}},
                verifiableIntent={"presentationRef": "tl://evidence/body"},
            )
        ),
        headers={
            "X-PAYMENT-SECURE": f"w3c.v1;tp={_TRACEPARENT}",
            "X-RISK-SESSION": sid,
            "X-VI-EVIDENCE-REF": "client_supplied_evidence",
            "Origin": "https://merchant.example",
        },
    )

    assert response.status_code == 200
    assert response.headers["x-vi-decision"] == "allow"
    assert response.headers["x-vi-decision-id"] == "vi_dec_settle"
    assert response.headers["x-risk-decision"] == "allow"
    assert response.headers["x-risk-decision-id"] == "vi_dec_settle"
    assert response.headers["x-vi-receipt-status"] == "recorded"
    assert [path for path, _payload in calls] == [
        "assess-verifiable-intent",
        "verifiable-intent-receipt",
    ]
    assert calls[1][1]["decisionId"] == "vi_dec_settle"
    assert calls[1][1]["evidenceRef"] == "tl_evd_settle"
    assert calls[1][1]["payment"]["status"] == "success"
    assert calls[1][1]["payment"]["settlementStatus"] == "success"


def test_proxy_settle_decision_header_does_not_bypass_required_vi_assessment(
    monkeypatch,
) -> None:
    client = _proxy_client(monkeypatch)
    sid = _risk_session(client)
    calls = []

    async def fake_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        calls.append((path, payload))
        if path == "assess-verifiable-intent":
            return {
                "decision": "deny",
                "decision_id": "vi_dec_denied",
                "reasons": ["policy denied"],
                "vi": {"verified": False, "evidence_ref": "tl_evd_denied"},
                "binding": payload["binding"],
            }
        raise AssertionError("receipt should not be recorded after denied VI assessment")

    monkeypatch.setattr("x402_proxy.routes.post_trustline_validation", fake_post)

    response = client.post(
        "/x402/settle",
        json=_body_json(
            _body(
                extra={"vi": {"requireVerifiableIntent": True, "reviewMode": "block"}},
                verifiableIntent={"presentationRef": "tl://evidence/body"},
            )
        ),
        headers={
            "X-PAYMENT-SECURE": f"w3c.v1;tp={_TRACEPARENT}",
            "X-RISK-SESSION": sid,
            "X-VI-DECISION-ID": "client_supplied_decision",
            "Origin": "https://merchant.example",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "VI_DENIED"
    assert [path for path, _payload in calls] == ["assess-verifiable-intent"]
