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
pip install x402-secure
```

Optional extras:
```bash
# For EIP-3009 payment signing
pip install x402-secure[signing]

# For OpenTelemetry integration
pip install x402-secure[otel]

# For running examples
pip install x402-secure[examples]

# For OpenAI agent integration
pip install x402-secure[agent]
```

## Basic Integration

### 1. FastAPI Integration

```python
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from x402_secure_client import SellerClient
import base64
import json

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
    origin = request.headers.get("Origin")
    
    if not all([x_payment, x_payment_secure, risk_session, origin]):
        # Return 402 Payment Required
        return JSONResponse(
            {
                "x402Version": 1,
                "accepts": [payment_requirements],
                "error": "Payment required"
            },
            status_code=402
        )
    
    # Verify and settle payment
    try:
        payment_data = json.loads(base64.b64decode(x_payment))
        
        result = await seller.verify_then_settle(
            payment_data,
            payment_requirements,
            x_payment_b64=x_payment,
            origin=origin,
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
        # Handle errors - 403 indicates risk denial, other errors are payment failures
        status_code = 403 if "Risk denied" in str(e) else 402
        return JSONResponse({"error": str(e)}, status_code=status_code)
```

### 2. Flask Integration

```python
from flask import Flask, request, jsonify
from x402_secure_client import SellerClient
import base64
import json
import anyio

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
    origin = request.headers.get("Origin")
    
    if not all([x_payment, x_payment_secure, risk_session, origin]):
        return jsonify({
            "x402Version": 1,
            "accepts": [payment_requirements],
            "error": "Payment required"
        }), 402
    
    # Verify and settle payment
    try:
        payment_data = json.loads(base64.b64decode(x_payment))
        
        # Note: SellerClient is async-only. For Flask, wrap with an async runner:
        async def _verify_and_settle():
            return await seller.verify_then_settle(
                payment_data,
                payment_requirements,
                x_payment_b64=x_payment,
                origin=origin,
                x_payment_secure=x_payment_secure,
                risk_sid=risk_session
            )
        
        result = anyio.from_thread.run(_verify_and_settle)
        
        # Process request
        data = request.json
        analysis_result = analyze_user_data(data)
        
        return jsonify({
            "result": analysis_result
        }), 200, {"X-PAYMENT-RESPONSE": base64.b64encode(json.dumps(result).encode()).decode()}
        
    except Exception as e:
        status_code = 403 if "Risk denied" in str(e) else 402
        return jsonify({"error": str(e)}), status_code
```

> **Note**: The SellerClient is async-only. For Flask, you must use an async runner like `anyio.from_thread.run()` or consider using an async-capable framework like FastAPI for endpoints that handle payments.

### 3. Payment Headers

x402-secure uses the following HTTP headers:

```http
POST /api/endpoint HTTP/1.1
Host: api.yourservice.com
Origin: https://buyer-origin.com
X-PAYMENT: <base64_encoded_payment_payload>
X-PAYMENT-SECURE: w3c.v1;tp=<traceparent>;ts=<url_encoded_tracestate>
X-RISK-SESSION: <session_uuid>
X-AP2-EVIDENCE: evd.v1;mr=<ref>;ms=<sha256_b64url>;mt=application/json;sz=<bytes>
```

**Required Headers:**
- `X-PAYMENT`: Base64-encoded JSON payment payload
- `X-PAYMENT-SECURE`: W3C trace context (format: `w3c.v1;tp=...;ts=...`)
- `X-RISK-SESSION`: UUID of the risk session
- `Origin`: Buyer's origin (for AP2 origin binding)

**Optional Headers:**
- `X-AP2-EVIDENCE`: AP2 mandate reference (format: `evd.v1;mr=...;ms=...;mt=application/json;sz=...`)

Your service forwards these headers to the proxy via `SellerClient`; the proxy validates them and calls the risk engine before forwarding to the upstream facilitator.

## Risk Management

### How Risk Gating Works

Risk evaluation happens **inside the proxy** before payment verification reaches the upstream facilitator:

1. Your service calls `seller.verify_then_settle(...)` with payment headers
2. The proxy calls `/risk/evaluate` with session ID, trace context, and payment data
3. The risk engine returns a decision: `allow`, `deny`, or `review`
4. If `deny`, the proxy returns 403 with an error message
5. If `allow`, the proxy forwards the request to the upstream facilitator

### Handling Risk Decisions

```python
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from x402_secure_client import SellerClient
import base64
import json

app = FastAPI()
seller = SellerClient("https://x402-proxy.t54.ai/x402")

@app.post("/api/premium-feature")
async def premium_feature(request: Request):
    payment_requirements = {
        "scheme": "exact",
        "network": "base-sepolia",
        "maxAmountRequired": "500000",  # 0.50 USDC
        "resource": str(request.url),
        "payTo": "0xYourWalletAddress",
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
    }
    
    x_payment = request.headers.get("X-PAYMENT")
    x_payment_secure = request.headers.get("X-PAYMENT-SECURE")
    risk_session = request.headers.get("X-RISK-SESSION")
    origin = request.headers.get("Origin")
    
    if not all([x_payment, x_payment_secure, risk_session, origin]):
        return JSONResponse(
            {"x402Version": 1, "accepts": [payment_requirements], "error": "Payment required"},
            status_code=402
        )
    
    try:
        payment_data = json.loads(base64.b64decode(x_payment))
        
        # Proxy handles risk evaluation internally
        result = await seller.verify_then_settle(
            payment_data,
            payment_requirements,
            x_payment_b64=x_payment,
            origin=origin,
            x_payment_secure=x_payment_secure,
            risk_sid=risk_session
        )
        
        # Risk passed - deliver service
        return {"data": "Premium content delivered"}
        
    except Exception as e:
        error_msg = str(e)
        
        # Handle risk denial (403 from proxy)
        if "Risk denied" in error_msg or "403" in error_msg:
            return JSONResponse(
                {"error": "Payment denied by risk assessment", "details": error_msg},
                status_code=403
            )
        
        # Handle other payment failures
        return JSONResponse(
            {"error": "Payment verification failed", "details": error_msg},
            status_code=402
        )
```

### Risk Response Headers

The proxy adds these headers to responses (for observability):

- `X-Risk-Decision`: The decision value (`allow`, `deny`, `review`)
- `X-Risk-Decision-ID`: Unique ID for this risk evaluation
- `X-Risk-TTL-Seconds`: How long this decision is valid

**Note**: The current `SellerClient` returns only the JSON body. To access these headers in your service, you would need to call the proxy endpoints directly with `httpx` instead of using the SDK wrapper.

### Error Handling Best Practices

```python
try:
    result = await seller.verify_then_settle(...)
except httpx.HTTPStatusError as e:
    if e.response.status_code == 403:
        # Risk engine denied the payment
        log_risk_denial(e.response.json())
        return JSONResponse({"error": "Payment denied"}, status_code=403)
    elif e.response.status_code == 422:
        # Validation error (bad headers, AP2 mismatch, etc.)
        return JSONResponse({"error": "Invalid payment data"}, status_code=422)
    else:
        # Other payment failures
        return JSONResponse({"error": "Payment failed"}, status_code=402)
except Exception as e:
    # Network or unexpected errors
    log_error(e)
    return JSONResponse({"error": "Service unavailable"}, status_code=503)
```

## Advanced Features

### AP2 Policy Enforcement

You can enforce agent payment protection (AP2) requirements by adding policy flags to your payment requirements:

```python
payment_requirements = {
    "scheme": "exact",
    "network": "base-sepolia",
    "maxAmountRequired": "100000",
    "resource": str(request.url),
    "payTo": "0xYourWalletAddress",
    "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    "extra": {
        "name": "USDC",
        "version": "2",
        "ap2": {
            "requireTrace": True,              # Require agent trace evidence
            "requireIntentMandate": False,     # Require intent mandate
            "requireCartMandate": False,       # Require cart mandate
            "requirePaymentMandate": False,    # Require payment mandate
            "acceptedMerchantIds": [           # Whitelist merchant DIDs (optional)
                "did:web:api.yourservice.com"
            ]
        }
    }
}
```

**Supported AP2 Policy Flags:**

- `requireTrace` (bool): Require agent trace context in payment
- `requireIntentMandate` (bool): Require intent_uid in AP2 evidence
- `requireCartMandate` (bool): Require cart_uid in AP2 evidence
- `requirePaymentMandate` (bool): Require payment_uid in AP2 evidence
- `acceptedMerchantIds` (list): Whitelist of accepted merchant DIDs

When a buyer provides the optional `X-AP2-EVIDENCE` header, the proxy validates:
- Origin binding (originHash matches Origin header)
- Payment hash binding (paymentHash matches X-PAYMENT)
- TTL (notBefore/notAfter/exp timestamps)
- Resource congruence (resource, network, asset, payTo match)
- Optional EIP-712 signature (if sig field present)

### Multi-Currency Support

To offer multiple payment options, return multiple entries in the `accepts` array of your 402 response:

```python
@app.get("/api/multi-currency-endpoint")
async def multi_currency_endpoint(request: Request):
    # Check for payment headers
    if not request.headers.get("X-PAYMENT"):
        # Offer multiple currency options
        base_requirements = {
            "scheme": "exact",
            "network": "base-sepolia",
            "resource": str(request.url),
            "payTo": "0xYourWalletAddress",
            "maxTimeoutSeconds": 30
        }
        
        usdc_option = {
            **base_requirements,
            "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
            "maxAmountRequired": "1000000",  # 1.00 USDC
            "extra": {"name": "USDC", "version": "2"}
        }
        
        usdt_option = {
            **base_requirements,
            "asset": "0x...",  # USDT address
            "maxAmountRequired": "1000000",  # 1.00 USDT
            "extra": {"name": "USDT", "version": "2"}
        }
        
        dai_option = {
            **base_requirements,
            "asset": "0x...",  # DAI address
            "maxAmountRequired": "1050000",  # 1.05 DAI (slight premium)
            "extra": {"name": "DAI", "version": "2"}
        }
        
        return JSONResponse(
            {
                "x402Version": 1,
                "accepts": [usdc_option, usdt_option, dai_option],
                "error": "Payment required"
            },
            status_code=402
        )
    
    # Process payment normally
    # ... (verify_then_settle code)
