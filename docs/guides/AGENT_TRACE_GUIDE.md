# 📊 Complete Guide to Agent Trace Context Viewing

## 🎯 What Does Agent Trace Context Include?

Agent trace context is the complete execution record of an AI agent, including:

| Field | Description | Example |
|------|------|------|
| **task** | Task description | "Buy BTC price" |
| **parameters** | Task parameters | `{"symbol": "BTC/USD"}` |
| **environment** | Execution environment | `{"network": "base-sepolia", ...}` |
| **events** | Execution events | Tool calls, reasoning process, results, etc. |
| **model_config** | Model/provider and enabled tools | `{"provider": "openai", "model": "gpt-5-mini", ...}` |
| **session_context** | Session/request ids, agent_did, client_ip_hash | ... |
| **completed_at** | ISO 8601 upload time | `"2025-10-12T07:44:51+00:00"` |

---

## 🔍 Three Ways to View

### Method 1: Buyer Side (Before Upload)

The examples do not print the trace or the `tid`. To see them, print `tracer.events` before upload, or print the return value of `buyer.store_agent_trace(...)` (the `tid`).

---

### Method 2: Query via API (Most Detailed) ⭐ Recommended

Local mode only (`PROXY_LOCAL_RISK=1`). With a forwarding proxy this returns 501. The route has no authentication, so keep a local-mode proxy on your own machine. Traces live in memory for `PROXY_LOCAL_RISK_TTL` seconds (default 900).

**Steps**:
1. Get the tid (see Method 1 or Method 3)
2. Call GET /risk/trace/{tid}

**Command**:
```bash
# Query complete agent trace
TID="59db88a1-fda6-4d9c-9945-6d083f4a12a8"
curl -sS "http://localhost:8000/risk/trace/$TID" | python3 -m json.tool
```

**Returns**:
```json
{
  "sid": "6252ec97-c88e-4ee8-8d4b-6b905018cf23",
  "fingerprint": null,
  "telemetry": null,
  "agent_trace": {
    "task": "Buy BTC price",
    "parameters": {
      "symbol": "BTC/USD"
    },
    "environment": {
      "network": "base-sepolia",
      "seller_base_url": "http://localhost:8010"
    },
    "events": [
      {
        "ts": 1760253460.0290308,
        "type": "response.created"
      },
      {
        "ts": 1760253462.778178,
        "type": "reasoning_summary",
        "text": "I need to call the merchant listing tool..."
      },
      {
        "ts": 1760253462.8779762,
        "type": "function_call",
        "name": "list_available_merchants",
        "call_id": "call_ObmlZGYza18M0gXujU75yZZ9",
        "arguments": {
          "query": "BTC/USD"
        }
      },
      {
        "ts": 1760253462.878004,
        "type": "tool_call",
        "name": "list_available_merchants",
        "args": {
          "a": [],
          "k": {"query": "BTC/USD"}
        }
      },
      {
        "ts": 1760253462.878026,
        "type": "tool_result",
        "name": "list_available_merchants",
        "result": {
          "merchants": [...]
        }
      },
      {
        "type": "function_call",
        "name": "prepare_payment",
        "arguments": {
          "merchant_id": "price-demo-1",
          "symbol": "BTC/USD",
          "max_amount_atomic": 100000
        }
      },
      {
        "type": "tool_result",
        "name": "prepare_payment",
        "result": {
          "merchant_id": "price-demo-1",
          "seller_base_url": "http://localhost:8010",
          "endpoint": "/api/market-data",
          "params": {"symbol": "BTC/USD"},
          "payment_info": {...}
        }
      }
    ]
  }
}
```

---

### Method 3: Proxy Logs

**Local mode** (`PROXY_LOCAL_RISK=1`), proxy log at INFO:
```
[LOCAL] Trace created: tid=<tid>, sid=<sid>, events=14 ({...type counts...})
[LOCAL] Agent trace context: tid=<tid>, task=Buy BTC price, model=openai/gpt-5-mini, events=14 ({...})
```

The first line is logged on upload (`POST /risk/trace`), the second during `/x402/verify` → `/risk/evaluate`.

**Forward mode** (`PROXY_LOCAL_RISK=0`): the proxy forwards the trace and logs nothing about its content.

---

## 📋 Events Array Explained

Each event is a record with timestamp + type + data:

