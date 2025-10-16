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
- OpenAI API key (if using OpenAI)
- Basic understanding of async Python

## Installation

```bash
pip install x402-secure-client
```

For OpenAI integration:
```bash
pip install x402-secure-client[openai]
```

## Basic Integration

### 1. Environment Setup

```python
import os
from x402_secure_client import BuyerClient, BuyerConfig, RiskClient, OpenAITraceCollector

# Required environment variables
PRIVATE_KEY = os.getenv("AGENT_PRIVATE_KEY")  # Your agent's wallet
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # If using OpenAI

# Initialize buyer client
buyer = BuyerClient(BuyerConfig(
    seller_base_url="https://api.example.com",
    agent_gateway_url="https://x402-proxy.t54.ai",
    buyer_private_key=PRIVATE_KEY,
    network="base"  # or "base-sepolia" for testing
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
    # Create risk session first
    session = await risk_client.create_session(agent_did=buyer.address)
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
        
        # Store trace and get trace ID
        tid = await store_agent_trace(
            risk=risk_client,
            sid=sid,
            task=f"Purchase {purchase_data['item']}",
            params=purchase_data,
            events=tracer.events
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
from x402_secure_client import LangChainTraceCollector

# Future support
tracer = LangChainTraceCollector()
# ... integrate with your LangChain agent
```

### 3. Making Protected Payments

```python
# Complete payment flow example
async def make_payment(task: str, endpoint: str, params: dict):
    # 1. Create risk session
    session = await risk_client.create_session(
        agent_did=buyer.address,
        app_id="my-agent-v1"
    )
    sid = session['sid']
    
    # 2. Store agent trace (from your AI interactions)
    tid = await store_agent_trace(
        risk=risk_client,
        sid=sid,
        task=task,
        params=params,
        events=tracer.events,  # Your collected trace events
        environment={"network": "base-sepolia"},
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

For better risk assessment across multiple transactions:

```python
# Create a session for a user interaction
session = await client.create_session(
    user_id="user123",
    app_id="shopping-agent-v1"
)

# Use session for all related payments
result = await client.protected_payment(
    merchant="store.com",
    amount="50.00",
    reason="User shopping cart",
    trace=tracer.get_events(),
    session_id=session.id
)
```

### 2. Risk Threshold Configuration

```python
# Configure risk tolerance
client = BuyerClient(
    proxy_url="https://x402-proxy.t54.ai",
    private_key=PRIVATE_KEY,
    risk_config={
        "max_amount_per_tx": "100.00",      # Max per transaction
        "max_amount_per_day": "500.00",     # Daily limit
        "require_explicit_amount": True,     # User must mention amount
        "allowed_merchants": ["*.example.com", "trusted-api.com"]
    }
)
```

### 3. Trace Enrichment

Add context to improve risk assessment:

```python
# Enrich trace with user context
tracer.add_context({
    "user_history": {
        "account_age_days": 365,
        "previous_purchases": 42,
        "dispute_rate": 0.02
    },
    "session_info": {
        "auth_method": "oauth",
        "device_trusted": True
    }
})

# The context is included with the trace
result = await client.protected_payment(...)
```

### 4. Async Batch Payments

```python
# Process multiple payments efficiently
payments = [
    {"merchant": "api1.com", "amount": "10.00", "reason": "API credits"},
    {"merchant": "api2.com", "amount": "15.00", "reason": "Data access"},
    {"merchant": "api3.com", "amount": "20.00", "reason": "Premium features"}
]

results = await client.batch_protected_payments(
    payments=payments,
    trace=tracer.get_events(),
    stop_on_failure=True  # Stop if any payment fails
)

for payment, result in zip(payments, results):
    print(f"{payment['merchant']}: {'✅' if result.approved else '❌'}")
```

## Error Handling

### Common Errors and Solutions

```python
from x402_secure_client.errors import (
    InsufficientFundsError,
    RiskBlockedError,
    NetworkError,
    InvalidMerchantError
)

try:
    result = await client.protected_payment(...)
except InsufficientFundsError:
    # Handle insufficient balance
    print("Please add funds to your agent wallet")
except RiskBlockedError as e:
    # Payment blocked by risk engine
    print(f"Payment blocked: {e.reason}")
    print(f"Risk factors: {e.risk_factors}")
except InvalidMerchantError:
    # Merchant not recognized or blocked
    print("This merchant is not approved")
except NetworkError:
    # Network issues - safe to retry
    await asyncio.sleep(5)
    result = await client.protected_payment(...)  # Retry
```

### Checking Protection Status

```python
# Later, check if a payment is still protected
protection = await client.check_protection(protection_id)

print(f"Status: {protection.status}")  # active, disputed, resolved
print(f"Coverage: {protection.coverage_amount}")
print(f"Expires: {protection.expires_at}")

if protection.status == "disputed":
    print(f"Dispute reason: {protection.dispute_reason}")
    print(f"Your evidence: {protection.evidence_url}")
```

## Testing

### 1. Test Environment Setup

```python
# Use test network
test_client = BuyerClient(
    proxy_url="https://test.x402-proxy.t54.ai",
    private_key=TEST_PRIVATE_KEY,
    network="base-sepolia"
)

# Test payments won't charge real money
result = await test_client.protected_payment(
    merchant="test.example.com",
    amount="100.00",
    reason="Test payment",
    trace=tracer.get_events()
)
```

### 2. Simulating Risk Scenarios

```python
# Test high-risk payment
tracer.add_context({"test_scenario": "high_risk"})

# Test blocked payment
tracer.add_context({"test_scenario": "block_payment"})

# Test dispute flow
tracer.add_context({"test_scenario": "will_dispute"})
```

### 3. Integration Tests

```python
import pytest
from x402_secure_client.testing import MockBuyerClient, MockTracer

@pytest.mark.asyncio
async def test_payment_flow():
    # Use mock client for testing
    client = MockBuyerClient()
    tracer = MockTracer()
    
    # Simulate agent interaction
    tracer.add_event("tool_call", {
        "name": "make_purchase",
        "args": {"item": "Test Item", "price": "10.00"}
    })
    
    # Test payment
    result = await client.protected_payment(
        merchant="test.com",
        amount="10.00",
        reason="Test purchase",
        trace=tracer.get_events()
    )
    
    assert result.approved
    assert result.protection_id is not None
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

- 📧 Email: support@x402-secure.com
- 💬 Discord: [Join our community](https://discord.gg/x402secure)
- 📖 API Docs: [docs.x402-secure.com](https://docs.x402-secure.com)
