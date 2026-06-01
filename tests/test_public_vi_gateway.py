from __future__ import annotations

from typing import Any, Dict, Optional

import pytest
from fastapi import HTTPException
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
            f"vi.v1;ref=tl://evidence/header;sha256={_VI_HASH};"
            "mt=application/json;sz=10"
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
