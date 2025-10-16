# Seller/API Integration Guide

This guide helps API and service providers accept secure payments from AI agents using x402-secure.

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [Basic Integration](#basic-integration)
4. [Risk Management](#risk-management)
5. [Advanced Features](#advanced-features)
6. [Dispute Handling](#dispute-handling)
7. [Production Checklist](#production-checklist)

## Prerequisites

- Python 3.11+ with FastAPI, Flask, or similar web framework
- Basic understanding of HTTP headers and REST APIs
- An Ethereum wallet for receiving payments (optional - can use custodial)

## Installation

```bash
pip install x402-secure-client
```

For specific frameworks:
```bash
# FastAPI
pip install x402-secure-client[fastapi]

# Flask  
pip install x402-secure-client[flask]

# Django
pip install x402-secure-client[django]
```

## Basic Integration

### 1. FastAPI Integration

```python
from fastapi import FastAPI, Request, HTTPException
from x402_secure_client import SellerClient

app = FastAPI()

# Initialize seller client
seller = SellerClient("https://x402-proxy.t54.ai/x402")

@app.post("/api/v1/generate-text")
async def generate_text(request: Request, prompt: str):
    # Define payment requirements
    payment_requirements = {
        "scheme": "exact",
        "network": "base-sepolia",
        "maxAmountRequired": "100000",  # 0.10 USDC
        "resource": str(request.url),
        "payTo": "0xYourWalletAddress",
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",  # USDC on Base Sepolia
        "description": "Text generation API"
    }
    
    # Check payment headers
    x_payment = request.headers.get("X-PAYMENT")
    x_payment_secure = request.headers.get("X-PAYMENT-SECURE")
    risk_session = request.headers.get("X-RISK-SESSION")
    
    if not all([x_payment, x_payment_secure, risk_session]):
        # Return 402 Payment Required
        raise HTTPException(
            status_code=402,
            detail={
                "x402Version": 1,
                "accepts": [payment_requirements],
                "error": "Payment required"
            }
        )
    
    # Verify and settle payment
    try:
        import base64, json
        payment_data = json.loads(base64.b64decode(x_payment))
        
        result = await seller.verify_then_settle(
            payment_data,
            payment_requirements,
            x_payment_b64=x_payment,
            origin=request.headers.get("Origin"),
            x_payment_secure=x_payment_secure,
            risk_sid=risk_session
        )
        
        # Process the request
        text_result = await process_text_generation(prompt)
        
        # Return response with payment receipt
        response_data = {"result": text_result}
        return JSONResponse(
            response_data,
            headers={"X-PAYMENT-RESPONSE": base64.b64encode(json.dumps(result).encode()).decode()}
        )
        
    except Exception as e:
        raise HTTPException(status_code=402, detail=str(e))
```

### 2. Flask Integration

```python
from flask import Flask, request, jsonify
from x402_secure_client import SellerClient
import base64
import json

app = Flask(__name__)

seller = SellerClient("https://x402-proxy.t54.ai/x402")

@app.route("/api/v1/analyze", methods=["POST"])
def analyze_data():
    # Define payment requirements
    payment_requirements = {
        "scheme": "exact",
        "network": "base-sepolia",
        "maxAmountRequired": "1000000",  # 1.00 USDC
        "resource": request.url,
        "payTo": "0xYourWalletAddress",
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
    }
    
    # Check payment headers
    x_payment = request.headers.get("X-PAYMENT")
    x_payment_secure = request.headers.get("X-PAYMENT-SECURE")
    risk_session = request.headers.get("X-RISK-SESSION")
    
    if not all([x_payment, x_payment_secure, risk_session]):
        return jsonify({
            "x402Version": 1,
            "accepts": [payment_requirements],
            "error": "Payment required"
        }), 402
    
    # Verify and settle payment
    try:
        payment_data = json.loads(base64.b64decode(x_payment))
        
        # Note: Flask integration requires sync version or run_async wrapper
        # This is a simplified example - see full docs for async support
        result = seller.verify_then_settle_sync(
            payment_data,
            payment_requirements,
            x_payment_b64=x_payment,
            origin=request.headers.get("Origin"),
            x_payment_secure=x_payment_secure,
            risk_sid=risk_session
        )
        
        # Process request
        data = request.json
        analysis_result = analyze_user_data(data)
        
        return jsonify({
            "result": analysis_result
        }), 200, {"X-PAYMENT-RESPONSE": base64.b64encode(json.dumps(result).encode()).decode()}
        
    except Exception as e:
        return jsonify({"error": str(e)}), 402
```

### 3. Payment Headers

x402-secure uses standard HTTP headers:

```http
POST /api/endpoint HTTP/1.1
Host: api.yourservice.com
X-Payment-Receipt: <base64_encoded_receipt>
X-Payment-Signature: <signature>
X-Risk-Session: <session_id>
X-Payment-Secure: <trace_context>
```

Your validator automatically handles these headers.

## Risk Management

### Understanding Risk Scores

```python
@app.post("/api/premium-feature")
async def premium_feature(request: Request):
    payment = await validator.verify_payment(request)
    
    if not payment.valid:
        return {"error": "Payment required"}, 402
    
    # Risk-based pricing/access
    if payment.risk_score < 0.3:
        # Low risk - full access
        return await full_premium_response()
    
    elif payment.risk_score < 0.7:
        # Medium risk - limited access
        return await limited_premium_response()
    
    else:
        # High risk - require additional verification
        return {
            "error": "Additional verification required",
            "verification_url": f"https://verify.x402-secure.com/{payment.id}"
        }
```

### Risk Factors Explained

The risk score (0.0 to 1.0) considers:

- **Agent Behavior** (40%)
  - Reasoning consistency
  - Tool usage patterns
  - Response to prompts

- **Transaction Pattern** (30%)
  - Amount vs. history
  - Frequency
  - Time patterns

- **User History** (20%)
  - Account age
  - Dispute rate
  - Previous transactions

- **Technical Factors** (10%)
  - Network anomalies
  - Header consistency
  - Signature validity

### Configuring Risk Tolerance

```python
seller = SellerClient(
    "https://x402-proxy.t54.ai/x402",
    config={
        "risk_tolerance": {
            "max_score": 0.8,           # Reject above this
            "require_trace": True,      # Require reasoning trace
            "min_confirmations": 1,     # Blockchain confirmations
            "challenge_threshold": 0.6  # Require challenge above this
        }
    }
)
```

## Advanced Features

> **Note**: The following examples demonstrate conceptual patterns for advanced payment scenarios. Some features like subscriptions, usage-based billing, and automated dispute handling are planned enhancements. For current implementation, focus on the basic integration pattern shown above.

### 1. Streaming Responses with Payment

```python
from fastapi.responses import StreamingResponse

@app.post("/api/stream")
async def stream_response(request: Request):
    # Check payment headers first
    payment_headers = {
        "x_payment": request.headers.get("X-PAYMENT"),
        "x_payment_secure": request.headers.get("X-PAYMENT-SECURE"),
        "risk_session": request.headers.get("X-RISK-SESSION")
    }
    
    if not all(payment_headers.values()):
        raise HTTPException(402, "Payment required")
    
    async def generate():
        try:
            # Start streaming
            for chunk in await generate_ai_response():
                yield chunk
            
            # Settle after successful completion
            # Note: In production, implement proper payment settlement
            # await seller.settle_payment(...)
            
        except Exception as e:
            # Handle errors appropriately
            # In production, implement refund logic if needed
            raise
    
    return StreamingResponse(generate())
```

### 2. Subscription Management

```python
@app.post("/api/subscribe")
async def create_subscription(request: Request, plan: str):
    payment = await validator.verify_payment(request)
    
    if not payment.valid:
        raise HTTPException(402, "Payment required")
    
    # Create subscription
    subscription = await validator.create_subscription(
        payment=payment,
        plan_id=plan,
        interval="monthly",
        amount="29.99"
    )
    
    return {
        "subscription_id": subscription.id,
        "next_payment": subscription.next_payment_date,
        "status": "active"
    }
```

### 3. Usage-Based Billing

```python
# Track usage
@app.middleware("http")
async def track_usage(request: Request, call_next):
    response = await call_next(request)
    
    if hasattr(request.state, "payment"):
        # Record usage for billing
        await validator.record_usage(
            payment_id=request.state.payment.id,
            usage_type="api_calls",
            quantity=1,
            metadata={"endpoint": request.url.path}
        )
    
    return response

# Bill at end of period
async def bill_usage_period():
    bills = await validator.calculate_usage_bills(
        period_start=datetime.now() - timedelta(days=30),
        period_end=datetime.now()
    )
    
    for bill in bills:
        await validator.charge_usage(bill)
```

### 4. Multi-Currency Support

```python
validator = PaymentValidator(
    config=SellerConfig(
        proxy_url="https://x402-proxy.t54.ai",
        merchant_id="api.yourservice.com",
        accepted_currencies=["USDC", "USDT", "DAI"],
        pricing={
            "USDC": "10.00",
            "USDT": "10.00",
            "DAI": "10.50"  # Slight premium for DAI
        }
    )
)
```

## Dispute Handling

### 1. Automatic Evidence Collection

```python
# Evidence is automatically collected, but you can add context
@app.post("/api/sensitive-action")
async def sensitive_action(request: Request, action: dict):
    payment = await validator.verify_payment(request)
    
    # Add your own evidence
    await validator.add_evidence(
        payment_id=payment.id,
        evidence={
            "request_body": action,
            "user_confirmation": action.get("confirmed", False),
            "risk_warnings_shown": True,
            "timestamp": datetime.now().isoformat()
        }
    )
    
    # Process action
    result = await execute_sensitive_action(action)
    
    # Settle with result evidence
    await validator.settle_payment(
        payment,
        evidence={"action_result": result}
    )
    
    return result
```

### 2. Handling Disputes

```python
# Webhook for dispute notifications
@app.post("/webhooks/disputes")
async def handle_dispute(dispute: dict):
    # Get dispute details
    dispute_info = await validator.get_dispute(dispute["id"])
    
    # Check the evidence
    if dispute_info.auto_resolvable:
        # Strong evidence - auto resolve
        await validator.resolve_dispute(
            dispute_id=dispute["id"],
            accept=False,  # Reject the dispute
            evidence_url=dispute_info.evidence_url
        )
    else:
        # Manual review needed
        await notify_support_team(dispute_info)
    
    return {"status": "processed"}
```

### 3. Dispute Prevention

```python
# Pre-transaction challenges for high-risk payments
@app.post("/api/high-value-action")
async def high_value_action(request: Request):
    payment = await validator.verify_payment(request)
    
    if payment.risk_score > 0.5 or payment.amount > 50:
        # Require additional confirmation
        challenge = await validator.create_challenge(
            payment_id=payment.id,
            challenge_type="user_confirmation",
            question="Please confirm this high-value transaction"
        )
        
        return {
            "status": "challenge_required",
            "challenge_id": challenge.id,
            "challenge_url": challenge.url
        }
    
    # Process normally for low-risk
    return await process_action()
```

## Production Checklist

### Security

- [ ] **HTTPS Only**
  - [ ] Enforce TLS 1.2+ for all endpoints
  - [ ] Valid SSL certificate
  - [ ] Secure headers (HSTS, CSP, etc.)

- [ ] **Authentication**
  - [ ] Validate payment signatures
  - [ ] Rate limiting per payment source
  - [ ] DDoS protection

- [ ] **Error Handling**
  - [ ] Don't leak internal errors
  - [ ] Consistent error format
  - [ ] Proper status codes

### Reliability

- [ ] **Payment Validation**
  - [ ] Always validate before processing
  - [ ] Handle validation failures gracefully
  - [ ] Cache validation results (5 min TTL)

- [ ] **Settlement**
  - [ ] Always settle after delivery
  - [ ] Handle settlement failures
  - [ ] Implement retry logic

- [ ] **Monitoring**
  - [ ] Log all payment attempts
  - [ ] Monitor validation failures
  - [ ] Track dispute rates

### Compliance

- [ ] **Terms of Service**
  - [ ] Clear payment terms
  - [ ] Dispute process documented
  - [ ] Refund policy stated

- [ ] **Privacy**
  - [ ] Don't log sensitive trace data
  - [ ] GDPR compliance if applicable
  - [ ] Data retention policy

- [ ] **Documentation**
  - [ ] API pricing clearly stated
  - [ ] Integration guide for users
  - [ ] Support contact information

### Testing

- [ ] **Integration Tests**
  - [ ] Test payment validation
  - [ ] Test risk score handling
  - [ ] Test dispute flow

- [ ] **Load Testing**
  - [ ] Verify performance under load
  - [ ] Test payment validation caching
  - [ ] Ensure no payment bottlenecks

- [ ] **Error Scenarios**
  - [ ] Network failures
  - [ ] Invalid payments
  - [ ] Downstream failures

## Example: Complete API Implementation

```python
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from x402_secure_client import SellerClient
import base64
import json
import logging

app = FastAPI(title="AI Service API")
logger = logging.getLogger(__name__)

# Initialize seller client
seller = SellerClient("https://x402-proxy.t54.ai/x402")

# Payment requirements for your service
PAYMENT_REQUIREMENTS = {
    "scheme": "exact",
    "network": "base-sepolia",
    "maxAmountRequired": "1000000",  # 1.00 USDC
    "payTo": "0xYourWalletAddress",
    "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",  # USDC
    "description": "AI Content Generation"
}

# Protected endpoint
@app.post("/api/v1/generate")
async def generate_content(request: Request, prompt: str):
    # Get payment headers
    x_payment = request.headers.get("X-PAYMENT")
    x_payment_secure = request.headers.get("X-PAYMENT-SECURE")
    risk_session = request.headers.get("X-RISK-SESSION")
    origin = request.headers.get("Origin")
    
    # Check if payment provided
    if not all([x_payment, x_payment_secure, risk_session]):
        logger.info("Payment required for request")
        return JSONResponse(
            {
                "x402Version": 1,
                "accepts": [PAYMENT_REQUIREMENTS],
                "error": "Payment required"
            },
            status_code=402
        )
    
    try:
        # Decode payment data
        payment_data = json.loads(base64.b64decode(x_payment))
        
        logger.info(f"Processing payment from session {risk_session}")
        
        # Verify and settle payment
        result = await seller.verify_then_settle(
            payment_data,
            PAYMENT_REQUIREMENTS,
            x_payment_b64=x_payment,
            origin=origin,
            x_payment_secure=x_payment_secure,
            risk_sid=risk_session
        )
        
        # Process the AI request
        logger.info(f"Generating content for prompt: {prompt[:50]}...")
        ai_result = await ai_generate(prompt)
        
        # Return response with payment receipt
        response_data = {
            "result": ai_result,
            "usage": {"prompt_tokens": 100, "completion_tokens": 200}
        }
        
        return JSONResponse(
            response_data,
            headers={
                "X-PAYMENT-RESPONSE": base64.b64encode(
                    json.dumps(result).encode()
                ).decode()
            }
        )
        
    except Exception as e:
        logger.error(f"Error processing request: {e}")
        return JSONResponse(
            {"error": "Payment processing failed", "details": str(e)},
            status_code=402
        )

# Health check (no payment required)
@app.get("/health")
async def health_check():
    return {"status": "healthy", "payment_enabled": True}

# Placeholder for your AI generation logic
async def ai_generate(prompt: str) -> str:
    # Your actual AI generation code here
    return f"Generated content based on: {prompt}"

# Start server
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

## Support

- 📧 Email: support@x402-secure.com
- 💬 Discord: [Join our community](https://discord.gg/x402secure)
- 📖 API Docs: [docs.x402-secure.com](https://docs.x402-secure.com)
