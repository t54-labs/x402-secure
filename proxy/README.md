# X-402 Proxy Server

A FastAPI-based proxy that validates AP2 (Agent Payments Protocol) evidence and forwards payments to upstream x402 facilitators.

**Note:** This is an internal service package, not published to PyPI. For the client SDK, see `packages/x402-secure`.

## Features

- ✅ **AP2 Policy Enforcement** - Validates X-AP2-EVIDENCE header against merchant policy
- ✅ **Risk gating** - evaluates every payment against the risk engine (`/risk/evaluate`) before forwarding
- ✅ **Cryptographic Validation** - Payment hash binding, origin binding, TTL checks
- ✅ **EIP-712 Signatures** - Optional payer signature verification
- ✅ **Clean x402 Forwarding** - Strips custom fields before forwarding to upstream
- ✅ **Flexible Upstream** - Works with any x402 v1 facilitator, or the bundled stub

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
from x402_proxy import risk_router, router

app = FastAPI()

# Mount the proxy and risk routers. /x402/verify calls /risk/evaluate in-app
# when PROXY_LOCAL_RISK=1, so local mode needs risk_router mounted.
app.include_router(router)
app.include_router(risk_router)

# Now your app exposes:
# - POST /x402/verify
# - POST /x402/settle
# - POST /risk/session, /risk/trace, /risk/evaluate
```

`run_facilitator_proxy.py` at the repo root builds the full app, including the internal facilitator router.

### Configuration

Configure via environment variables:

```bash
# Upstream facilitator endpoints
export PROXY_UPSTREAM_VERIFY_URL=https://x402.org/facilitator/verify
export PROXY_UPSTREAM_SETTLE_URL=https://x402.org/facilitator/settle

# Risk engine (required unless PROXY_LOCAL_RISK=1)
export RISK_ENGINE_URL=<risk engine base URL>
# Or, for development only: in-memory risk sessions and traces in this process
# export PROXY_LOCAL_RISK=1

# Timeout for upstream requests
export PROXY_TIMEOUT_S=15
# Optional: extend network→chainId mapping used for EIP-712 verification
# Either JSON or comma-separated pairs
export PROXY_NETWORK_CHAIN_MAP='base:8453,base-sepolia:84532,optimism:10'
# Optional: enable risk evaluation during settlement (default: disabled)
export PROXY_SETTLE_RISK_ENABLED=false
export PROXY_PAYMENT_AUTHORIZATION_KEY="$(openssl rand -hex 32)"
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
  ├─ 7. Evaluate risk (POST /risk/evaluate; deny/review → 403)
  ├─ 8. Optional VI assessment
  ├─ 9. Forward x402 v1 request (extra reduced to name/version)
  └─ 10. On success return X-PAYMENT-AUTHORIZATION (required on /x402/settle)
       ↓
Upstream x402 Facilitator (CDP, local, etc.)
  └─ Validates standard x402 payment and returns result
