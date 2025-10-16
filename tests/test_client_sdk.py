# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
"""
Test x402 client SDK functionality.
"""
import pytest
from unittest.mock import AsyncMock, patch, Mock
import httpx
from datetime import datetime, timezone


@pytest.mark.asyncio
class TestBuyerClient:
    """Test buyer client functionality."""
    
    @pytest.fixture
    def buyer_client(self, test_env):
        """Create buyer client instance."""
        from x402_secure_client.buyer import BuyerClient
        return BuyerClient(
            gateway_url="http://localhost:8000",
            signing_key="0x" + "a" * 64,
            buyer_address="0x" + "b" * 40
        )
    
    async def test_create_risk_session(self, buyer_client):
        """Test creating risk session."""
        with patch.object(buyer_client.http, 'post') as mock_post:
            mock_response = Mock()
            mock_response.json.return_value = {
                "sid": "925ca6ee-aa4b-4508-955b-10b1c02c69bb",
                "expires_at": "2025-12-31T23:59:59Z"
            }
            mock_response.raise_for_status = Mock()
            mock_post.return_value = mock_response
            
            session = await buyer_client.create_risk_session(
                app_id="test-app",
                device={"user_agent": "x402-agent/1.0"}
            )
            
            assert session["sid"] == "925ca6ee-aa4b-4508-955b-10b1c02c69bb"
            mock_post.assert_called_once()
    
    async def test_submit_agent_trace(self, buyer_client):
        """Test submitting agent trace."""
        with patch.object(buyer_client.http, 'post') as mock_post:
            mock_response = Mock()
            mock_response.json.return_value = {
                "tid": "af88271b-e93d-4998-bc15-2f130d437262"
            }
            mock_response.raise_for_status = Mock()
            mock_post.return_value = mock_response
            
            trace_id = await buyer_client.submit_agent_trace(
                sid="925ca6ee-aa4b-4508-955b-10b1c02c69bb",
                agent_trace={
                    "task": "Get weather",
                    "events": []
                }
            )
            
            assert trace_id == "af88271b-e93d-4998-bc15-2f130d437262"
    
    async def test_make_payment(self, buyer_client):
        """Test making a payment."""
        with patch.object(buyer_client.http, 'post') as mock_post:
            # Mock verify response
            verify_response = Mock()
            verify_response.json.return_value = {
                "isValid": True,
                "payer": "0x" + "b" * 40
            }
            verify_response.raise_for_status = Mock()
            
            # Mock settle response
            settle_response = Mock()
            settle_response.json.return_value = {
                "success": True,
                "transaction": "0x" + "e" * 64,
                "network": "base-sepolia"
            }
            settle_response.raise_for_status = Mock()
            
            mock_post.side_effect = [verify_response, settle_response]
            
            result = await buyer_client.make_payment(
                payment_requirements={
                    "merchantName": "Test Merchant",
                    "merchantDomain": "https://test.example.com",
                    "accepts": [{
                        "chain": "base-sepolia",
                        "currency": "USDC",
                        "receiver": "0x" + "c" * 40,
                        "requiredAmount": "1000000"
                    }]
                },
                sid="925ca6ee-aa4b-4508-955b-10b1c02c69bb",
                tid="af88271b-e93d-4998-bc15-2f130d437262"
            )
            
            assert result["success"] is True
            assert result["transaction"].startswith("0x")


