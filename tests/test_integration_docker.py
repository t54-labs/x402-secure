# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
"""
Integration tests using Docker containers.
"""
import os
import pytest
import httpx
import asyncio
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

# Get URLs from environment
GATEWAY_URL = os.getenv("AGENT_GATEWAY_URL", "http://localhost:8000")
MOCK_FACILITATOR_URL = os.getenv("MOCK_FACILITATOR_URL", "http://localhost:8001")


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def http_client():
    """Create HTTP client for tests."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        yield client


@pytest.fixture
async def wait_for_services():
    """Wait for all services to be ready."""
    services = [
        (GATEWAY_URL, "facilitator-proxy"),
        (MOCK_FACILITATOR_URL, "mock-facilitator")
    ]
    
    async with httpx.AsyncClient() as client:
        for url, name in services:
            health_url = f"{url}/health"
            max_retries = 30
            retry_count = 0
            
            while retry_count < max_retries:
                try:
                    response = await client.get(health_url)
                    if response.status_code == 200:
                        logger.info(f"{name} is ready")
                        break
                except Exception as e:
                    logger.debug(f"Waiting for {name}: {e}")
                
                retry_count += 1
                await asyncio.sleep(1)
            
            if retry_count >= max_retries:
                pytest.fail(f"{name} failed to start after {max_retries} seconds")


@pytest.mark.asyncio
class TestDockerIntegration:
    """Integration tests with real services."""
    
    async def test_health_endpoints(self, http_client: httpx.AsyncClient, wait_for_services):
        """Test that all services are healthy."""
        # Test proxy health
        response = await http_client.get(f"{GATEWAY_URL}/health")
        assert response.status_code == 200
        
        # Test mock facilitator health
        response = await http_client.get(f"{MOCK_FACILITATOR_URL}/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
    
    async def test_create_risk_session(self, http_client: httpx.AsyncClient, wait_for_services):
        """Test creating a risk session through the proxy."""
        response = await http_client.post(
            f"{GATEWAY_URL}/risk/session",
            json={
                "agent_did": "0x" + "a" * 40,
                "app_id": "docker-test",
                "device": {"user_agent": "integration-test/1.0"}
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "sid" in data
        assert "expires_at" in data
        return data["sid"]
    
    async def test_submit_agent_trace(self, http_client: httpx.AsyncClient, wait_for_services):
        """Test submitting agent trace."""
        # First create a session
        sid = await self.test_create_risk_session(http_client, wait_for_services)
        
        # Submit trace
        response = await http_client.post(
            f"{GATEWAY_URL}/risk/trace",
            json={
                "sid": sid,
                "agent_trace": {
                    "task": "Integration test task",
                    "events": [
                        {
                            "timestamp": "2025-10-13T10:00:00Z",
                            "type": "test_event",
                            "data": {"test": True}
                        }
                    ],
                    "model_config": {
                        "model": "test-model",
                        "temperature": 0.5
                    }
                }
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "tid" in data
        return data["tid"]
    
    async def test_payment_verification_flow(self, http_client: httpx.AsyncClient, wait_for_services):
        """Test complete payment verification flow."""
        # Create session
        session_response = await http_client.post(
            f"{GATEWAY_URL}/risk/session",
            json={"agent_did": "0x" + "b" * 40}
        )
        sid = session_response.json()["sid"]
        
        # Submit trace
        trace_response = await http_client.post(
            f"{GATEWAY_URL}/risk/trace",
            json={
                "sid": sid,
                "agent_trace": {
                    "task": "Test payment",
                    "events": []
                }
            }
        )
        tid = trace_response.json()["tid"]
        
        # Prepare payment data
        payment_data = {
            "x402Version": 1,
            "paymentPayload": {
                "x402Version": 1,
                "scheme": "eip3009",
                "network": "base-sepolia",
                "payload": {
                    "authorization": {
                        "from": "0x" + "b" * 40,
                        "to": "0x" + "c" * 40,
                        "value": "1000000",
                        "validAfter": "0",
                        "validBefore": str(2**256 - 1),
                        "nonce": "0x" + "0" * 64
                    },
                    "signature": "0x" + "d" * 130
                }
            },
            "paymentRequirements": {
                "scheme": "eip3009",
                "network": "base-sepolia",
                "maxAmountRequired": "1000000",
                "resource": "https://test.example.com/api/resource",
                "description": "Test payment",
                "mimeType": "application/json",
                "payTo": "0x" + "c" * 40,
                "maxTimeoutSeconds": 300,
                "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",  # USDC on Base Sepolia
                "merchantName": "Docker Test Merchant",
                "merchantDomain": "https://test.example.com"
            }
        }
        
        # Encode payment payload for X-PAYMENT header
        import json
        import base64
        payment_str = json.dumps(payment_data["paymentPayload"], separators=(",", ":"))
        x_payment = base64.b64encode(payment_str.encode()).decode()
        
        # Verify payment
        headers = {
            "X-PAYMENT": x_payment,
            "X-PAYMENT-SECURE": f"w3c.v1;tp=00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01;ts={tid}",
            "X-RISK-SESSION": sid,
            "Origin": "https://test.example.com"
        }
        
        response = await http_client.post(
            f"{GATEWAY_URL}/x402/verify",
            json=payment_data,
            headers=headers
        )
        
        if response.status_code != 200:
            print(f"Verify failed with status {response.status_code}")
            print(f"Response: {response.text}")
        assert response.status_code == 200
        data = response.json()
        assert data["isValid"] is True
        assert data["payer"] == "0x" + "b" * 40
    
    async def test_payment_settlement_flow(self, http_client: httpx.AsyncClient, wait_for_services):
        """Test complete payment settlement flow."""
        # Create session
        session_response = await http_client.post(
            f"{GATEWAY_URL}/risk/session",
            json={"agent_did": "0x" + "b" * 40}
        )
        sid = session_response.json()["sid"]
        
        # Prepare payment data
        payment_data = {
            "x402Version": 1,
            "paymentPayload": {
                "x402Version": 1,
                "scheme": "eip3009",
                "network": "base-sepolia",
                "payload": {
                    "authorization": {
                        "from": "0x" + "b" * 40,
                        "to": "0x" + "c" * 40,
                        "value": "1000000",
                        "validAfter": "0",
                        "validBefore": str(2**256 - 1),
                        "nonce": "0x" + "0" * 64
                    },
                    "signature": "0x" + "d" * 130
                }
            },
            "paymentRequirements": {
                "scheme": "eip3009",
                "network": "base-sepolia",
                "maxAmountRequired": "1000000",
                "resource": "https://test.example.com/api/resource",
                "description": "Test payment",
                "mimeType": "application/json",
                "payTo": "0x" + "c" * 40,
                "maxTimeoutSeconds": 300,
                "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",  # USDC on Base Sepolia
                "merchantName": "Docker Test Merchant",
                "merchantDomain": "https://test.example.com"
            }
        }
        
        # Settle payment
        headers = {
            "X-PAYMENT": "base64encodedpayment",
            "X-PAYMENT-SECURE": "w3c.v1;tp=00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            "X-RISK-SESSION": sid,
            "Origin": "https://test.example.com"
        }
        
        response = await http_client.post(
            f"{GATEWAY_URL}/x402/settle",
            json=payment_data,
            headers=headers
        )
        
        if response.status_code != 200:
            print(f"Settle failed with status {response.status_code}")
            print(f"Response: {response.text}")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["transaction"].startswith("0x")
        assert data["network"] == "base-sepolia"
    
    async def test_risk_evaluation_flow(self, http_client: httpx.AsyncClient, wait_for_services):
        """Test risk evaluation endpoint."""
        # Create session and trace
        session_response = await http_client.post(
            f"{GATEWAY_URL}/risk/session",
            json={"agent_did": "0x" + "b" * 40}
        )
        sid = session_response.json()["sid"]
        
        trace_response = await http_client.post(
            f"{GATEWAY_URL}/risk/trace",
            json={
                "sid": sid,
                "agent_trace": {
                    "task": "Risk evaluation test",
                    "events": []
                }
            }
        )
        tid = trace_response.json()["tid"]
        
        # Evaluate risk
        response = await http_client.post(
            f"{GATEWAY_URL}/risk/evaluate",
            json={
                "sid": sid,
                "tid": tid,
                "trace_context": {
                    "tp": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
                }
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["decision"] == "allow"
        assert "decision_id" in data
        assert data["risk_level"] == "low"  # Local mode always returns low risk


@pytest.mark.asyncio
class TestErrorHandling:
    """Test error handling with Docker setup."""
    
    async def test_invalid_risk_session(self, http_client: httpx.AsyncClient, wait_for_services):
        """Test using invalid risk session."""
        headers = {
            "X-PAYMENT": "base64encodedpayment",
            "X-PAYMENT-SECURE": "w3c.v1;tp=00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            "X-RISK-SESSION": "invalid-session-id",
            "Origin": "https://test.example.com"
        }
        
        payment_data = {
            "x402Version": 1,
            "paymentPayload": {"from": "0x" + "b" * 40},
            "paymentRequirements": {"merchantName": "Test"}
        }
        
        response = await http_client.post(
            f"{GATEWAY_URL}/x402/verify",
            json=payment_data,
            headers=headers
        )
        
        # Should fail with invalid session
        assert response.status_code >= 400
    
    async def test_missing_headers(self, http_client: httpx.AsyncClient, wait_for_services):
        """Test request with missing required headers."""
        payment_data = {
            "x402Version": 1,
            "paymentPayload": {"from": "0x" + "b" * 40},
            "paymentRequirements": {"merchantName": "Test"}
        }
        
        # Missing all required headers
        response = await http_client.post(
            f"{GATEWAY_URL}/x402/verify",
            json=payment_data
        )
        
        assert response.status_code >= 400
