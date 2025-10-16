# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
import os
import sys
import asyncio
import pytest
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, Mock
import httpx


def _add_project_root_to_syspath() -> None:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if root not in sys.path:
        sys.path.insert(0, root)


_add_project_root_to_syspath()


# Import after adding to syspath
from fastapi.testclient import TestClient
from fastapi import FastAPI


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_httpx_client() -> AsyncMock:
    """Mock httpx AsyncClient for testing external API calls."""
    client = AsyncMock(spec=httpx.AsyncClient)
    return client


@pytest.fixture
def test_env(monkeypatch) -> None:
    """Set up test environment variables."""
    # Basic proxy config
    monkeypatch.setenv("AGENT_GATEWAY_URL", "http://localhost:8000")
    monkeypatch.setenv("UPSTREAM_FACILITATOR_BASE_URL", "https://facilitator.example.com")
    
    # Local risk mode for testing
    monkeypatch.setenv("PROXY_LOCAL_RISK", "1")
    
    # Test buyer config
    monkeypatch.setenv("BUYER_SIGNING_KEY", "0x" + "a" * 64)
    monkeypatch.setenv("BUYER_ADDRESS", "0x" + "b" * 40)
    
    # Test seller config
    monkeypatch.setenv("SELLER_MERCHANT_NAME", "Test Merchant")
    monkeypatch.setenv("SELLER_MERCHANT_DOMAIN", "https://test.example.com")


@pytest.fixture
def sample_payment_data() -> dict:
    """Sample payment data for testing."""
    return {
        "x402Version": 1,
        "paymentPayload": {
            "from": "0x" + "b" * 40,
            "to": "0x" + "c" * 40,
            "value": "1000000",  # 1 USDC
            "validAfter": "0",
            "validBefore": str(2**256 - 1),
            "nonce": "0x" + "0" * 64,
            "signature": "0x" + "d" * 130
        },
        "paymentRequirements": {
            "merchantName": "Test Merchant",
            "merchantDomain": "https://test.example.com",
            "accepts": [
                {
                    "chain": "base-sepolia",
                    "currency": "USDC",
                    "receiver": "0x" + "c" * 40,
                    "requiredAmount": "1000000"
                }
            ]
        }
    }


@pytest.fixture
def sample_risk_session() -> dict:
    """Sample risk session data."""
    return {
        "sid": "925ca6ee-aa4b-4508-955b-10b1c02c69bb",
        "agent_id": "0x" + "b" * 40,
        "expires_at": "2025-12-31T23:59:59Z"
    }


@pytest.fixture
def sample_agent_trace() -> dict:
    """Sample agent trace data."""
    return {
        "sid": "925ca6ee-aa4b-4508-955b-10b1c02c69bb",
        "agent_trace": {
            "task": "Buy BTC price information",
            "events": [
                {
                    "timestamp": "2025-10-13T10:00:00Z",
                    "type": "tool_call",
                    "tool": "get_price",
                    "arguments": {"symbol": "BTC/USD"},
                    "result": {"price": 63500.12}
                }
            ],
            "model_config": {
                "model": "gpt-4",
                "temperature": 0.7
            }
        }
    }

