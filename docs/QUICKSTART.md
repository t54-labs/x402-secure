# Quick Start Guide

Get started with x402-secure in 5 minutes!

## Choose Your Path

### 🤖 I'm building an AI Agent that needs to make payments
→ [Jump to Buyer Agent Quick Start](#buyer-agent-quick-start)

### 🏪 I'm building an API/Service that accepts payments
→ [Jump to Seller API Quick Start](#seller-api-quick-start)

### 🔧 I want to deploy my own proxy
→ [See Development Guide](DEVELOPMENT.md)

---

## Buyer Agent Quick Start

### 1. Install the SDK

```bash
# Signing and OpenTelemetry are both required for payments
pip install "x402-secure[signing,otel]"
```

### 2. Get Test Wallet (Development)

```bash
# Generate a test wallet
python -c "from eth_account import Account; a = Account.create(); print(f'Address: {a.address}\nPrivate Key: {a.key.hex()}')"

# Save the private key as environment variable
export BUYER_PRIVATE_KEY=0x...  # Your private key from above
```

### 3. Create Your First Protected Payment

```python
import asyncio
import os
from x402_secure_client import BuyerClient, BuyerConfig, setup_otel_from_env

async def main():
    # Required: X-PAYMENT-SECURE needs an active OpenTelemetry span
    setup_otel_from_env()

    # Initialize buyer client
    buyer = BuyerClient(BuyerConfig(
        seller_base_url="https://test.example.com",
        agent_gateway_url="https://x402-proxy.t54.ai",
        buyer_private_key=os.getenv("BUYER_PRIVATE_KEY"),
        expected_pay_to=os.environ["BUYER_EXPECTED_PAY_TO"],
        expected_asset=os.environ["BUYER_EXPECTED_ASSET"],
        max_amount_atomic=int(os.environ["BUYER_MAX_AMOUNT_ATOMIC"]),
        network="base-sepolia"  # Use testnet for development
    ))

    # Create risk session (app_id and device are optional)
    session = await buyer.create_risk_session(
        app_id=None,
        device={"ua": "quickstart-demo"}
    )
    sid = session['sid']

    # For testing: create a simple trace without AI
    tid = await buyer.store_agent_trace(
        sid=sid,
        task="Test payment",
        params={"test": True},
        environment={
            "network": "base-sepolia",
            "seller_base_url": "https://test.example.com"
        },
        events=[]  # Empty events for testing
    )

    # Execute payment
    result = await buyer.execute_paid_request(
        endpoint="/api/test",
        task="Test payment",
        params={"amount": "1.00"},
        sid=sid,
        tid=tid
    )

    print(f"✅ Payment completed: {result}")

# Run it
asyncio.run(main())
```

### 4. Add AI Agent Integration (OpenAI Example)

```python
import os
import asyncio
import json
from openai import OpenAI
from x402_secure_client import (
    BuyerClient, BuyerConfig, OpenAITraceCollector, setup_otel_from_env
)

async def shopping_agent():
    # Initialize OpenTelemetry for observability
    setup_otel_from_env()

    # Initialize buyer client
    buyer = BuyerClient(BuyerConfig(
        seller_base_url="https://shop.example.com",
        agent_gateway_url="https://x402-proxy.t54.ai",
        buyer_private_key=os.getenv("BUYER_PRIVATE_KEY"),
        expected_pay_to=os.environ["BUYER_EXPECTED_PAY_TO"],
        expected_asset=os.environ["BUYER_EXPECTED_ASSET"],
        max_amount_atomic=int(os.environ["BUYER_MAX_AMOUNT_ATOMIC"]),
        network="base-sepolia"
    ))

    openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    tracer = OpenAITraceCollector()

    # Configure model info for trace context
    tracer.set_model_config(
        provider="openai",
        model=os.getenv("OPENAI_MODEL", "gpt-5-mini"),
        tools_enabled=["purchase_item"]
    )

    # Create risk session (app_id and device are optional)
    session = await buyer.create_risk_session(
        app_id=None,
        device={"ua": "shopping-agent"}
    )
    sid = session['sid']

    # Define purchase tool
    @tracer.tool
    def purchase_item(item: str, price: str, merchant: str):
        return {
            "status": "ready",
            "item": item,
            "price": price,
            "merchant": merchant
        }

    # Agent interaction with OpenAI streaming
    messages = [
        {"role": "system", "content": "You are a shopping assistant."},
        {"role": "user", "content": "Buy me a coffee maker under $50"}
    ]

    with openai_client.responses.stream(
        model=os.getenv("OPENAI_MODEL", "gpt-5-mini"),
        input=messages,
        # Responses API function tools use the flat shape (no nested "function" key)
        tools=[{
            "type": "function",
            "name": "purchase_item",
            "description": "Purchase an item",
            "parameters": {"type": "object", "properties": {
                "item": {"type": "string"},
                "price": {"type": "string"},
                "merchant": {"type": "string"}
            }}
        }]
    ) as stream:
        result = await tracer.process_stream(
            stream=stream,
            tools={"purchase_item": purchase_item}
        )

    # Check if purchase was made
    if "purchase_item" in result.get("tool_results", {}):
        purchase = result["tool_results"]["purchase_item"]

        # Store trace and get trace ID
        tid = await buyer.store_agent_trace(
            sid=sid,
            task="Purchase coffee maker",
            params=purchase,
            environment={
                "network": "base-sepolia",
                "seller_base_url": "https://shop.example.com"
            },
            events=tracer.events,
            model_config=tracer.model_config,
            session_context={"user_intent": "buy coffee maker"}
        )

        # Execute protected payment
        payment_result = await buyer.execute_paid_request(
            endpoint="/api/purchase",
            task="Purchase coffee maker",
            params=purchase,
            sid=sid,
            tid=tid
        )

        print(f"✅ Payment completed: {payment_result}")

# Run it
asyncio.run(shopping_agent())
```

### Next Steps

- 📖 [Read full Buyer Integration Guide](BUYER_INTEGRATION.md)
- 🧪 [Test with different scenarios](BUYER_INTEGRATION.md#testing)
- 🚀 [Production checklist](BUYER_INTEGRATION.md#production-checklist)

---

## Seller API Quick Start

### 1. Install the SDK

```bash
# For FastAPI (recommended - includes all dependencies)
pip install x402-secure[examples]

# Or install framework separately
pip install x402-secure fastapi uvicorn pydantic

# For Flask
pip install x402-secure flask
# Note: SellerClient is async-only; see the Flask note below
```

### 2. Add Payment Protection to Your API

#### FastAPI Example

```python
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from x402_secure_client import (
    PaymentVerificationError,
    SellerClient,
    SettlementDeclined,
    SettlementUnknown,
)
import base64, json

app = FastAPI()

# Initialize seller client with the gateway URL (SellerClient appends /x402)
seller = SellerClient("https://x402-proxy.t54.ai")

@app.get("/api/generate")
async def generate_content(request: Request, prompt: str):
    # Define payment requirements
    pr = {
        "scheme": "exact",
        "network": "base-sepolia",
        "maxAmountRequired": "100000",  # 0.10 USDC
        "resource": str(request.url),
        "description": "Content generation",
        "mimeType": "application/json",
        "payTo": "0xYourWallet",
        "maxTimeoutSeconds": 30,
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",  # USDC
        # Required for EIP-3009 signing: the token's EIP-712 domain
        "extra": {"name": "USDC", "version": "2"},
    }

    # Check payment headers
    x_payment = request.headers.get("X-PAYMENT")
    x_payment_secure = request.headers.get("X-PAYMENT-SECURE")
    risk_session = request.headers.get("X-RISK-SESSION")
    origin = request.headers.get("Origin")

    if not all([x_payment, x_payment_secure, risk_session, origin]):
        return JSONResponse(
            {"x402Version": 1, "accepts": [pr], "error": "Payment required"},
            status_code=402
        )

    # Verify and settle
    try:
        payment_data = json.loads(base64.b64decode(x_payment))
    except Exception:
        return JSONResponse({"error": "Invalid X-PAYMENT"}, status_code=402)

    try:
        result = await seller.verify_then_settle(
            payment_data, pr,
            x_payment_b64=x_payment,
            origin=origin,
            x_payment_secure=x_payment_secure,
            risk_sid=risk_session
        )
    except (PaymentVerificationError, SettlementDeclined):
        return JSONResponse({"error": "Payment declined"}, status_code=402)
    except SettlementUnknown:
        return JSONResponse({"error": "Settlement could not be confirmed"}, status_code=502)

    # Provide service
    content = f"Generated content for: {prompt}"

    return JSONResponse(
        {"result": content},
        headers={"X-PAYMENT-RESPONSE": base64.b64encode(json.dumps(result).encode()).decode()}
    )

# Run with: uvicorn main:app
```

#### Flask

`SellerClient` is async-only. For a Flask view, see the [Flask integration](SELLER_INTEGRATION.md#2-flask-integration) in the Seller Integration Guide, or use an async framework such as FastAPI.

### 3. Test Your Integration

```bash
# Test without payment (should return 402)
curl "http://localhost:8000/api/generate?prompt=Hello+world"

# Test with payment headers (contact us for test headers)
# Note: The buyer SDK handles these headers automatically
curl "http://localhost:8000/api/generate?prompt=Hello+world" \
  -H "X-PAYMENT: <base64-encoded-payment>" \
  -H "X-PAYMENT-SECURE: <w3c-trace-context>" \
  -H "X-RISK-SESSION: <session-id>" \
  -H "Origin: http://localhost:8000"
```

### Next Steps

- 📖 [Read full Seller Integration Guide](SELLER_INTEGRATION.md)
- 🛡️ [Configure risk tolerance](SELLER_INTEGRATION.md#risk-management)

---

## Common Questions

### For Buyers

**Q: How much does it cost?**
A: You only pay for the API services you use. x402-secure adds a small fee (0.1%) for dispute protection.

**Q: What if my agent makes a mistake?**
A: If the payment was approved by our risk engine, you're protected from disputes. We have the evidence.

**Q: Which AI frameworks are supported?**
A: Currently OpenAI, with LangChain, AutoGPT, and CrewAI coming soon.

### For Sellers

**Q: How do I set prices?**
A: Include pricing in your 402 response. The SDK handles the rest.

**Q: What about existing customers?**
A: The SDK only validates when payment headers are present. Regular requests work normally.

**Q: How are disputes handled?**
A: We store complete evidence of AI reasoning. Most disputes are auto-resolved in your favor.

---

## Need Help?

- 💬 [Join our Discord](https://discord.gg/x402secure)
- 📧 Email: support@x402-secure.com
- 📖 [Full Documentation](https://docs.x402-secure.com)
