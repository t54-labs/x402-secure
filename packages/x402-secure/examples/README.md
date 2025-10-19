# x402-secure Examples

Examples demonstrating the x402-secure SDK for AI agent payments with AP2 verification.

## Quick Setup

1. **Create virtual environment:**
   ```bash
   cd packages/x402-secure/examples
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. **Install from PyPI:**
   ```bash
   pip install "x402-secure[examples]"
   
   # For agent examples (buyer_agent_openai.py), also install:
   pip install "x402-secure[agent]"
   # Or alternatively: pip install openai
   ```

3. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env and add:
   # - BUYER_PRIVATE_KEY (required)
   # - OPENAI_API_KEY (required for agent example)
   # 
   # ⚠️ IMPORTANT: Never commit .env to version control - it contains secrets!
   ```

## Environment Variables

### Required
- `BUYER_PRIVATE_KEY` - Your wallet private key for signing payments
- `OPENAI_API_KEY` - OpenAI API key (for agent examples)

### Optional (with defaults)
- `AGENT_GATEWAY_URL` - Agent gateway base URL (default: http://localhost:8000)
  - Used by buyers for `/risk/*` endpoints and sellers for `/x402/*` proxy endpoints
- `SELLER_BASE_URL` - Seller API URL (default: http://localhost:8010)
- `NETWORK` - Blockchain network (default: base-sepolia)
- `OPENAI_MODEL` - AI model (default: gpt-5-mini)
- `MERCHANT_PAYTO` - Merchant wallet address
- `USDC_ADDRESS` - USDC contract address
- `OTEL_EXPORTER_OTLP_ENDPOINT` - OpenTelemetry endpoint (optional, for trace export)
- `OTEL_SERVICE_NAME` - Service name for traces (default: x402-buyer)
- `AGENT_ORIGIN` - Agent origin for session context (optional)
- `CLIENT_IP` - Client IP for session context hashing (optional)

## Complete Flow Demo

To run a complete end-to-end demo:

### 1. Start Facilitator Proxy (Terminal 1)
```bash
cd /path/to/repo/root
uv run run_facilitator_proxy.py
# Runs on http://localhost:8000
```

### 2. Start Seller (Terminal 2)
```bash
cd packages/x402-secure/examples
source .venv/bin/activate
python -m uvicorn seller_integration:app --host 0.0.0.0 --port 8010
```

### 3. Run Buyer (Terminal 3)
```bash
cd packages/x402-secure/examples
source .venv/bin/activate

# Basic buyer
python buyer_basic.py

# Or AI agent buyer
python buyer_agent_openai.py
```

## Examples

### 1. Upstream Stub (Mock Facilitator)
Mock upstream facilitator for testing.

**Run:**
```bash
python -m uvicorn upstream_stub:app --port 9000
```

**To use with the proxy:**
Before starting the facilitator proxy, set these environment variables to point to the stub:
```bash
export PROXY_UPSTREAM_VERIFY_URL=http://localhost:9000/verify
export PROXY_UPSTREAM_SETTLE_URL=http://localhost:9000/settle
```

### 2. Seller Integration
Seller implementation example for x402 protocol.

**Run:**
```bash
python -m uvicorn seller_integration:app --host 0.0.0.0 --port 8010
```

**Test endpoint:**
```bash
curl http://localhost:8010/api/market-data?symbol=BTC/USD
# Returns 402 Payment Required (expected without payment headers)
```

### 3. Basic Buyer
Simple buyer client example.

**Run:**
```bash
python buyer_basic.py
```

### 4. OpenAI Agent Buyer
Advanced buyer with OpenAI agent integration, multi-turn conversation, and full trace collection.

**Run:**
```bash
python buyer_agent_openai.py
```

## Troubleshooting

### Port 8000 already in use
```bash
lsof -i :8000
kill <PID>
```

### Seller gets 404 on /x402/verify
Ensure `AGENT_GATEWAY_URL` points to the facilitator proxy (port **8000**), not the seller port:
```bash
AGENT_GATEWAY_URL=http://localhost:8000
```
The `SellerClient` automatically appends `/x402` to the gateway URL.

### ModuleNotFoundError
Install with examples support:
```bash
pip install "x402-secure[examples]"
```

### Missing environment variables
Make sure you have `.env` file with required variables:
```bash
cp .env.example .env
# Add BUYER_PRIVATE_KEY and OPENAI_API_KEY
```

## See Also
- [Buyer Integration Guide](../../../docs/BUYER_INTEGRATION.md)
- [Seller Integration Guide](../../../docs/SELLER_INTEGRATION.md)
- [Quickstart](../../../docs/QUICKSTART.md)
