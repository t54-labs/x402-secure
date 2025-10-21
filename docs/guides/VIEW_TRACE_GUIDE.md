# 📊 Complete Guide to Viewing Trace Context

## 🎯 Overview

When running the demo (using `PROXY_LOCAL_RISK=1`), all risk data is stored in the proxy's memory. You can view this data in multiple ways.

---

## Method 1: View Proxy Logs in Real-Time (Recommended)

### View in Proxy Terminal

If you ran the proxy in Terminal 1, you can directly see all input/output in that terminal.

### Or View Log Files

If you started with a script (running in background), you can monitor logs in real-time:

```bash
tail -f logs/proxy.log
```

### What You'll See

#### 1. POST /risk/session

```
================================================================================
📥 [RISK] POST /risk/session - RAW Input Payload (JSON):
================================================================================
{
  "agent_did": "0xYourAgentAddress...",
  "wallet_address": "0xYourAgentAddress...",
  "agent_endpoint": "https://agent.example.com",
  "app_id": null,
  "device": {
    "ua": "oss-agent"
  }
}
================================================================================

✅ [RISK] Session created: sid=84e35e6e-8260-42fb-a811-5c25089efbf8
   Expires at: 2025-10-13T12:34:56.789Z
```

#### 2. POST /risk/trace

**Raw JSON Payload:**
```
================================================================================
📥 [RISK] POST /risk/trace - RAW Input Payload (JSON):
================================================================================
{
  "sid": "84e35e6e-8260-42fb-a811-5c25089efbf8",
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
    "model_config": {
      "provider": "openai",
      "model": "gpt-4o-mini",
      "tools_enabled": ["list_available_merchants", "prepare_payment"],
      "reasoning_enabled": true
    },
    "session_context": {
      "session_id": "84e35e6e-8260-42fb-a811-5c25089efbf8",
      "request_id": "a1b2c3d4-5678-90ab-cdef-1234567890ab",
      "agent_id": "0xYourAddress",
      "sdk_version": "x402-agent/1.0.0",
      "origin": "cli",
      "client_ip_hash": "6ca13d52fdfb9021..."
    },
    "events": [
      {
        "type": "user_input",
        "timestamp": "2025-10-13T12:34:56.789Z",
        "content": "Use list_available_merchants..."
      },
      // ... more events
    ]
  }
}
================================================================================
```

**Formatted Summary (Easy-to-Read Version):**
```
================================================================================
📊 [RISK] /risk/trace - Formatted Summary:
================================================================================
  sid: 84e35e6e-8260-42fb-a811-5c25089efbf8
  fingerprint: None
  telemetry: None

  📊 Agent Trace:
    task: Buy BTC price
    parameters: {'symbol': 'BTC/USD'}
    environment: {'network': 'base-sepolia', 'seller_base_url': 'http://localhost:8010'}
    model_config:
      provider: openai
      model: gpt-4o-mini
      tools_enabled: ['list_available_merchants', 'prepare_payment']
    session_context:
      session_id: 84e35e6e-8260-42fb-a811-5c25089efbf8
      request_id: a1b2c3d4-5678-90ab-cdef-1234567890ab
      agent_id: 0xYourAgentAddress...
    events: 15 total
      - user_input: 2
      - tool_call: 2
      - tool_result: 2
      - agent_output: 2
      - reasoning: 4
================================================================================

✅ [RISK] Trace created: tid=dde4fbf8-1448-43c0-a556-e6e98353416b
   Linked to sid=84e35e6e-8260-42fb-a811-5c25089efbf8
```

#### 3. POST /risk/evaluate

**Raw JSON Payload:**
```
================================================================================
📥 [RISK] POST /risk/evaluate - RAW Input Payload (JSON):
================================================================================
{
  "sid": "84e35e6e-8260-42fb-a811-5c25089efbf8",
  "tid": "dde4fbf8-1448-43c0-a556-e6e98353416b",
  "trace_context": {
    "tp": "00-b5ccb78e8b2640d785213de685ac2644-82516a311157fe39-01",
    "ts": "eyJ0aWQiOiAiZGRlNGZiZjgtMTQ0OC00M2MwLWE1NTYtZTZlOTgzNTM0MTZiIn0="
  },
  "mandate": null,
  "payment": null
}
================================================================================
```

**Formatted Summary:**
```
================================================================================
📊 [RISK] /risk/evaluate - Formatted Summary:
================================================================================
  sid: 84e35e6e-8260-42fb-a811-5c25089efbf8
  tid: dde4fbf8-1448-43c0-a556-e6e98353416b
  trace_context:
    tp (traceparent): 00-b5ccb78e8b2640d785213de685ac2644-82516a311157fe39-01
    ts (tracestate): eyJ0aWQiOiAiZGRlNGZiZjgtMTQ0OC00M2MwLWE1NTYtZTZlOTgzNTM0MTZiIn0=...
  mandate: None
  payment: None
================================================================================

================================================================================
📊 [PROXY LOCAL] Agent Trace Context for tid=dde4fbf8-1448-43c0-a556-e6e98353416b:
================================================================================
  Task: Buy BTC price
  Parameters: {'symbol': 'BTC/USD'}
  Environment: {'network': 'base-sepolia', 'seller_base_url': 'http://localhost:8010'}
  Model Config:
    Provider: openai
    Model: gpt-4o-mini
    Tools: list_available_merchants, prepare_payment
  Session Context:
    Session ID: 84e35e6e-8260-42fb-a811-5c25089efbf8
    Request ID: a1b2c3d4-5678-90ab-cdef-1234567890ab
    Agent ID: 0xYourAgentAddress...
    Client IP (hashed): 6ca13d52fdfb9021...
  Events: 15 total

  👤 User Inputs (2 items):
    1. Use list_available_merchants to find merchants that provide BTC/USD price data.
    2. Now use prepare_payment to validate merchant 'price-demo-1' and create a payment...

  🤖 Agent Outputs (2 items):
    1. Found merchant: price-demo-1 - Demo Price API
    2. Payment plan prepared for price-demo-1: endpoint=/api/market-data, symbol=BTC/...

  Recent events:
    1. tool_result: prepare_payment
    2. agent_output: N/A
    3. reasoning: N/A
    4. reasoning: N/A
    5. agent_output: N/A
================================================================================

✅ [RISK] Evaluation complete: decision=allow
   decision_id=f9876543-210a-bcde-f012-34567890abcd
```