@pytest.mark.asyncio
class TestSellerClient:
    """Test seller client functionality."""
    
    @pytest.fixture
    def seller_client(self, test_env):
        """Create seller client instance."""
        from x402_secure_client.seller import SellerClient
        return SellerClient(
            merchant_name="Test Merchant",
            merchant_domain="https://test.example.com"
        )
    
    def test_create_payment_requirements(self, seller_client):
        """Test creating payment requirements."""
        requirements = seller_client.create_payment_requirements(
            accepts=[{
                "chain": "base-sepolia",
                "currency": "USDC",
                "receiver": "0x" + "c" * 40,
                "requiredAmount": "1000000"
            }]
        )
        
        assert requirements["merchantName"] == "Test Merchant"
        assert requirements["merchantDomain"] == "https://test.example.com"
        assert len(requirements["accepts"]) == 1
        assert requirements["accepts"][0]["chain"] == "base-sepolia"
    
    def test_validate_payment_headers(self, seller_client):
        """Test validating payment headers."""
        headers = {
            "X-PAYMENT": "base64encodedpayment",
            "X-PAYMENT-SECURE": "w3c.v1;tp=00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            "X-RISK-SESSION": "925ca6ee-aa4b-4508-955b-10b1c02c69bb"
        }
        
        # Should not raise exception
        seller_client.validate_payment_headers(headers)
        
        # Test missing header
        with pytest.raises(ValueError):
            seller_client.validate_payment_headers({})


@pytest.mark.asyncio
class TestAgentIntegration:
    """Test AI agent integration features."""
    
    @pytest.fixture
    def agent_client(self, test_env):
        """Create agent-enabled buyer client."""
        from x402_secure_client.agent import AgentBuyerClient
        return AgentBuyerClient(
            gateway_url="http://localhost:8000",
            signing_key="0x" + "a" * 64,
            buyer_address="0x" + "b" * 40,
            agent_id="test-agent-001"
        )
    
    async def test_agent_trace_builder(self, agent_client):
        """Test building agent trace."""
        trace_builder = agent_client.create_trace_builder()
        
        # Add events
        trace_builder.add_event({
            "type": "tool_call",
            "tool": "get_price",
            "arguments": {"symbol": "BTC/USD"},
            "result": {"price": 63500.12}
        })
        
        trace_builder.add_event({
            "type": "reasoning",
            "content": "Price is reasonable for purchase"
        })
        
        trace = trace_builder.build(
            task="Buy BTC price information",
            model_config={
                "model": "gpt-4",
                "temperature": 0.7
            }
        )
        
        assert trace["task"] == "Buy BTC price information"
        assert len(trace["events"]) == 2
        assert trace["events"][0]["type"] == "tool_call"
        assert trace["model_config"]["model"] == "gpt-4"
    
    async def test_agent_payment_with_mandate(self, agent_client):
        """Test agent payment with AP2 mandate."""
        with patch.object(agent_client.http, 'post') as mock_post:
            # Mock all responses
            session_response = Mock()
            session_response.json.return_value = {
                "sid": "925ca6ee-aa4b-4508-955b-10b1c02c69bb",
                "expires_at": "2025-12-31T23:59:59Z"
            }
            session_response.raise_for_status = Mock()
            
            trace_response = Mock()
            trace_response.json.return_value = {
                "tid": "af88271b-e93d-4998-bc15-2f130d437262"
            }
            trace_response.raise_for_status = Mock()
            
            verify_response = Mock()
            verify_response.json.return_value = {
                "isValid": True,
                "payer": "0x" + "b" * 40
            }
            verify_response.raise_for_status = Mock()
            
            settle_response = Mock()
            settle_response.json.return_value = {
                "success": True,
                "transaction": "0x" + "e" * 64
            }
            settle_response.raise_for_status = Mock()
            
            mock_post.side_effect = [
                session_response,
                trace_response,
                verify_response,
                settle_response
            ]
            
            # Create trace
            trace_builder = agent_client.create_trace_builder()
            trace_builder.add_event({
                "type": "user_request",
                "content": "Get BTC price"
            })
            
            # Make payment with trace
            result = await agent_client.make_agent_payment(
                payment_requirements={
                    "merchantName": "Test Merchant",
                    "merchantDomain": "https://test.example.com",
                    "accepts": [{
                        "chain": "base-sepolia",
                        "currency": "USDC",
                        "receiver": "0x" + "c" * 40,
                        "requiredAmount": "1000000"
                    }]
                },
                trace_builder=trace_builder,
                task="Get BTC price",
                mandate_reference="mandates/test/mandate.json"
            )
            
            assert result["success"] is True
            assert mock_post.call_count == 4  # session, trace, verify, settle
