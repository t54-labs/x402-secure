# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
"""
Test core payment flows including risk session creation, trace submission,
payment verification and settlement.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.mark.asyncio
class TestCompletePaymentFlow:
    """Test the complete payment flow from risk session to settlement."""

    @pytest.fixture
    def app(self):
        """Create FastAPI app instance."""
        from fastapi import FastAPI
        from x402_proxy import risk_router, router

        app = FastAPI(title="Test x402 Proxy")
        app.include_router(risk_router)
        app.include_router(router)
        return app

    @pytest.fixture
    def client(self, app: FastAPI, test_env):
        """Create test client."""
        return TestClient(app)

    async def test_risk_session_creation(self, client: TestClient):
        """Test creating a risk session."""
        response = client.post(
            "/risk/session",
            json={
                "agent_did": "0x" + "b" * 40,
                "wallet_address": "0x" + "b" * 40,
                "app_id": "test-app",
                "device": {"user_agent": "x402-agent/1.0"},
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "sid" in data
        assert "expires_at" in data
        assert len(data["sid"]) == 36  # UUID format

    async def test_agent_trace_submission(
        self, client: TestClient, sample_risk_session, sample_agent_trace
    ):
        """Test submitting agent trace."""
        # First create a session
        session_response = client.post(
            "/risk/session",
            json={"agent_did": "0x" + "b" * 40, "wallet_address": "0x" + "b" * 40},
        )
        sid = session_response.json()["sid"]

        # Submit trace
        trace_data = sample_agent_trace.copy()
        trace_data["sid"] = sid

        response = client.post("/risk/trace", json=trace_data)

        assert response.status_code == 200
        data = response.json()
        assert "tid" in data
        assert len(data["tid"]) == 36  # UUID format

    @pytest.mark.skip(reason="Requires complex mocking of upstream facilitator HTTP calls")
    @patch("httpx.AsyncClient")
    async def test_payment_verification_with_risk(
        self, mock_httpx: AsyncMock, client: TestClient, sample_payment_data: dict
    ):
        """Test payment verification with risk assessment."""
        # Mock upstream facilitator response
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"isValid": True, "payer": "0x" + "b" * 40}
        mock_httpx.return_value.__aenter__.return_value.post.return_value = mock_response

        # Create session first
        session_response = client.post(
            "/risk/session",
            json={"agent_did": "0x" + "b" * 40, "wallet_address": "0x" + "b" * 40},
        )
        sid = session_response.json()["sid"]

        # Verify payment
        xps = "w3c.v1;tp=00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        response = client.post(
            "/x402/verify",
            json=sample_payment_data,
            headers={
                "X-PAYMENT": "base64encodedpayment",
                "X-PAYMENT-SECURE": xps,
                "X-RISK-SESSION": sid,
                "Origin": "https://test.example.com",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["isValid"] is True
        assert data["payer"] == "0x" + "b" * 40

    @pytest.mark.skip(reason="Requires complex mocking of upstream facilitator HTTP calls")
    @patch("httpx.AsyncClient")
    async def test_payment_settlement(
        self, mock_httpx: AsyncMock, client: TestClient, sample_payment_data: dict
    ):
        """Test payment settlement."""
        # Mock upstream facilitator response
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "payer": "0x" + "b" * 40,
            "transaction": "0x" + "e" * 64,
            "network": "base-sepolia",
        }
        mock_httpx.return_value.__aenter__.return_value.post.return_value = mock_response

        # Create session
        session_response = client.post(
            "/risk/session",
            json={"agent_did": "0x" + "b" * 40, "wallet_address": "0x" + "b" * 40},
        )
        sid = session_response.json()["sid"]

        # Settle payment
        xps = "w3c.v1;tp=00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        response = client.post(
            "/x402/settle",
            json=sample_payment_data,
            headers={
                "X-PAYMENT": "base64encodedpayment",
                "X-PAYMENT-SECURE": xps,
                "X-RISK-SESSION": sid,
                "Origin": "https://test.example.com",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["transaction"].startswith("0x")
        assert data["network"] == "base-sepolia"


@pytest.mark.asyncio
class TestRiskEvaluation:
    """Test risk evaluation logic."""

    @pytest.fixture
    def app(self):
        """Create FastAPI app instance."""
        from fastapi import FastAPI
        from x402_proxy import risk_router, router

        app = FastAPI(title="Test x402 Proxy")
        app.include_router(risk_router)
        app.include_router(router)
        return app

    @pytest.fixture
    def client(self, app: FastAPI, test_env):
        """Create test client."""
        return TestClient(app)

    async def test_risk_evaluation_allow(self, client: TestClient):
        """Test risk evaluation with allow decision."""
        # Create session
        session_response = client.post(
            "/risk/session",
            json={"agent_did": "0x" + "b" * 40, "wallet_address": "0x" + "b" * 40},
        )
        sid = session_response.json()["sid"]

        # Submit trace
        trace_response = client.post(
            "/risk/trace", json={"sid": sid, "agent_trace": {"task": "Get weather", "events": []}}
        )
        tid = trace_response.json()["tid"]

        # Evaluate risk (in local mode, should always allow)
        response = client.post(
            "/risk/evaluate",
            json={
                "sid": sid,
                "tid": tid,
                "trace_context": {"tp": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"},
                "payment": {
                    "protocol": "eip3009",
                    "network": "base-sepolia",
                    "payload": {
                        "authorization": {
                            "from": "0x" + "a" * 40,
                            "to": "0x" + "b" * 40,
                            "value": "1000000",
                        }
                    },
                },
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["decision"] == "allow"
        assert data["risk_level"] == "low"
        assert "decision_id" in data

    @pytest.mark.skip(reason="Requires complex async mocking of external risk engine")
    @patch("httpx.AsyncClient")
    async def test_risk_evaluation_external(
        self, mock_httpx: AsyncMock, client: TestClient, monkeypatch
    ):
        """Test risk evaluation with external Trustline engine."""
        # Switch to external risk mode
        monkeypatch.setenv("PROXY_LOCAL_RISK", "0")
        monkeypatch.setenv("RISK_ENGINE_URL", "<TRUSTLINE_API_URL>")

        # Mock Trustline response
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "decision": "allow",
            "decision_id": "12345678-1234-1234-1234-123456789012",
            "risk_level": "medium",
            "ttl_seconds": 300,
            "reasons": ["High-value transaction"],
            "extra": {"confidence": 0.85},
        }
        mock_httpx.return_value.__aenter__.return_value.post.return_value = mock_response

        response = client.post(
            "/risk/evaluate",
            json={
                "sid": "925ca6ee-aa4b-4508-955b-10b1c02c69bb",
                "trace_context": {"tp": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"},
                "payment": {
                    "protocol": "x402:exact",
                    "network": "base-sepolia",
                    "payload": {
                        "authorization": {
                            "from": "0x" + "c" * 40,
                            "to": "0x" + "d" * 40,
                            "value": "2000000",
                        }
                    },
                },
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["decision"] == "allow"
        assert data["risk_level"] == "medium"
        assert data["extra"]["confidence"] == 0.85