---

## Method 2: Query via API for Stored Trace

### View Individual Trace

Using the tool script I created:

```bash
# Get tid from buyer output, then query
python scripts/view_trace.py dde4fbf8-1448-43c0-a556-e6e98353416b
```

### Or Use curl Directly

```bash
curl http://localhost:8000/risk/trace/dde4fbf8-1448-43c0-a556-e6e98353416b | jq
```

Output example:

```json
{
  "sid": "84e35e6e-8260-42fb-a811-5c25089efbf8",
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
    "model_config": {
      "provider": "openai",
      "model": "gpt-4o-mini",
      "tools_enabled": ["list_available_merchants", "prepare_payment"],
      "reasoning_enabled": true
    },
    "session_context": {
      "session_id": "84e35e6e-8260-42fb-a811-5c25089efbf8",
      "request_id": "a1b2c3d4-5678-90ab-cdef-1234567890ab",
      "agent_id": "0xYourAddress",
      "sdk_version": "x402-agent/1.0.0",
      "origin": "cli",
      "client_ip_hash": "6ca13d52fdfb9021..."
    },
    "events": [
      {
        "type": "user_input",
        "timestamp": "2025-10-13T12:34:56.789Z",
        "content": "Use list_available_merchants..."
      },
      {
        "type": "tool_call",
        "timestamp": "2025-10-13T12:34:57.123Z",
        "name": "list_available_merchants",
        "arguments": {"query": "BTC/USD"}
      },
      // ... more events
    ]
  }
}
```

---

## Method 3: Use grep to Filter Specific Information

### View only session creation

```bash
grep -A 5 "POST /risk/session" logs/proxy.log
```

### View only trace creation

```bash
grep -A 20 "POST /risk/trace" logs/proxy.log
```

### View only evaluate input

```bash
grep -A 10 "POST /risk/evaluate" logs/proxy.log
```

### View only complete Agent Trace Context

```bash
grep -A 40 "Agent Trace Context for tid=" logs/proxy.log
```

---

## 📝 Quick Debug Workflow

### Run demo once, then view all information:

```bash
# 1. Get tid from buyer output
# Example: Agent Trace ID (tid): dde4fbf8-1448-43c0-a556-e6e98353416b

# 2. View complete trace
python scripts/view_trace.py dde4fbf8-1448-43c0-a556-e6e98353416b

# 3. View all risk operations in proxy logs
grep "\[RISK\]" logs/proxy.log

# 4. View specific evaluate details
grep -A 50 "Agent Trace Context" logs/proxy.log
```

---

## 🎯 Key Points

1. ✅ **Data stored in memory**: TTLCache, default TTL 900 seconds (15 minutes)
2. ✅ **Automatic log output**: All risk endpoint input/output is printed
3. ✅ **API query**: Use `GET /risk/trace/{tid}` to query anytime
4. ✅ **Complete trace context**: Includes task, parameters, events, model_config, session_context

---

## 🔍 Data Structure Description

### Session Storage

```python
{
  "agent_id": "0x...",
  "app_id": None,
  "device": {"ua": "oss-agent"},
  "expires_at": "2025-10-13T12:34:56Z"
}
```

### Trace Storage

```python
{
  "sid": "session-uuid",
  "fingerprint": {...},    # Optional
  "telemetry": {...},      # Optional
  "agent_trace": {
    "task": "...",
    "parameters": {...},
    "environment": {...},
    "model_config": {...},
    "session_context": {...},
    "events": [...]
  }
}
```

---

## 💡 Tips

- If you don't see logs, check if `PROXY_LOCAL_RISK=1` is set
- Logs are in **proxy terminal** (Terminal 1) or `logs/proxy.log`
- Use `tail -f logs/proxy.log` for real-time monitoring
- Trace data is in memory, proxy restart will lose it

---

## 🎊 Complete Example

After running agent demo, you should see:

```bash
# Terminal 1 (Proxy) output:
📥 [RISK] POST /risk/session - Input Payload:
  agent_did: 0x123...
  wallet_address: 0x123...
  agent_endpoint: https://agent.example.com
✅ [RISK] Session created: sid=abc-123

📥 [RISK] POST /risk/trace - Input Payload:
  📊 Agent Trace:
    task: Buy BTC price
    events: 15 total
✅ [RISK] Trace created: tid=xyz-789

📥 [RISK] POST /risk/evaluate - Input Payload:
  sid: abc-123
  tid: xyz-789
📊 [PROXY LOCAL] Agent Trace Context for tid=xyz-789:
  Task: Buy BTC price
  👤 User Inputs (2 items):
    1. Use list_available_merchants...
  🤖 Agent Outputs (2 items):
    1. Found merchant: price-demo-1...
✅ [RISK] Evaluation complete: decision=allow
```

Perfect! 🎯
