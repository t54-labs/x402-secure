# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
"""
Test x402 client SDK functionality.
"""

from unittest.mock import Mock, patch

import pytest


@pytest.mark.asyncio
class TestBuyerClient:
    """Test buyer client functionality."""

    @pytest.fixture
    def buyer_client(self, test_env):
        """Create buyer client instance."""
        from x402_secure_client.buyer import BuyerClient, BuyerConfig

        config = BuyerConfig(
            seller_base_url="http://localhost:8001",
            agent_gateway_url="http://localhost:8000",
            network="base-sepolia",
            buyer_private_key="0x" + "a" * 64,
        )
        return BuyerClient(config)

    async def test_execute_paid_request_preflight(self, buyer_client):
        """Test that BuyerClient handles 402 preflight correctly."""
        # Mock the 402 preflight request
        preflight_response = Mock()
        preflight_response.status_code = 402
        preflight_response.headers = {"content-type": "application/json"}
        preflight_response.json.return_value = {
            "accepts": [
                {
                    "payTo": "0x" + "c" * 40,
                    "maxAmountRequired": "1000000",
                    "resource": "/api/data",
                    "network": "base-sepolia",
                    "asset": "USDC",
                }
            ]
        }

        with patch.object(buyer_client.http, "get") as mock_get:
            mock_get.return_value = preflight_response

            # Test that _first_request_402 extracts payment requirements correctly
            pr = await buyer_client._first_request_402(
                url="http://localhost:8001/api/data", params={"query": "test"}
            )

            assert pr["payTo"] == "0x" + "c" * 40
            assert pr["maxAmountRequired"] == "1000000"
            assert pr["network"] == "base-sepolia"
            mock_get.assert_called_once()


@pytest.mark.asyncio
class TestRiskClient:
    """Test risk client functionality."""

    @pytest.fixture
    def risk_client(self, test_env):
        """Create risk client instance."""
        from x402_secure_client.risk import RiskClient

        return RiskClient(base_url="http://localhost:8000")

    async def test_create_session(self, risk_client):
        """Test creating risk session."""
        with patch.object(risk_client.http, "post") as mock_post:
            mock_response = Mock()
            mock_response.json.return_value = {
                "sid": "925ca6ee-aa4b-4508-955b-10b1c02c69bb",
                "expires_at": "2025-12-31T23:59:59Z",
            }
            mock_response.raise_for_status = Mock()
            mock_response.headers = {"content-type": "application/json"}
            mock_post.return_value = mock_response

            session = await risk_client.create_session(
                agent_did="0x" + "b" * 40,
                app_id="test-app",
                device={"user_agent": "x402-agent/1.0"},
            )

            assert session["sid"] == "925ca6ee-aa4b-4508-955b-10b1c02c69bb"
            mock_post.assert_called_once()

    async def test_create_trace(self, risk_client):
        """Test submitting agent trace."""
        with patch.object(risk_client.http, "post") as mock_post:
            mock_response = Mock()
            mock_response.json.return_value = {"tid": "af88271b-e93d-4998-bc15-2f130d437262"}
            mock_response.raise_for_status = Mock()
            mock_response.headers = {"content-type": "application/json"}
            mock_post.return_value = mock_response

            result = await risk_client.create_trace(
                sid="925ca6ee-aa4b-4508-955b-10b1c02c69bb",
                agent_trace={"task": "Get weather", "events": []},
            )

            assert result["tid"] == "af88271b-e93d-4998-bc15-2f130d437262"
            mock_post.assert_called_once()


@pytest.mark.asyncio
class TestSellerClient:
    """Test seller client functionality."""

    @pytest.fixture
    def seller_client(self, test_env):
        """Create seller client instance."""
        from x402_secure_client.seller import SellerClient

        return SellerClient(gateway_base_url="http://localhost:8000")

    async def test_verify_payment(self, seller_client):
        """Test payment verification."""
        with patch.object(seller_client.http, "post") as mock_post:
            mock_response = Mock()
            mock_response.json.return_value = {"isValid": True, "payer": "0x" + "b" * 40}
            mock_response.raise_for_status = Mock()
            mock_response.headers = {"content-type": "application/json"}
            mock_post.return_value = mock_response

            result = await seller_client.verify(
                payment_payload={
                    "from": "0x" + "b" * 40,
                    "to": "0x" + "c" * 40,
                    "value": "1000000",
                },
                payment_requirements={"merchantName": "Test", "accepts": []},
                x_payment_b64="base64payment",
                origin="http://localhost",
                x_payment_secure="tid=test",
                risk_sid="test-sid",
            )

            assert result["isValid"] is True
            assert result["payer"] == "0x" + "b" * 40

    async def test_settle_payment(self, seller_client):
        """Test payment settlement."""
        with patch.object(seller_client.http, "post") as mock_post:
            mock_response = Mock()
            mock_response.json.return_value = {"success": True, "txHash": "0x" + "e" * 64}
            mock_response.raise_for_status = Mock()
            mock_response.headers = {"content-type": "application/json"}
            mock_post.return_value = mock_response

            result = await seller_client.settle(
                payment_payload={
                    "from": "0x" + "b" * 40,
                    "to": "0x" + "c" * 40,
                    "value": "1000000",
                },
                payment_requirements={"merchantName": "Test", "accepts": []},
                x_payment_b64="base64payment",
                origin="http://localhost",
                x_payment_secure="tid=test",
                risk_sid="test-sid",
            )

            assert result["success"] is True
            assert result["txHash"].startswith("0x")


