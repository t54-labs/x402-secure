# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
"""
Test x402 client SDK functionality.
"""

from unittest.mock import Mock, patch

import pytest


class TestHeaderHelpers:
    """Test VI/AP2 SDK header helpers."""

    def test_build_verifiable_intent_header_from_evidence(self):
        from x402_secure_client import build_verifiable_intent_header

        headers = build_verifiable_intent_header(
            reference="tl://evidence/vi_1",
            evidence={"claims": {"purpose": "market-data"}},
            media_type="application/json",
        )

        value = headers["X-VERIFIABLE-INTENT"]
        assert value.startswith("vi.v1;ref=tl://evidence/vi_1;sha256=sha256:")
        assert ";mt=application/json;" in value
        assert value.endswith(";sz=36")

    def test_build_ap2_evidence_header(self):
        from x402_secure_client import build_ap2_evidence_header

        headers = build_ap2_evidence_header(
            mandate_reference="tl://mandate/payment_1",
            mandate_sha256_b64url="abc123",
            size=42,
        )

        assert headers == {
            "X-AP2-EVIDENCE": (
                "evd.v1;mr=tl://mandate/payment_1;ms=abc123;mt=application/json;sz=42"
            )
        }

    def test_attach_verifiable_intent_policy_merges_extra_vi(self):
        from x402_secure_client import attach_verifiable_intent_policy

        original = {"extra": {"name": "USDC", "vi": {"reviewMode": "allow"}}}
        updated = attach_verifiable_intent_policy(
            original,
            {"requireVerifiableIntent": True, "reviewMode": "block"},
        )

        assert updated["extra"]["name"] == "USDC"
        assert updated["extra"]["vi"]["requireVerifiableIntent"] is True
        assert updated["extra"]["vi"]["reviewMode"] == "block"
        assert original["extra"]["vi"]["reviewMode"] == "allow"


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

    async def test_create_session_requires_wallet_for_non_evm_agent_did(self, risk_client):
        """Test DID-style agent ids do not get sent as wallet addresses."""
        with pytest.raises(ValueError, match="wallet_address required"):
            await risk_client.create_session(
                agent_did="did:eip8004:1:0xabc:123",
                app_id="test-app",
            )

    async def test_create_session_defaults_wallet_when_agent_did_is_evm_address(self, risk_client):
        """Test wallet default is kept only for EVM-address agent ids."""
        with patch.object(risk_client.http, "post") as mock_post:
            mock_response = Mock()
            mock_response.json.return_value = {"sid": "925ca6ee-aa4b-4508-955b-10b1c02c69bb"}
            mock_response.raise_for_status = Mock()
            mock_response.headers = {"content-type": "application/json"}
            mock_post.return_value = mock_response

            await risk_client.create_session(agent_did="0x" + "b" * 40)

            payload = mock_post.call_args.kwargs["json"]
            assert payload["wallet_address"] == "0x" + "b" * 40

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

        return SellerClient("http://localhost:8000")

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

    async def test_verify_then_settle_carries_vi_decision_headers(self, seller_client):
        """Test VI decision headers from verify are passed to settle."""
        seller_client.last_vi_headers = {
            "X-VI-DECISION-ID": "stale_decision",
            "X-VI-EVIDENCE-REF": "stale_evidence",
        }
        with patch.object(seller_client.http, "post") as mock_post:
            verify_response = Mock()
            verify_response.json.return_value = {"isValid": True, "payer": "0x" + "b" * 40}
            verify_response.raise_for_status = Mock()
            verify_response.headers = {
                "content-type": "application/json",
                "X-VI-DECISION-ID": "vi_dec_1",
                "X-VI-EVIDENCE-REF": "tl_evd_1",
            }

            settle_response = Mock()
            settle_response.json.return_value = {"success": True, "txHash": "0x" + "e" * 64}
            settle_response.raise_for_status = Mock()
            settle_response.headers = {"content-type": "application/json"}
            mock_post.side_effect = [verify_response, settle_response]

            result = await seller_client.verify_then_settle(
                payment_payload={"from": "0x" + "b" * 40},
                payment_requirements={"merchantName": "Test", "accepts": []},
                x_payment_b64="base64payment",
                origin="http://localhost",
                x_payment_secure="tid=test",
                risk_sid="test-sid",
                x_verifiable_intent=(
                    "vi.v1;ref=tl://evidence/vi_1;sha256=sha256:"
                    + ("a" * 64)
                    + ";mt=application/json;sz=1"
                ),
                verifiable_intent={"presentationRef": "tl://evidence/vi_1"},
                vi_policy={"requireVerifiableIntent": True},
                settlement_attempt_id="settle_attempt_1",
            )

            assert result["success"] is True
            verify_call = mock_post.call_args_list[0].kwargs
            settle_call = mock_post.call_args_list[1].kwargs
            assert verify_call["headers"]["X-VERIFIABLE-INTENT"].startswith("vi.v1")
            assert verify_call["json"]["verifiableIntent"]["presentationRef"] == (
                "tl://evidence/vi_1"
            )
            assert verify_call["json"]["viPolicy"]["requireVerifiableIntent"] is True
            assert settle_call["headers"]["X-VI-DECISION-ID"] == "vi_dec_1"
            assert settle_call["headers"]["X-VI-EVIDENCE-REF"] == "tl_evd_1"
            assert settle_call["headers"]["X-SETTLEMENT-ATTEMPT-ID"] == "settle_attempt_1"

    async def test_settle_does_not_reuse_last_vi_headers_by_default(self, seller_client):
        """Test bare settle calls do not leak a prior verify decision."""
        seller_client.last_vi_headers = {
            "X-VI-DECISION-ID": "stale_decision",
            "X-VI-EVIDENCE-REF": "stale_evidence",
        }
        with patch.object(seller_client.http, "post") as mock_post:
            mock_response = Mock()
            mock_response.json.return_value = {"success": True, "txHash": "0x" + "e" * 64}
            mock_response.raise_for_status = Mock()
            mock_response.headers = {"content-type": "application/json"}
            mock_post.return_value = mock_response

            await seller_client.settle(
                payment_payload={"from": "0x" + "b" * 40},
                payment_requirements={"merchantName": "Test", "accepts": []},
                x_payment_b64="base64payment",
                origin="http://localhost",
                x_payment_secure="tid=test",
                risk_sid="test-sid",
            )

            headers = mock_post.call_args.kwargs["headers"]
            assert "X-VI-DECISION-ID" not in headers
            assert "X-VI-EVIDENCE-REF" not in headers


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