```

### X-AP2-EVIDENCE Structure

Header value: base64 of this JSON (or the `evd.v1;mr=...` mandate reference form).

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

When `PROXY_LOCAL_RISK` is not set, the proxy calls your risk engine at `RISK_ENGINE_URL`:

- `POST /risk/evaluate`: called on every `/x402/verify` (and on `/x402/settle` when `PROXY_SETTLE_RISK_ENABLED` is set); must return `decision`, `decision_id`, `ttl_seconds`
- `POST /risk/session` and `POST /risk/trace`: buyer calls to the gateway's `/risk/*` routes are forwarded here

`RISK_INTERNAL_TOKEN` is sent as a Bearer token. `RISK_ENGINE_COMPAT=trustline` adapts session and trace payloads.

## Environment Variables

- `PROXY_UPSTREAM_VERIFY_URL` / `PROXY_UPSTREAM_SETTLE_URL`: Upstream x402 facilitator URLs.
- `RISK_ENGINE_URL`: Base URL of the risk engine that receives `/risk/evaluate` (and forwarded `/risk/session`, `/risk/trace`) calls. Required unless `PROXY_LOCAL_RISK=1`.
- `PROXY_LOCAL_RISK`: `1` keeps risk sessions and traces in memory in this process instead of calling a risk engine. For development only; it is per process, so do not combine it with multiple workers.
- `PROXY_LOCAL_RISK_TTL`: Lifetime in seconds of local-mode sessions and traces (default 900).
- `RISK_INTERNAL_TOKEN`: Bearer token sent to the risk engine.
- `RISK_ENGINE_COMPAT`: Set to `trustline` to adapt session and trace payloads to the Trustline API.
- `X402_SECURE_VI_RECEIPT_STORE`, `X402_SECURE_VI_RECEIPT_REDIS_URL`, `X402_SECURE_VI_RECEIPT_REDIS_TIMEOUT_SECONDS`, `X402_SECURE_VI_RECEIPT_TTL_SECONDS`: VI receipt store (see `env.example`).
- `PROXY_TIMEOUT_S`: Timeout for upstream calls.
- `PROXY_NETWORK_CHAIN_MAP`: JSON or `k:v` pairs extending network→chainId mapping for EIP-712 verification.
- Set `PROXY_PAYMENT_AUTHORIZATION_KEY` to a random secret of at least 32 bytes shared by all replicas serving the same upstream pair. Pass the verify response's `X-PAYMENT-AUTHORIZATION` on settle. The authorization expires with the risk and VI decisions and binds the payment, requirements, policy, evidence, and upstream pair.
- `PROXY_SETTLE_RISK_ENABLED=true` adds a second risk evaluation before forwarding to settle.
- `PROXY_PUBLIC_MERCHANT_POLICY_JSON` defines the deployment's AP2 and VI requirements, for example `{"ap2":{"requirePaymentMandate":true},"vi":{"requireVerifiedIntent":true}}`. Request policies can tighten the deployment policy. Use separate gateway deployments when merchants need different baseline policies. The default blocks review. A deployment may explicitly allow Trustline VI review through its server-side `vi.reviewMode`.
- Mandatory AP2 expects signed, payment-bound AP2 evidence in `ap2EvidenceHeader` or `X-AP2-EVIDENCE`, with a signed expiry. The `evd.v1` header carries an optional mandate reference and can accompany the signed body envelope. Set `allowAp2MandateReference: true` in the deployment policy only where a VI mandate reference is sufficient. AP2 mandate requirements still require the signed envelope.
- The public upstream adapter uses the x402 v1 `/verify` and `/settle` contract. It sends equivalent `paymentPayload` and `paymentHeader` representations and forwards the EIP-3009 `name` and `version` metadata from the quote. Verify the configured pair against this contract per deployment.

## Testing

From the repo root:

```bash
uv run pytest
```

## License

Apache-2.0


## Internal facilitator service scopes

For one facilitator on a dedicated deployment, configure `X402_SECURE_INTERNAL_TOKEN` and
`X402_SECURE_INTERNAL_TRUSTLINE_TOKEN`. Set
`X402_SECURE_INTERNAL_ORGANIZATION`, `X402_SECURE_INTERNAL_ENVIRONMENT`,
`X402_SECURE_INTERNAL_POLICY` and `X402_SECURE_INTERNAL_POLICY_VERSION` to the values the
facilitator supplies. An unset scope field accepts requests that omit that selector.
The default actions are `evaluate,evidence_session,evidence_upload,receipt_write`.
Explicitly grant `receipt_lookup` with `X402_SECURE_INTERNAL_ACTIONS` if this service reads receipts.

For multiple services or tenants, set `X402_SECURE_INTERNAL_SERVICES_JSON` to an object
keyed by service name. Give each service a distinct inbound `token`, its own fixed
`organization`, `environment`, `policy`, optional `policy_version`, and an explicit `actions` array.
Set `trustline_token` to the downstream credential for those selectors for every service
that calls Trustline. A service with only `receipt_lookup` uses the shared receipt store.
A reader and writer can share receipts by using the same organization, environment,
policy and policy version. Existing unscoped receipt keys expire under the prior TTL.
Remove legacy organization/policy credential maps when switching over. A deployment
uses a fixed downstream credential for each authenticated scope.

For XRPL evaluate calls, send validated `payment.amount`, `payment.asset` and
`payment.amountUnit`. Native amounts use `xrp` or integer `drops`. Issued amounts use
`issued` and include `payment.issuer`. Roll out the facilitator amount-unit contract with the proxy.
