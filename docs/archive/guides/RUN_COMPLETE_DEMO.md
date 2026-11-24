> ⚠️ **Historical document: do not build or operate from it.** A 2025-10 demo walkthrough. Its scripts are gone, and its commands predate the payment authorization key that verify and settle now require.
>
> Current source: [`packages/x402-secure/examples/README.md`](../../../packages/x402-secure/examples/README.md).
>
> Moved from `docs/guides/RUN_COMPLETE_DEMO.md`. Checked against `main@b4526b8` on 2026-11-24.

# 🚀 Complete Flow Demo - One-Click Launch

## ✅ Fixed Issues

1. ✅ OTLP exporter disabled (no more 4318 connection errors)
2. ✅ PROXY_LOCAL_RISK local mode fully available
3. ✅ Complete trace context log output
4. ✅ Official x402.org facilitator integration

---

## 🎯 Quick Start (3 Steps)

### Step 1: Set Buyer Private Key
```bash
export BUYER_PRIVATE_KEY=<your_private_key>
```

Or generate a new wallet:
```bash
uv run python scripts/create_wallets.py
```

### Step 2: Start Services
```bash
./start_demo.sh
```

### Step 3: Run Tests
```bash
# Basic example
./test_complete_flow.sh basic

# Agent example (requires export OPENAI_API_KEY=sk-... first)
./test_complete_flow.sh agent
```

---

## 📋 Complete Manual Commands (For Debugging)

### Terminal 1: Start Proxy (8000)
```bash
PROXY_LOCAL_RISK=1 \
PROXY_PORT=8000 \
PROXY_UPSTREAM_VERIFY_URL=https://x402.org/facilitator/verify \
PROXY_UPSTREAM_SETTLE_URL=https://x402.org/facilitator/settle \
uv run python run_facilitator_proxy.py
```

### Terminal 2: Start Seller (8010)
```bash
uv run uvicorn --app-dir packages/x402-secure/examples seller_integration:app --port 8010
```

### Terminal 3: Run Buyer

**Option A: Basic Example**
```bash
AGENT_GATEWAY_URL=http://localhost:8000 \
SELLER_BASE_URL=http://localhost:8010 \
uv run python packages/x402-secure/examples/buyer_basic.py
```

**Option B: OpenAI Agent Example**
```bash
export OPENAI_API_KEY=sk-proj-...
AGENT_GATEWAY_URL=http://localhost:8000 \
SELLER_BASE_URL=http://localhost:8010 \
uv run python packages/x402-secure/examples/buyer_agent_openai.py
```

---

## ✅ Expected Output

### 1. Buyer-side Logs
```
================================================================================
🔐 PAYMENT HEADERS
================================================================================
📍 URL: http://localhost:8010/api/market-data
🆔 X-RISK-SESSION: 84e35e6e-8260-42fb-a811-5c25089efbf8
🔒 X-PAYMENT-SECURE: w3c.v1;tp=00-b5ccb78e8b2640d785213de685ac2644-82516a311157fe39-01;ts=...

📊 X-PAYMENT-SECURE Details:
   traceparent: 00-b5ccb78e8b2640d785213de685ac2644-82516a311157fe39-01
   tracestate (decoded): {
      "tid": "dde4fbf8-1448-43c0-a556-e6e98353416b"
   }
================================================================================
```

### 2. Seller-side Logs
```
================================================================================
📥 SELLER RECEIVED HEADERS
================================================================================
🌐 Origin: http://localhost:8010
🆔 X-RISK-SESSION: 84e35e6e-8260-42fb-a811-5c25089efbf8
🔒 X-PAYMENT-SECURE: w3c.v1;tp=00-b5ccb78e8b2640d785213de685ac2644-...

📊 Trace Context:
   traceparent: 00-b5ccb78e8b2640d785213de685ac2644-82516a311157fe39-01
   tracestate: {
      "tid": "dde4fbf8-1448-43c0-a556-e6e98353416b"
   }
================================================================================
```

### 3. OpenTelemetry Span (Console Output)
```json
{
  "name": "buyer.payment",
  "context": {
    "trace_id": "0xb5ccb78e8b2640d785213de685ac2644",
    "span_id": "0x82516a311157fe39",
    "trace_state": "[]"
  },
  "status": {
    "status_code": "UNSET"
  }
}
```

### 4. Final Business Result
```json
{
  "symbol": "BTC/USD",
  "price": 63500.12,
  "source": "oss-demo"
}
```

**Exit code: 0** ✅

---

## 🔧 Advanced Configuration

### Enable OTLP Collector (Optional)
```bash
# 1. Run collector
docker run -p 4318:4318 otel/opentelemetry-collector-contrib

# 2. Set environment variable
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318/v1/traces

# 3. Run example (exports to both console and OTLP)
```

### Use Different OpenAI Model
```bash
export OPENAI_MODEL=gpt-4o-mini  # Or gpt-4o, o1-mini, etc.
```

---

## 📊 Complete Data Flow

```
1️⃣  Buyer → Gateway /risk/session
    └─ Create sid

2️⃣  (Agent mode) OpenAI tool calls
    └─ Turn 1: list_available_merchants
    └─ Turn 2: prepare_payment

3️⃣  Buyer → Gateway /risk/trace
    └─ Upload agent trace (task + params + events)
    └─ Create tid

4️⃣  Buyer builds OpenTelemetry span
    └─ Generate traceparent (W3C format)
    └─ Encode tracestate: base64({"tid": "..."})

5️⃣  Buyer → Seller (with complete headers)
    ├─ X-PAYMENT (EIP-3009 signature)
    ├─ X-PAYMENT-SECURE (trace context + tid)
    ├─ X-RISK-SESSION (sid)
    └─ Origin

6️⃣  Seller → Gateway /x402/verify
    └─ Gateway → Local /risk/evaluate
       ├─ Verify sid exists ✓
       ├─ Verify tid linked ✓
       └─ Decision: allow

7️⃣  Seller → Gateway /x402/settle
    └─ Gateway → x402.org /facilitator/settle
       └─ 200 OK ✓

8️⃣  Seller → Buyer
    └─ Return business data
```

---

## 🎊 Success Indicators

- ✅ No OTLP connection errors
- ✅ Complete header information printed
- ✅ sid/tid correctly created and passed
- ✅ traceparent and tracestate format correct
- ✅ Payment verification and settlement successful
- ✅ Business data returned

**Exit code: 0** 🎯
