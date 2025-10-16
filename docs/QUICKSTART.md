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
pip install x402-secure-client
```

### 2. Get Test Wallet (Development)

```bash
# Generate a test wallet
python -c "from eth_account import Account; a = Account.create(); print(f'Address: {a.address}\nPrivate Key: {a.key.hex()}')"

# Save the private key as environment variable
export AGENT_PRIVATE_KEY=0x...  # Your private key from above
```

### 3. Create Your First Protected Payment

```python
import asyncio
import os
from x402_secure_client import BuyerClient, BuyerConfig, RiskClient
from x402_secure_client import execute_payment_with_tid, store_agent_trace

async def main():
    # Initialize clients
    buyer = BuyerClient(BuyerConfig(
        seller_base_url="https://test.example.com",
        agent_gateway_url="https://x402-proxy.t54.ai",
        buyer_private_key=os.getenv("AGENT_PRIVATE_KEY"),
        network="base-sepolia"  # Use testnet for development
    ))
    
    risk_client = RiskClient("https://x402-proxy.t54.ai")
    
    # Create risk session
    session = await risk_client.create_session(agent_did=buyer.address)
    sid = session['sid']
    
    # For testing: create a simple trace without AI
    tid = await store_agent_trace(
        risk=risk_client,
        sid=sid,
        task="Test payment",
        params={"test": True},
        events=[]  # Empty events for testing
    )
    
    # Execute payment
    result = await execute_payment_with_tid(
        buyer=buyer,
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
    BuyerClient, BuyerConfig, RiskClient, OpenAITraceCollector,
    store_agent_trace, execute_payment_with_tid
)

async def shopping_agent():
    # Initialize clients
    buyer = BuyerClient(BuyerConfig(
        seller_base_url="https://shop.example.com",
        agent_gateway_url="https://x402-proxy.t54.ai",
        buyer_private_key=os.getenv("AGENT_PRIVATE_KEY")
    ))
    
    risk_client = RiskClient("https://x402-proxy.t54.ai")
    openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    tracer = OpenAITraceCollector()
    
    # Create risk session
    session = await risk_client.create_session(agent_did=buyer.address)
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
        model="gpt-4",
        input=messages,
        tools=[{"type": "function", "function": {
            "name": "purchase_item",
            "description": "Purchase an item",
            "parameters": {"type": "object", "properties": {
                "item": {"type": "string"},
                "price": {"type": "string"},
                "merchant": {"type": "string"}
            }}
        }}]
    ) as stream:
        result = await tracer.process_stream(
            stream=stream,
            tools={"purchase_item": purchase_item}
        )
    
    # Check if purchase was made
    if "purchase_item" in result.get("tool_results", {}):
        purchase = result["tool_results"]["purchase_item"]
        
        # Store trace and get trace ID
        tid = await store_agent_trace(
            risk=risk_client,
            sid=sid,
            task="Purchase coffee maker",
            params=purchase,
            events=tracer.events
        )
        
        # Execute protected payment
        payment_result = await execute_payment_with_tid(
            buyer=buyer,
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
# For FastAPI
pip install x402-secure-client[fastapi]

# For Flask
pip install x402-secure-client[flask]
```

### 2. Add Payment Protection to Your API

#### FastAPI Example

```python
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from x402_secure_client import SellerClient
import base64, json

app = FastAPI()

# Initialize seller client
seller = SellerClient("https://x402-proxy.t54.ai/x402")

@app.post("/api/generate")
async def generate_content(request: Request, prompt: str):
    # Define payment requirements
    pr = {
        "scheme": "exact",
        "network": "base-sepolia",
        "maxAmountRequired": "100000",  # 0.10 USDC
        "resource": str(request.url),
        "payTo": "0xYourWallet",
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
    }
    
    # Check payment headers
    x_payment = request.headers.get("X-PAYMENT")
    x_payment_secure = request.headers.get("X-PAYMENT-SECURE")
    risk_session = request.headers.get("X-RISK-SESSION")
    
    if not all([x_payment, x_payment_secure, risk_session]):
        return JSONResponse(
            {"x402Version": 1, "accepts": [pr], "error": "Payment required"},
            status_code=402
        )
    
    # Verify and settle
    payment_data = json.loads(base64.b64decode(x_payment))
    result = await seller.verify_then_settle(
        payment_data, pr,
        x_payment_b64=x_payment,
        origin=request.headers.get("Origin"),
        x_payment_secure=x_payment_secure,
        risk_sid=risk_session
    )
    
    # Provide service
    content = f"Generated content for: {prompt}"
    
    return JSONResponse(
        {"result": content},
        headers={"X-PAYMENT-RESPONSE": base64.b64encode(json.dumps(result).encode()).decode()}
    )

# Run with: uvicorn main:app
```

#### Flask Example

```python
from flask import Flask, request, jsonify, make_response
from x402_secure_client import SellerClient
import base64, json

app = Flask(__name__)
seller = SellerClient("https://x402-proxy.t54.ai/x402")

@app.route("/api/analyze", methods=["POST"])
def analyze_data():
    # Define payment requirements
    pr = {
        "scheme": "exact",
        "network": "base-sepolia",
        "maxAmountRequired": "1000000",  # 1.00 USDC
        "resource": request.url,
        "payTo": "0xYourWallet",
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
    }
    
    # Check payment headers
    x_payment = request.headers.get("X-PAYMENT")
    x_payment_secure = request.headers.get("X-PAYMENT-SECURE")
    risk_session = request.headers.get("X-RISK-SESSION")
    
    if not all([x_payment, x_payment_secure, risk_session]):
        return jsonify({
            "x402Version": 1,
            "accepts": [pr],
            "error": "Payment required"
        }), 402
    
    # Note: Flask requires sync or asyncio wrapper
    # This is simplified - see docs for async support
    try:
        payment_data = json.loads(base64.b64decode(x_payment))
        # In production, use async wrapper for seller.verify_then_settle
        # For now, assume sync version exists
        
        # Process request
        data = request.json
        result = f"Analysis complete for {len(data)} items"
        
        response = make_response(jsonify({"result": result}))
        # Add payment response header
        # response.headers["X-PAYMENT-RESPONSE"] = ...
        return response
        
    except Exception as e:
        return jsonify({"error": str(e)}), 402

# Run with: flask run
```

### 3. Test Your Integration

```bash
# Test without payment (should return 402)
curl -X POST http://localhost:8000/api/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Hello world"}'

# Test with payment headers (contact us for test headers)
curl -X POST http://localhost:8000/api/generate \
  -H "Content-Type: application/json" \
  -H "X-Payment-Receipt: ..." \
  -H "X-Payment-Signature: ..." \
  -d '{"prompt": "Hello world"}'
```

### Next Steps

- 📖 [Read full Seller Integration Guide](SELLER_INTEGRATION.md)
- 🛡️ [Configure risk tolerance](SELLER_INTEGRATION.md#risk-management)
- 💰 [Set up usage-based billing](SELLER_INTEGRATION.md#usage-based-billing)

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