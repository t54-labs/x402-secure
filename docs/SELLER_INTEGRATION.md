# Seller/API Integration Guide

This guide helps API and service providers accept secure payments from AI agents using x402-secure.

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [Basic Integration](#basic-integration)
4. [Risk Management](#risk-management)
5. [Advanced Features](#advanced-features)
6. [Production Checklist](#production-checklist)

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
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from x402_secure_client import (
    PaymentVerificationError,
    SellerClient,
    SettlementDeclined,
    SettlementUnknown,
)
import base64
import json

app = FastAPI()

# Initialize seller client with the gateway URL (SellerClient appends /x402)
seller = SellerClient("https://x402-proxy.t54.ai")

@app.post("/api/v1/generate-text")
async def generate_text(request: Request, prompt: str):
    # Define payment requirements
    payment_requirements = {
        "scheme": "exact",
        "network": "base-sepolia",
        "maxAmountRequired": "100000",  # 0.10 USDC
        "resource": str(request.url),
        "description": "Text generation API",
        "mimeType": "application/json",
        "payTo": "0xYourWalletAddress",
        "maxTimeoutSeconds": 30,
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",  # USDC on Base Sepolia
        "extra": {"name": "USDC", "version": "2"},
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
    except Exception:
        return JSONResponse({"error": "Invalid X-PAYMENT"}, status_code=402)

    try:
        result = await seller.verify_then_settle(
            payment_data,
            payment_requirements,
            x_payment_b64=x_payment,
            origin=origin,
            x_payment_secure=x_payment_secure,
            risk_sid=risk_session
        )
    except (PaymentVerificationError, SettlementDeclined):
        # Verification failed (including a risk deny or review) or settlement was declined
        return JSONResponse({"error": "Payment declined"}, status_code=402)
    except SettlementUnknown:
        return JSONResponse({"error": "Settlement could not be confirmed"}, status_code=502)

    # Process the request
    text_result = await process_text_generation(prompt)

    # Return response with payment receipt
    response_data = {"result": text_result}
    return JSONResponse(
        response_data,
        headers={"X-PAYMENT-RESPONSE": base64.b64encode(json.dumps(result).encode()).decode()}
    )
```

### 2. Flask Integration

```python
from flask import Flask, request, jsonify
from x402_secure_client import (
    PaymentVerificationError,
    SellerClient,
    SettlementDeclined,
    SettlementUnknown,
)
import asyncio
import base64
import json

app = Flask(__name__)

GATEWAY_URL = "https://x402-proxy.t54.ai"  # SellerClient appends /x402

@app.route("/api/v1/analyze", methods=["POST"])
def analyze_data():
    # Define payment requirements
    payment_requirements = {
        "scheme": "exact",
        "network": "base-sepolia",
        "maxAmountRequired": "1000000",  # 1.00 USDC
        "resource": request.url,
        "description": "Data analysis API",
        "mimeType": "application/json",
        "payTo": "0xYourWalletAddress",
        "maxTimeoutSeconds": 30,
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "extra": {"name": "USDC", "version": "2"},
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

    try:
        payment_data = json.loads(base64.b64decode(x_payment))
    except Exception:
        return jsonify({"error": "Invalid X-PAYMENT"}), 402

    # SellerClient is async-only. Its httpx.AsyncClient cannot be reused across
    # event loops, so create one per call inside the loop that asyncio.run starts.
    async def _verify_and_settle():
        seller = SellerClient(GATEWAY_URL)
        try:
            return await seller.verify_then_settle(
                payment_data,
                payment_requirements,
                x_payment_b64=x_payment,
                origin=origin,
                x_payment_secure=x_payment_secure,
                risk_sid=risk_session
            )
        finally:
            await seller.http.aclose()

    try:
        result = asyncio.run(_verify_and_settle())
    except (PaymentVerificationError, SettlementDeclined):
        return jsonify({"error": "Payment declined"}), 402
    except SettlementUnknown:
        return jsonify({"error": "Settlement could not be confirmed"}), 502

    # Process request
    data = request.json
    analysis_result = analyze_user_data(data)

    return jsonify({
        "result": analysis_result
    }), 200, {"X-PAYMENT-RESPONSE": base64.b64encode(json.dumps(result).encode()).decode()}
```

> **Note**: SellerClient is async-only. In Flask, run it with `asyncio.run(...)` and a client created per call, as above, or use an async framework such as FastAPI.

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
- `X-AP2-EVIDENCE`: AP2 mandate reference (format: `evd.v1;mr=...;ms=...;mt=application/json;sz=...`), or base64(JSON signed AP2 evidence); see `protocol-spec/openapi.yaml`

Your service forwards these headers to the proxy via `SellerClient`; the proxy validates them and calls the risk engine before forwarding to the upstream facilitator.

## Risk Management

### How Risk Gating Works

Risk evaluation happens **inside the proxy** before payment verification reaches the upstream facilitator:

1. Your service calls `seller.verify_then_settle(...)` with payment headers
2. The proxy calls `/risk/evaluate` with session ID, trace context, and payment data
3. The risk engine returns a decision: `allow`, `deny`, or `review`
4. If `deny` or `review`, the proxy returns 403 with an error body (`RISK_DENIED` or `RISK_REVIEW`); `SellerClient` raises `PaymentVerificationError`
5. If `allow`, the proxy forwards the request to the upstream facilitator

### Handling Risk Decisions

```python
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from x402_secure_client import (
    PaymentVerificationError,
    SellerClient,
    SettlementDeclined,
    SettlementUnknown,
)
import base64
import json

app = FastAPI()
seller = SellerClient("https://x402-proxy.t54.ai")  # SellerClient appends /x402

@app.post("/api/premium-feature")
async def premium_feature(request: Request):
    payment_requirements = {
        "scheme": "exact",
        "network": "base-sepolia",
        "maxAmountRequired": "500000",  # 0.50 USDC
        "resource": str(request.url),
        "description": "Premium feature",
        "mimeType": "application/json",
        "payTo": "0xYourWalletAddress",
        "maxTimeoutSeconds": 30,
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "extra": {"name": "USDC", "version": "2"},
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
    except Exception:
        return JSONResponse({"error": "Invalid X-PAYMENT"}, status_code=402)

    try:
        # Proxy handles risk evaluation internally
        result = await seller.verify_then_settle(
            payment_data,
            payment_requirements,
            x_payment_b64=x_payment,
            origin=origin,
            x_payment_secure=x_payment_secure,
            risk_sid=risk_session
        )
    except (PaymentVerificationError, SettlementDeclined):
        # A risk deny or review, a failed verification and a declined settlement
        # all raise one of these; SellerClient does not expose the proxy status.
        return JSONResponse({"error": "Payment declined"}, status_code=402)
    except SettlementUnknown:
        return JSONResponse({"error": "Settlement could not be confirmed"}, status_code=502)

    # Risk passed and payment settled - deliver service
    return {"data": "Premium content delivered"}
```

### Risk Response Headers

The proxy adds these headers to successful `/x402/verify` and `/x402/settle` responses (for observability):

- `X-Risk-Decision`: `allow` (set only on successful responses; failures return an error body with `error.code`, e.g. `RISK_DENIED`, `RISK_REVIEW`)
- `X-Risk-Decision-ID`: Unique ID for this risk evaluation
- `X-Risk-TTL-Seconds`: How long this decision is valid

**Note**: `SellerClient` returns only the JSON body. After a verify call, `SellerClient.last_vi_headers` holds `X-PAYMENT-AUTHORIZATION`, `X-VI-DECISION-ID` and `X-VI-EVIDENCE-REF` when present. To read the `X-Risk-*` headers, call the proxy endpoints directly with `httpx`.

### Error Handling Best Practices

`SellerClient` does not raise `httpx.HTTPStatusError`. It wraps every failure in one of three exceptions, and does not pass on the proxy's status code or error body:

```python
try:
    result = await seller.verify_then_settle(...)
except PaymentVerificationError:
    # Verification failed: risk deny/review, invalid payment, bad headers or proxy error
    return JSONResponse({"error": "Payment declined"}, status_code=402)
except SettlementDeclined:
    # The facilitator explicitly declined settlement
    return JSONResponse({"error": "Payment declined"}, status_code=402)
except SettlementUnknown:
    # Settlement outcome not confirmed; do not serve the content
    return JSONResponse({"error": "Settlement could not be confirmed"}, status_code=502)
```

See [Settlement outcomes](#settlement-outcomes) for how to treat each case.

## Advanced Features

### AP2 Policy Enforcement

You can enforce Agent Payments Protocol (AP2) requirements by adding policy flags to your payment requirements:

```python
payment_requirements = {
    "scheme": "exact",
    "network": "base-sepolia",
    "maxAmountRequired": "100000",
    "resource": str(request.url),
    "description": "Premium API",
    "mimeType": "application/json",
    "payTo": "0xYourWalletAddress",
    "maxTimeoutSeconds": 30,
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

Setting any of these flags requires signed AP2 evidence (base64 JSON in `X-AP2-EVIDENCE` or the body's `ap2EvidenceHeader`, with `sig` and `notAfter`). The buyer SDK does not produce it yet, so payments from SDK buyers fail with 422 when any flag is set.

- `requireTrace`: evidence must include `trace_uid`
- `requireIntentMandate` / `requireCartMandate` / `requirePaymentMandate`: evidence must include `intent_uid` / `cart_uid` / `payment_uid`
- `acceptedMerchantIds`: `did:web:<host>` entries; the `resource` host must match one of them

When AP2 evidence is present or required, the proxy validates:
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
            "description": "Multi-currency API",
            "mimeType": "application/json",
            "payTo": "0xYourWalletAddress",
            "maxTimeoutSeconds": 30
        }

        usdc_option = {
            **base_requirements,
            "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
            "maxAmountRequired": "1000000",  # 1.00 USDC
            "extra": {"name": "USDC", "version": "2"}
        }

        # Add more options the same way. Only offer tokens that implement
        # EIP-3009 (e.g. USDC); extra.name/version must match the token's
        # EIP-712 domain.

        return JSONResponse(
            {
                "x402Version": 1,
                "accepts": [usdc_option],
                "error": "Payment required"
            },
            status_code=402
        )

    # Process payment normally
    # ... (verify_then_settle code)
```

**Note**: The proxy automatically strips non-standard fields from `extra` before forwarding to the upstream facilitator, keeping only `name` and `version` for EIP-3009 signing.

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
  - [ ] Deliver only after settlement succeeds
  - [ ] Handle settlement failures
  - [ ] Implement retry logic for safe failures

- [ ] **Monitoring**
  - [ ] Log all payment attempts with X-Request-ID
  - [ ] Log X-Risk-* headers from proxy responses (if accessing directly)
  - [ ] Monitor validation and settlement failures
  - [ ] Track verification failures vs declined vs unconfirmed settlements

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
from x402_secure_client import (
    PaymentVerificationError,
    SellerClient,
    SettlementDeclined,
    SettlementUnknown,
)
import base64
import json
import logging

app = FastAPI(title="AI Service API")
logger = logging.getLogger(__name__)

# Initialize seller client with the gateway URL (SellerClient appends /x402)
seller = SellerClient("https://x402-proxy.t54.ai")


def payment_requirements_for(request: Request) -> dict:
    # resource must be the URL of this request, so build the quote per request
    return {
        "scheme": "exact",
        "network": "base-sepolia",
        "maxAmountRequired": "1000000",  # 1.00 USDC
        "resource": str(request.url),
        "description": "AI Content Generation",
        "mimeType": "application/json",
        "payTo": "0xYourWalletAddress",
        "maxTimeoutSeconds": 30,
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",  # USDC
        "extra": {"name": "USDC", "version": "2"},
    }


# Protected endpoint
@app.post("/api/v1/generate")
async def generate_content(request: Request, prompt: str):
    payment_requirements = payment_requirements_for(request)

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
                "accepts": [payment_requirements],
                "error": "Payment required"
            },
            status_code=402
        )

    try:
        payment_data = json.loads(base64.b64decode(x_payment))
    except Exception:
        return JSONResponse({"error": "Invalid X-PAYMENT"}, status_code=402)

    logger.info(f"Processing payment from session {risk_session}")

    try:
        result = await seller.verify_then_settle(
            payment_data,
            payment_requirements,
            x_payment_b64=x_payment,
            origin=origin,
            x_payment_secure=x_payment_secure,
            risk_sid=risk_session
        )
    except (PaymentVerificationError, SettlementDeclined) as e:
        logger.warning(f"Payment declined: {type(e).__name__}")
        return JSONResponse({"error": "Payment declined"}, status_code=402)
    except SettlementUnknown:
        logger.error("Settlement outcome unknown; reconcile before retrying")
        return JSONResponse({"error": "Settlement could not be confirmed"}, status_code=502)

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
    uvicorn.run(app, host="0.0.0.0", port=8010)  # 8000 is the local proxy's default
```

## Support

- 📧 Email: support@x402-secure.com
- 💬 Discord: [Join our community](https://discord.gg/x402secure)
- 📖 API Docs: [docs.x402-secure.com](https://docs.x402-secure.com)


## Settlement outcomes

Serve paid content only after `verify_then_settle` returns a successful result with its
transaction reference. The SDK raises `PaymentVerificationError` when verification did not
establish validity, `SettlementDeclined` when the facilitator explicitly declined, and
`SettlementUnknown` when a pending, malformed or unavailable response leaves the outcome
unconfirmed. Return without serving the content for all three. Resolve an unknown outcome
through the facilitator's settlement status or reconciliation flow before authorizing another payment.

`verify_then_settle` forwards the payment authorization from the same verify call.
HTTP callers using separate calls must send the verify response's `X-PAYMENT-AUTHORIZATION`
on settle with the same payment, requirements and evidence.
