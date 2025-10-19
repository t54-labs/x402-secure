# Buyer Agent Integration Guide

This guide helps AI agent developers integrate x402-secure for protected payments.

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [Basic Integration](#basic-integration)
4. [Advanced Features](#advanced-features)
5. [Error Handling](#error-handling)
6. [Testing](#testing)
7. [Production Checklist](#production-checklist)

## Prerequisites

- Python 3.11+
- An Ethereum wallet with private key
- OpenAI API key (if using OpenAI for agent traces)
- Basic understanding of async Python
- OpenTelemetry SDK (installed via the `otel` extra) for X-PAYMENT-SECURE trace headers

## Installation

```bash
# Basic installation (includes OpenAI client dependency)
pip install x402-secure

# For full buyer functionality (EIP-3009 signing + OpenTelemetry tracing)
pip install x402-secure[signing,otel]
```

**Note**: The package is imported as `x402_secure_client`.

## Basic Integration

### 1. Environment Setup

```python
import os
from x402_secure_client import (
    BuyerClient, BuyerConfig, RiskClient, 
    OpenAITraceCollector, setup_otel_from_env
)

# Initialize OpenTelemetry first (required for X-PAYMENT-SECURE headers)
setup_otel_from_env()

# Required environment variables
BUYER_PRIVATE_KEY = os.getenv("BUYER_PRIVATE_KEY")  # Your agent's wallet
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # If using OpenAI

# Initialize buyer client
buyer = BuyerClient(BuyerConfig(
    seller_base_url="https://api.example.com",
    agent_gateway_url="https://x402-proxy.t54.ai",
    buyer_private_key=BUYER_PRIVATE_KEY,
    network="base-sepolia"  # or "base" for mainnet
))

# Initialize risk client
risk_client = RiskClient("https://x402-proxy.t54.ai")
```

### 2. Trace Collection

#### OpenAI Integration

```python
from openai import OpenAI
from x402_secure_client import OpenAITraceCollector, store_agent_trace, execute_payment_with_tid
import json

openai_client = OpenAI(api_key=OPENAI_API_KEY)
tracer = OpenAITraceCollector()

# Automatic trace collection with OpenAI streaming
async def agent_task(user_request: str):
    # Create risk session first (all parameters are keyword-only)
    session = await risk_client.create_session(
        agent_did=buyer.address,
        app_id="my-agent-v1",
        device={"ua": "my-agent"}
    )
    sid = session['sid']
    
    # Define the purchase tool
    @tracer.tool
    def make_purchase(item: str, price: str, merchant: str):
        return {
            "item": item,
            "price": price,
            "merchant": merchant,
            "status": "ready_to_purchase"
        }
    
    # Stream OpenAI response with tool execution
    messages = [
        {"role": "system", "content": "You are a helpful shopping assistant."},
        {"role": "user", "content": user_request}
    ]
    
    with openai_client.responses.stream(
        model="gpt-4",
        input=messages,
        tools=[{
            "type": "function",
            "function": {
                "name": "make_purchase",
                "description": "Make a purchase on behalf of the user",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "item": {"type": "string"},
                        "price": {"type": "string"},
                        "merchant": {"type": "string"}
                    }
                }
            }
        }]
    ) as stream:
        result = await tracer.process_stream(
            stream=stream,
            tools={"make_purchase": make_purchase}
        )
    
    # Check if purchase was requested
    if "make_purchase" in result.get("tool_results", {}):
        purchase_data = result["tool_results"]["make_purchase"]
        
        # Store trace and get trace ID (environment is required)
        tid = await store_agent_trace(
            risk=risk_client,
            sid=sid,
            task=f"Purchase {purchase_data['item']}",
            params=purchase_data,
            environment={
                "network": buyer.cfg.network,
                "seller_base_url": buyer.cfg.seller_base_url
            },
            events=tracer.events,
            model_config=tracer.model_config
        )
        
        # Execute payment with protection
        payment_result = await execute_payment_with_tid(
            buyer=buyer,
            endpoint="/api/purchase",
            task=f"Purchase {purchase_data['item']}",
            params=purchase_data,
            sid=sid,
            tid=tid
        )
        
        return f"Successfully completed: {payment_result}"
```

#### LangChain Integration (Coming Soon)

```python
# Future support - custom trace collector for LangChain
# tracer = LangChainTraceCollector()
# ... integrate with your LangChain agent
```

### 3. Making Protected Payments

```python
# Complete payment flow example
async def make_payment(task: str, endpoint: str, params: dict):
    # 1. Create risk session (all parameters are keyword-only)
    session = await risk_client.create_session(
        agent_did=buyer.address,
        app_id="my-agent-v1",
        device={"ua": "my-agent"}
    )
    sid = session['sid']
    
    # 2. Store agent trace (from your AI interactions)
    tid = await store_agent_trace(
        risk=risk_client,
        sid=sid,
        task=task,
        params=params,
        environment={
            "network": buyer.cfg.network,
            "seller_base_url": buyer.cfg.seller_base_url
        },
        events=tracer.events,  # Your collected trace events
        model_config=tracer.model_config
    )
    
    # 3. Execute payment with trace ID
    result = await execute_payment_with_tid(
        buyer=buyer,
        endpoint=endpoint,
        task=task,
        params=params,
        sid=sid,
        tid=tid
    )
    
    print(f"✅ Payment completed")
    print(f"Result: {result}")
    return result

# Example usage
result = await make_payment(
    task="Purchase API credits",
    endpoint="/api/credits",
    params={"amount": "25.00", "credits": 1000}
)
```

## Advanced Features

### 1. Session Management

Reuse a single session ID across a conversation, but create a new trace ID per payment:

```python
# Create a session for a user interaction
session = await risk_client.create_session(
    agent_did=buyer.address,
    app_id="shopping-agent-v1",
    device={"ua": "my-agent"}
)
sid = session['sid']

# For each purchase in the conversation:
# 1. Create new trace
tid = await store_agent_trace(
    risk=risk_client,
    sid=sid,  # Reuse session
    task="Purchase item",
    params={"item": "Coffee Maker"},
    environment={"network": buyer.cfg.network, "seller_base_url": buyer.cfg.seller_base_url},
    events=tracer.events,
    model_config=tracer.model_config
)

# 2. Execute payment with trace ID
result = await execute_payment_with_tid(
    buyer=buyer,
    endpoint="/api/purchase",
    task="Purchase item",
    params={"item": "Coffee Maker"},
    sid=sid,
    tid=tid
)
```

### 2. Trace Enrichment

Add context to improve risk assessment using tracer methods:

```python
# Record user input
tracer.record_user_input("I want to buy a coffee maker for under $100")

# Record system prompt if applicable
tracer.record_system_prompt("You are a helpful shopping assistant.", version="v1.0")

# Set model configuration
tracer.set_model_config(
    provider="openai",
    model="gpt-4",
    tools_enabled=["make_purchase", "check_inventory"]
)

# Record agent output
tracer.record_agent_output("I found a coffee maker for $89.99. Shall I proceed?")

# Store trace with model_config and optional session_context
tid = await store_agent_trace(
    risk=risk_client,
    sid=sid,
    task="Purchase coffee maker",
    params={"item": "Coffee Maker", "max_price": 100},
    environment={"network": buyer.cfg.network, "seller_base_url": buyer.cfg.seller_base_url},
    events=tracer.events,
    model_config=tracer.model_config,
    session_context={
        "user_id": "user123",
        "auth_method": "oauth",
        "device_trusted": True
    }
)
```

### 3. Async Batch Payments

Process multiple payments by reusing the session and creating a new trace per item:

```python
# Create session once
sid = (await risk_client.create_session(
    agent_did=buyer.address,
    app_id="batch-processor",
    device={"ua": "my-agent"}
))['sid']

# Process multiple payments
payments = [
    {"endpoint": "/api/credits", "item": "API credits", "amount": "10.00"},
    {"endpoint": "/api/data", "item": "Data access", "amount": "15.00"},
    {"endpoint": "/api/premium", "item": "Premium features", "amount": "20.00"}
]

for payment in payments:
    # Create new trace for each payment
    tid = await store_agent_trace(
        risk=risk_client,
        sid=sid,  # Reuse session
        task=f"Purchase {payment['item']}",
        params={"item": payment['item'], "amount": payment['amount']},
        environment={"network": buyer.cfg.network, "seller_base_url": buyer.cfg.seller_base_url},
        events=tracer.events,
        model_config=tracer.model_config
    )
    
    # Execute payment
    result = await execute_payment_with_tid(
        buyer=buyer,
        endpoint=payment['endpoint'],
        task=f"Purchase {payment['item']}",
        params={"amount": payment['amount']},
        sid=sid,
        tid=tid
    )
    print(f"{payment['item']}: ✅ {result}")
```

## Error Handling

### Common Errors and Solutions

```python
import httpx
import asyncio

try:
    result = await execute_payment_with_tid(
        buyer=buyer,
        endpoint="/api/purchase",
        task="Purchase item",
        params={"item": "Coffee"},
        sid=sid,
        tid=tid
    )
except httpx.HTTPStatusError as e:
    # Non-2xx response from seller or proxy
    print(f"Payment failed: {e.response.status_code}")
    print(f"Response: {e.response.text}")
    # Check specific status codes:
    if e.response.status_code == 402:
        print("Payment required - check wallet balance")
    elif e.response.status_code == 403:
        print("Payment blocked by risk engine or seller")
    elif e.response.status_code == 404:
        print("Endpoint not found")
except httpx.RequestError as e:
    # Network issues - retryable
    print(f"Network error: {e}")
    await asyncio.sleep(5)
    # Retry logic here
except RuntimeError as e:
    # SDK precondition failures
    print(f"SDK error: {e}")
    # Examples: missing BUYER_PRIVATE_KEY, missing X-PAYMENT-SECURE
except ValueError as e:
    # Invalid parameters
    print(f"Invalid input: {e}")
```

### Retry Pattern for Network Errors

```python
async def execute_payment_with_retry(buyer, endpoint, task, params, sid, tid, max_retries=3):
    for attempt in range(max_retries):
        try:
            return await execute_payment_with_tid(
                buyer=buyer,
                endpoint=endpoint,
                task=task,
                params=params,
                sid=sid,
                tid=tid
            )
        except httpx.RequestError as e:
            if attempt == max_retries - 1:
                raise
            print(f"Retry {attempt + 1}/{max_retries} after network error: {e}")
            await asyncio.sleep(2 ** attempt)  # Exponential backoff
```

## Testing

### 1. Local Test Environment Setup

Run the proxy locally for testing (see `docs/DEVELOPMENT.md` for details):

```bash
# Set environment variables
export AGENT_GATEWAY_URL=http://localhost:8000
export SELLER_BASE_URL=http://localhost:8010
export NETWORK=base-sepolia
export BUYER_PRIVATE_KEY=0x...  # Your test wallet private key
```

```python
import os
from x402_secure_client import (
    BuyerClient, BuyerConfig, RiskClient,
    setup_otel_from_env
)

# Initialize for local testing
setup_otel_from_env()

buyer = BuyerClient(BuyerConfig(
    seller_base_url=os.getenv("SELLER_BASE_URL", "http://localhost:8010"),
    agent_gateway_url=os.getenv("AGENT_GATEWAY_URL", "http://localhost:8000"),
    network="base-sepolia",
    buyer_private_key=os.getenv("BUYER_PRIVATE_KEY")
))

risk_client = RiskClient(os.getenv("AGENT_GATEWAY_URL", "http://localhost:8000"))
```

### 2. Running Example Scripts

The SDK ships with working examples:

```bash
# Basic payment example
cd packages/x402-secure/examples
python buyer_basic.py

# OpenAI agent example (requires OPENAI_API_KEY)
python buyer_agent_openai.py
```

See `packages/x402-secure/examples/README.md` for details.

### 3. Integration Tests

```python
import pytest
from x402_secure_client import (
    BuyerClient, BuyerConfig, RiskClient,
    OpenAITraceCollector, store_agent_trace, execute_payment_with_tid,
    setup_otel_from_env
)

@pytest.mark.asyncio
async def test_payment_flow():
    setup_otel_from_env()
    
    buyer = BuyerClient(BuyerConfig(
        seller_base_url="http://localhost:8010",
        agent_gateway_url="http://localhost:8000",
        network="base-sepolia",
        buyer_private_key=os.getenv("BUYER_PRIVATE_KEY")
    ))
    
    risk = RiskClient("http://localhost:8000")
    tracer = OpenAITraceCollector()
    
    # Simulate agent interaction
    tracer.record_user_input("Buy test item")
    tracer.record_agent_output("Purchasing test item")
    
    # Create session and trace
    sid = (await risk.create_session(
        agent_did=buyer.address,
        app_id="test-agent",
        device={"ua": "pytest"}
    ))['sid']
    
    tid = await store_agent_trace(
        risk=risk,
        sid=sid,
        task="Test purchase",
        params={"item": "Test Item"},
        environment={"network": "base-sepolia", "seller_base_url": "http://localhost:8010"},
        events=tracer.events
    )
    
    # Execute payment
    result = await execute_payment_with_tid(
        buyer=buyer,
        endpoint="/api/test",
        task="Test purchase",
        params={"item": "Test Item"},
        sid=sid,
        tid=tid
    )
    
    assert result is not None
```

## Production Checklist

Before going live:

- [ ] **Wallet Security**
  - [ ] Private key stored securely (use environment variables or secret manager)
  - [ ] Separate wallets for development and production
  - [ ] Wallet has sufficient funds for gas fees

- [ ] **Error Handling**
  - [ ] Graceful handling of all error types
  - [ ] Retry logic for network errors
  - [ ] User-friendly error messages

- [ ] **Monitoring**
  - [ ] Log all payment attempts and results
  - [ ] Set up alerts for failed payments
  - [ ] Monitor protection status

- [ ] **Risk Configuration**
  - [ ] Set appropriate transaction limits
  - [ ] Configure allowed merchants
  - [ ] Test risk thresholds

- [ ] **Testing**
  - [ ] Test all payment flows on testnet
  - [ ] Simulate edge cases and errors
  - [ ] Verify dispute protection

- [ ] **Documentation**
  - [ ] Document your agent's payment policies
  - [ ] Create user-facing payment explanations
  - [ ] Prepare dispute response templates

## Support

- 📧 Email: support@t54.ai
- 💬 Discord: [Join our community](https://discord.gg/t54labs)
- 📖 Documentation: [docs.t54.ai](https://docs.t54.ai)
- 🔗 GitHub: [github.com/t54labs/x402-secure](https://github.com/t54labs/x402-secure)
