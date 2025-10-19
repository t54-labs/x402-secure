# 📊 Complete Guide to Agent Trace Context Viewing

## 🎯 What Does Agent Trace Context Include?

Agent trace context is the complete execution record of an AI agent, including:

| Field | Description | Example |
|------|------|------|
| **task** | Task description | "Buy BTC price" |
| **parameters** | Task parameters | `{"symbol": "BTC/USD"}` |
| **environment** | Execution environment | `{"network": "base-sepolia", ...}` |
| **events** | Execution events | Tool calls, reasoning process, results, etc. |

---

## 🔍 Three Ways to View

### Method 1: Buyer-side Auto-Print (Before Upload)

**Location**: Terminal output when running buyer_agent_openai.py

**Output**:
```
================================================================================
📊 AGENT TRACE CONTEXT (before upload)
================================================================================
🎯 Task: Buy BTC price
📝 Parameters: {'symbol': 'BTC/USD'}
🌍 Environment: {'network': 'base-sepolia', 'seller_base_url': 'http://localhost:8010'}
📋 Events: 14 total

   Recent events:
   1. tool_call: prepare_payment
   2. tool_call: list_available_merchants
   3. tool_result: list_available_merchants
   4. tool_result: prepare_payment
   5. response.completed: N/A
================================================================================

✅ Agent trace uploaded, tid: 59db88a1-fda6-4d9c-9945-6d083f4a12a8
```

---

### Method 2: Query via API (Most Detailed) ⭐ Recommended

**Steps**:
1. Get tid from buyer output
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

### Method 3: Proxy/Risk Engine Logs (During Verification)

Auto-printed during /x402/verify → /risk/evaluate

**Proxy Local Mode** (PROXY_LOCAL_RISK=1):
```
📊 [PROXY LOCAL] Agent Trace Context for tid=59db88a1...
  Task: Buy BTC price
  Parameters: {'symbol': 'BTC/USD'}
  Environment: {'network': 'base-sepolia', ...}
  Events: 14 total
  
  Recent events:
    1. tool_call: prepare_payment
    2. tool_call: list_available_merchants
    ...
```

**Risk Engine Mode** (PROXY_LOCAL_RISK=0):
```
INFO:📊 [PROXY LOCAL] Agent Trace Context for tid=...
INFO:  Task: Buy BTC price
INFO:  Parameters: {'symbol': 'BTC/USD'}
INFO:  Environment: ...
INFO:  Events (14 total): [...]
```

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
  "args": {"k": {"query": "BTC/USD"}}
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

---

## 🧪 Quick Test

```bash
# 1. Ensure services are running
export BUYER_PRIVATE_KEY=<your_private_key>
./start_demo.sh

# 2. Run agent and save output
export OPENAI_API_KEY=sk-...
AGENT_GATEWAY_URL=http://localhost:8000 SELLER_BASE_URL=http://localhost:8010 \
uv run python packages/x402-secure/examples/buyer_agent_openai.py > /tmp/agent_run.txt 2>&1

# 3. View agent trace summary
grep -A 15 "AGENT TRACE CONTEXT" /tmp/agent_run.txt

# 4. Extract tid
TID=$(grep "Agent trace uploaded, tid:" /tmp/agent_run.txt | head -1 | sed 's/.*tid: //')
echo "TID: $TID"

# 5. Query complete trace
curl -sS "http://localhost:8000/risk/trace/$TID" | python3 -m json.tool > /tmp/full_trace.json
cat /tmp/full_trace.json

# 6. View events only
python3 -c "import json; data=json.load(open('/tmp/full_trace.json')); print(json.dumps(data['agent_trace']['events'], indent=2))"
```

---

## ✅ Complete Trace Context Data Flow

```
1️⃣  OpenAI Agent execution
    ├─ Turn 1: list_available_merchants
    ├─ Turn 2: prepare_payment
    └─ Collect all events (14 total)

2️⃣  store_agent_trace() upload
    ├─ Print agent trace summary (before upload)
    ├─ POST /risk/trace (HTTP API)
    └─ Return tid

3️⃣  tid encoded into X-PAYMENT-SECURE
    └─ tracestate: base64({"tid": "..."})

4️⃣  Seller → Proxy → /risk/evaluate
    ├─ Proxy/Risk Engine extracts tid
    ├─ Query stored agent_trace
    └─ Print complete trace context

5️⃣  Query via API
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
✅ Queryable via API  
✅ Auto-printed during verification  

**Agent trace context is fully visible, queryable, and auditable!** 🎉