### Main Event Types

1. **response.created** - Agent starts execution
```json
{"ts": 1760253460.029, "type": "response.created"}
```

2. **reasoning_summary** - AI reasoning process (gpt-5 series)
```json
{
  "ts": 1760253462.778,
  "type": "reasoning_summary",
  "text": "I need to call the merchant listing tool..."
}
```

3. **function_call** - OpenAI decides to call a tool
```json
{
  "ts": 1760253462.878,
  "type": "function_call",
  "name": "list_available_merchants",
  "call_id": "call_Obml...",
  "arguments": {"query": "BTC/USD"}
}
```

4. **tool_call** - Actual Python function execution
```json
{
  "ts": 1760253462.878,
  "type": "tool_call",
  "name": "list_available_merchants",
  "args": {"a": [], "k": {"query": "BTC/USD"}}
}
```

5. **tool_result** - Tool return result
```json
{
  "ts": 1760253462.878,
  "type": "tool_result",
  "name": "list_available_merchants",
  "result": {"merchants": [...]}  // Complete return data
}
```

6. **response.completed** - Agent finishes execution
```json
{"ts": 1760253464.123, "type": "response.completed"}
```

7. **user_input**, **system_prompt**, **agent_output** - Recorded with `tracer.record_user_input(...)`, `record_system_prompt(...)` and `record_agent_output(...)`
```json
{"ts": 1760253460.001, "type": "user_input", "role": "user", "content": "...", "content_hash": "<sha256 hex>", "length": 42}
{"ts": 1760253460.002, "type": "system_prompt", "role": "system", "content": "...", "content_hash": "<sha256 hex>", "version": "v1.0", "length": 120}
{"ts": 1760253464.200, "type": "agent_output", "role": "assistant", "content": "...", "output_hash": "<sha256 hex>", "length": 80}
```
`user_input` and `system_prompt` events are inserted at the start of the list, so the list is not in time order. Use `ts` to order events.

---

## 🧪 Quick Test

1. Start the proxy with `PROXY_LOCAL_RISK=1` and `PROXY_PAYMENT_AUTHORIZATION_KEY` set (see [proxy/README.md](../../proxy/README.md) and [DEVELOPMENT.md](../DEVELOPMENT.md#3-environment-configuration)), and the example seller:
   ```bash
   uv run uvicorn --app-dir packages/x402-secure/examples seller_integration:app --port 8010
   ```
2. Run the buyer agent with the environment shown in [DEVELOPMENT.md](../DEVELOPMENT.md#4-run-development-server).
3. Take the `tid` from the proxy log line `[LOCAL] Trace created: tid=...`.
4. Query and view the trace:
   ```bash
   TID=<tid from the proxy log>

   # Complete trace
   curl -sS "http://localhost:8000/risk/trace/$TID" | python3 -m json.tool > /tmp/full_trace.json
   cat /tmp/full_trace.json

   # Events only
   python3 -c "import json; data=json.load(open('/tmp/full_trace.json')); print(json.dumps(data['agent_trace']['events'], indent=2))"

   # Or use the helper script
   uv run python scripts/view_trace.py $TID
   ```

---

## ✅ Complete Trace Context Data Flow

```
1️⃣  OpenAI Agent execution
    ├─ Turn 1: list_available_merchants
    ├─ Turn 2: prepare_payment
    └─ Collect all events (14 total)

2️⃣  store_agent_trace() upload
    ├─ POST /risk/trace (HTTP API)
    └─ Return tid

3️⃣  tid encoded into X-PAYMENT-SECURE
    └─ tracestate: base64({"tid": "..."})

4️⃣  Seller → Proxy → /risk/evaluate
    ├─ Proxy/Risk Engine extracts tid
    └─ Local mode logs a one-line summary; Trustline loads the stored trace by tid

5️⃣  Query via API (local mode only)
    └─ GET /risk/trace/{tid}
    └─ Return complete JSON
```

---

## 🎯 Verification Points

✅ task/parameters/environment fully recorded
✅ All tool calls are captured
✅ Tool results fully saved
✅ AI reasoning process recorded (gpt-5 series)
✅ tid associated with agent trace
✅ Queryable via API (local mode)
✅ Summarized in the local-mode proxy log

**Agent trace context is fully visible, queryable, and auditable!** 🎉
