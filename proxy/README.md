# X-402 Proxy Server

A FastAPI-based proxy that validates AP2 (Attestation Protocol 2) evidence and forwards payments to upstream x402 facilitators.

**Note:** This is an internal service package, not published to PyPI. For the client SDK, see `packages/x402-secure`.

## Features

- ✅ **AP2 Policy Enforcement** - Validates X-AP2-EVIDENCE header against merchant policy
- ✅ **UID-Based Risk Assessment** - Fetches trace context and PaymentMandate from Risk Engine using UIDs
- ✅ **Cryptographic Validation** - Payment hash binding, origin binding, TTL checks
- ✅ **EIP-712 Signatures** - Optional payer signature verification
- ✅ **Clean x402 Forwarding** - Strips custom fields before forwarding to upstream
- ✅ **Flexible Upstream** - Works with CDP facilitator, local Risk Engine, or stubs

## Installation

```bash
# Install from source (internal use)
cd proxy
pip install -e .
```

## Quick Start

### Basic Usage

```python
from fastapi import FastAPI
from x402_proxy import router

app = FastAPI()

# Mount the proxy router
app.include_router(router)

# Now your app exposes:
# - POST /x402/verify
# - POST /x402/settle
# - GET /x402/debug
```

### Configuration

Configure via environment variables:

```bash
# Upstream facilitator endpoints
export PROXY_UPSTREAM_VERIFY_URL=https://x402.org/facilitator/verify
export PROXY_UPSTREAM_SETTLE_URL=https://x402.org/facilitator/settle

# Risk Engine for AP2 UID lookups
export RISK_ENGINE_URL=http://localhost:8001

# Timeout for upstream requests
export PROXY_TIMEOUT_S=15
# Disable debug endpoint in production (recommended)
export PROXY_DEBUG_ENABLED=false
# Optional: extend network→chainId mapping used for EIP-712 verification
# Either JSON or comma-separated pairs
export PROXY_NETWORK_CHAIN_MAP='base:8453,base-sepolia:84532,optimism:10'
# Optional: enable risk evaluation during settlement (default: disabled)
export PROXY_SETTLE_RISK_ENABLED=false
```

Or via dependency injection:

```python
from x402_proxy import ProxyRuntimeConfig, get_proxy_cfg

def custom_proxy_cfg() -> ProxyRuntimeConfig:
    return ProxyRuntimeConfig(
        upstream_verify_url="https://your-facilitator.com/verify",
        upstream_settle_url="https://your-facilitator.com/settle",
        timeout_s=30.0
    )

app.dependency_overrides[get_proxy_cfg] = custom_proxy_cfg
```

## Architecture

### Flow Diagram

```
Buyer (X-PAYMENT + X-AP2-EVIDENCE)
  ↓
Seller
  ↓
Facilitator Proxy (/x402/verify)
  ├─ 1. Validate AP2 policy (from PaymentRequirements.extra.ap2)
  ├─ 2. Parse X-AP2-EVIDENCE (paymentHash, resource, originHash, etc.)
  ├─ 3. Verify congruence (resource, network, asset, payTo)
  ├─ 4. Verify TTL (notBefore, notAfter)
  ├─ 5. Verify origin binding (originHash)
  ├─ 6. Verify payment hash binding (paymentHash)
  ├─ 7. Fetch trace context via trace_uid (from Risk Engine)
  ├─ 8. Fetch PaymentMandate VC via payment_uid (from Risk Engine)
  ├─ 9. Strip custom AP2 fields from PaymentRequirements
  └─ 10. Forward clean x402 to upstream facilitator
       ↓
Upstream x402 Facilitator (CDP, local, etc.)
  └─ Validates standard x402 payment and returns result
```

### X-AP2-EVIDENCE Structure

```json
{
  "v": 1,
  "paymentHash": "0x...",
  "resource": "https://...",
  "originHash": "0x...",
  "network": "base-sepolia",
  "asset": "0x...",
  "payTo": "0x...",
  "intent_uid": "0x...",
  "cart_uid": "0x...",
  "payment_uid": "0x...",
  "trace_uid": "0x...",
  "notBefore": 1234567890,
  "notAfter": 1234567999,
  "sig": "0x...",
  "kid": "eoa:0x..."
}
```

## Risk Engine Integration

The proxy fetches deep risk assessment data from your Risk Engine using UIDs:

Required APIs (read by the proxy):
- `GET /ap2/trace/{trace_uid}` - Returns trace context
- `GET /ap2/mandate/payment/{payment_uid}` - Returns PaymentMandate VC

## Environment Variables

- `PROXY_UPSTREAM_VERIFY_URL` / `PROXY_UPSTREAM_SETTLE_URL`: Upstream x402 facilitator URLs.
- `RISK_ENGINE_URL`: Base URL of your Risk Engine for trace/mandate lookups (default: `http://localhost:8001`).
- `PROXY_TIMEOUT_S`: Timeout for upstream calls.
- `PROXY_DEBUG_ENABLED`: `true`/`false` toggle for `/x402/debug` endpoint (default: `true`).
- `PROXY_NETWORK_CHAIN_MAP`: JSON or `k:v` pairs extending network→chainId mapping for EIP-712 verification.
- `PROXY_SETTLE_RISK_ENABLED`: When `true`, the proxy calls `/risk/evaluate` during `POST /x402/settle`. Default is `false` to avoid double evaluation; can be enabled later.

## Testing

```bash
cd sdk/secure_x402
pytest
```

## License

Apache-2.0