```

**Note**: The proxy automatically strips non-standard fields from `extra` before forwarding to the upstream facilitator, keeping only `name` and `version` for EIP-3009 signing.

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

### Planned Features

The following features are under development and not yet available:

- **Subscription Management**: Recurring payment handling
- **Usage-Based Billing**: Metered API usage tracking and charging
- **Automated Dispute Handling**: Dispute resolution workflows with evidence
- **Challenge Flows**: Pre-transaction user confirmation for high-risk payments

For updates on these features, see our [GitHub roadmap](https://github.com/t54-labs/x402-secure/issues).

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
  - [ ] Always verify before processing
  - [ ] Handle validation failures gracefully
  - [ ] Honor 4xx/5xx from proxy and surface X-Request-ID for support

- [ ] **Settlement**
  - [ ] Always settle after delivery
  - [ ] Handle settlement failures
  - [ ] Implement retry logic for safe failures

- [ ] **Monitoring**
  - [ ] Log all payment attempts with X-Request-ID
  - [ ] Log X-Risk-* headers from proxy responses (if accessing directly)
  - [ ] Monitor validation and settlement failures
  - [ ] Track 403 (risk denial) vs 422 (validation) vs other errors

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
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from x402_secure_client import SellerClient
import base64
import json
import logging
import httpx

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
    if not all([x_payment, x_payment_secure, risk_session, origin]):
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
        
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error processing payment: {e.response.status_code}")
        
        # Handle different error types
        if e.response.status_code == 403:
            # Risk engine denied the payment
            return JSONResponse(
                {"error": "Payment denied by risk assessment"},
                status_code=403
            )
        elif e.response.status_code == 422:
            # Validation error (bad headers, AP2 mismatch, etc.)
            return JSONResponse(
                {"error": "Invalid payment data", "details": str(e)},
                status_code=422
            )
        else:
            # Other payment failures
            return JSONResponse(
                {"error": "Payment verification failed", "details": str(e)},
                status_code=402
            )
    
    except Exception as e:
        logger.error(f"Unexpected error processing request: {e}")
        return JSONResponse(
            {"error": "Service unavailable", "details": str(e)},
            status_code=503
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