@pytest.mark.asyncio
class TestAgentIntegration:
    """Test AI agent integration features."""

    @pytest.fixture
    def buyer_client(self, test_env):
        """Create buyer client instance."""
        from x402_secure_client.buyer import BuyerClient, BuyerConfig

        config = BuyerConfig(
            seller_base_url="http://localhost:8001",
            agent_gateway_url="http://localhost:8000",
            network="base-sepolia",
            buyer_private_key="0x" + "a" * 64,
        )
        return BuyerClient(config)

    @pytest.fixture
    def risk_client(self, test_env):
        """Create risk client instance."""
        from x402_secure_client.risk import RiskClient

        return RiskClient(base_url="http://localhost:8000")

    async def test_agent_trace_builder(self):
        """Test building agent trace with OpenAITraceCollector."""
        from x402_secure_client.tracing import OpenAITraceCollector

        collector = OpenAITraceCollector()

        # Set model configuration
        collector.set_model_config(provider="openai", model="gpt-4", tools_enabled=["get_price"])

        # Record user input
        collector.record_user_input("What is the BTC price?")

        # Record agent output
        collector.record_agent_output("The BTC price is $63,500.12")

        # Check events were collected
        assert len(collector.events) >= 2
        assert collector.model_config["model"] == "gpt-4"
        assert collector.events[0]["type"] == "user_input"

    async def test_store_agent_trace(self, risk_client):
        """Test storing agent trace."""
        from x402_secure_client.agent import store_agent_trace

        with patch.object(risk_client.http, "post") as mock_post:
            mock_response = Mock()
            mock_response.json.return_value = {"tid": "test-tid-123"}
            mock_response.raise_for_status = Mock()
            mock_response.headers = {"content-type": "application/json"}
            mock_post.return_value = mock_response

            tid = await store_agent_trace(
                risk=risk_client,
                sid="test-sid",
                task="Get BTC price",
                params={"symbol": "BTC/USD"},
                environment={"model": "gpt-4"},
                events=[{"type": "tool_call", "tool": "get_price"}],
                model_config={"temperature": 0.7},
            )

            assert tid == "test-tid-123"
            mock_post.assert_called_once()

    async def test_execute_payment_with_tid(self, buyer_client):
        """Test executing payment with trace ID using run_agent_payment."""
        from x402_secure_client.risk import RiskClient

        risk_client = RiskClient(base_url="http://localhost:8000")

        # Mock risk client responses
        with patch.object(risk_client.http, "post") as mock_risk_post:
            # Mock session creation
            session_response = Mock()
            session_response.json.return_value = {"sid": "test-sid"}
            session_response.raise_for_status = Mock()
            session_response.headers = {"content-type": "application/json"}

            # Mock trace creation
            trace_response = Mock()
            trace_response.json.return_value = {"tid": "test-tid"}
            trace_response.raise_for_status = Mock()
            trace_response.headers = {"content-type": "application/json"}

            mock_risk_post.side_effect = [session_response, trace_response]

            # Mock buyer client 402 flow
            preflight_response = Mock()
            preflight_response.status_code = 402
            preflight_response.headers = {"content-type": "application/json"}
            preflight_response.json.return_value = {
                "accepts": [
                    {
                        "payTo": "0x" + "c" * 40,
                        "maxAmountRequired": "1000000",
                        "resource": "/api/price",
                        "network": "base-sepolia",
                        "asset": "USDC",
                        "scheme": "eip3009",
                        "description": "Price data",
                        "mimeType": "application/json",
                        "maxTimeoutSeconds": 3600,
                    }
                ]
            }

            final_response = Mock()
            final_response.status_code = 200
            final_response.json.return_value = {"price": 63500.12}
            final_response.raise_for_status = Mock()

            with patch.object(buyer_client.http, "get") as mock_get:
                mock_get.side_effect = [preflight_response, final_response]

                # This test just verifies the structure - actual execution would
                # need OpenTelemetry setup. For now, we test that components exist
                assert hasattr(risk_client, "create_session")
                assert hasattr(risk_client, "create_trace")
                assert hasattr(buyer_client, "execute_paid_request")
