# 📋 Trace Payload Format Description

## Overview

When you run the demo (using `PROXY_LOCAL_RISK=1`), the proxy prints **two formats** of data:

1. **RAW Input Payload (JSON)** - Original JSON format, exactly as sent to the API
2. **Formatted Summary** - Formatted easy-to-read version

---

## POST /risk/session

### Raw JSON Payload

```json
{
  "agent_did": "0xedE5Ff927607e8E83490fd07436c09A30c81FD09",
  "wallet_address": "0xedE5Ff927607e8E83490fd07436c09A30c81FD09",
  "agent_endpoint": "https://agent.example.com",
  "app_id": null,
  "device": {
    "ua": "oss-agent"
  }
}
```

This is the **raw data format** sent from the buyer to the proxy.

**Required fields:**
- `agent_did`: Agent DID (current phase: often same as wallet; future: did:eip8004:...)
- `wallet_address`: EVM wallet address (0x...)

**Optional fields:**
- `agent_endpoint`: Agent callback/base URL
- `app_id`: Application identifier
- `device`: Device information

---

## POST /risk/trace

### Raw JSON Payload (Complete Structure)

```json
{
  "sid": "925ca6ee-aa4b-4508-955b-10b1c02c69bb",
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
      "model": "gpt-5-mini",
      "tools_enabled": [
        "list_available_merchants",
        "prepare_payment"
      ],
      "reasoning_enabled": true
    },
    "session_context": {
      "session_id": "925ca6ee-aa4b-4508-955b-10b1c02c69bb",
      "request_id": "269ae30b-40a2-4697-a50e-b4142780f81f",
      "agent_id": "0xedE5Ff927607e8E83490fd07436c09A30c81FD09",
      "sdk_version": "x402-agent/1.0.0",
      "origin": "cli",
      "client_ip_hash": "6ca13d52fdfb90217de8c73c68e3c10a..."
    },
    "events": [
      {
        "type": "user_input",
        "timestamp": "2025-10-13T00:43:35.123Z",
        "content": "Use list_available_merchants to find merchants that provide BTC/USD price data."
      },
      {
        "type": "response.created",
        "timestamp": "2025-10-13T00:43:36.456Z"
      },
      {
        "type": "reasoning_summary",
        "timestamp": "2025-10-13T00:43:37.789Z",
        "summary": "I'll search for merchants..."
      },
      {
        "type": "function_call",
        "timestamp": "2025-10-13T00:43:38.012Z",
        "name": "list_available_merchants",
        "arguments": {
          "query": "BTC/USD"
        }
      },
      {
        "type": "tool_call",
        "timestamp": "2025-10-13T00:43:38.345Z",
        "name": "list_available_merchants",
        "arguments": {
          "query": "BTC/USD"
        }
      },
      {
        "type": "tool_result",
        "timestamp": "2025-10-13T00:43:38.678Z",
        "name": "list_available_merchants",
        "result": {
          "merchants": [
            {
              "id": "price-demo-1",
              "name": "Demo Price API",
              "endpoint": "/api/market-data"
            }
          ]
        }
      },
      {
        "type": "response.completed",
        "timestamp": "2025-10-13T00:43:39.901Z"
      },
      {
        "type": "agent_output",
        "timestamp": "2025-10-13T00:43:40.234Z",
        "content": "Found merchant: price-demo-1 - Demo Price API"
      },
      {
        "type": "user_input",
        "timestamp": "2025-10-13T00:43:41.567Z",
        "content": "Now use prepare_payment to validate merchant 'price-demo-1'..."
      }
      // ... more events
    ]
  }
}
```

### Formatted Summary (What You Saw Before)

```
================================================================================
📊 [RISK] /risk/trace - Formatted Summary:
================================================================================
  sid: 925ca6ee-aa4b-4508-955b-10b1c02c69bb
  fingerprint: None
  telemetry: None

  📊 Agent Trace:
    task: Buy BTC price
    parameters: {'symbol': 'BTC/USD'}
    environment: {'network': 'base-sepolia', 'seller_base_url': 'http://localhost:8010'}
    model_config:
      provider: openai
      model: gpt-5-mini
      tools_enabled: ['list_available_merchants', 'prepare_payment']
    session_context:
      session_id: 925ca6ee-aa4b-4508-955b-10b1c02c69bb
      request_id: 269ae30b-40a2-4697-a50e-b4142780f81f
      agent_id: 0xedE5Ff927607e8E83490fd07436c09A30c81FD09
    events: 18 total
      - user_input: 2
      - response.created: 2
      - reasoning_summary: 2
      - function_call: 2
      - tool_call: 3
      - tool_result: 3
      - response.completed: 2
      - agent_output: 2
================================================================================
```

---

## POST /risk/evaluate

### Raw JSON Payload

```json
{
  "sid": "925ca6ee-aa4b-4508-955b-10b1c02c69bb",
  "tid": "af88271b-e93d-4998-bc15-2f130d437262",
  "trace_context": {
    "tp": "00-b5ccb78e8b2640d785213de685ac2644-82516a311157fe39-01",
    "ts": "eyJ0aWQiOiAiYWY4ODI3MWItZTkzZC00OTk4LWJjMTUtMmYxMzBkNDM3MjYyIn0="
  },
  "mandate": null,
  "payment": null
}
```

---

## 🎯 Key Points

### 1. Both Output Types Are Displayed

Now the proxy prints:
- ✅ **RAW Input Payload (JSON)** - Complete original JSON
- ✅ **Formatted Summary** - Human-readable formatted version

### 2. Raw JSON = Actual Sent Data

`RAW Input Payload (JSON)` is the **real data** sent from the buyer SDK to the proxy, completely identical.

### 3. How to View

**Restart the proxy** then run the buyer demo, you'll see both formats in the **proxy terminal**.

```bash
# 1. Stop old proxy
pkill -f run_facilitator_proxy

# 2. Restart proxy (will load new code)
PROXY_LOCAL_RISK=1 \
PROXY_PORT=8000 \
PROXY_UPSTREAM_VERIFY_URL=https://x402.org/facilitator/verify \
PROXY_UPSTREAM_SETTLE_URL=https://x402.org/facilitator/settle \
uv run python run_facilitator_proxy.py

# 3. Run buyer in another terminal
AGENT_GATEWAY_URL=http://localhost:8000 \
SELLER_BASE_URL=http://localhost:8010 \
uv run python packages/x402-secure/examples/buyer_agent_openai.py
```

---

## 📊 Events Array Explained

The `agent_trace.events` array contains the complete agent execution process:

### Event Types

| Type | Description |
|------|------|
| `user_input` | User input query or instruction |
| `response.created` | OpenAI starts responding |
| `reasoning_summary` | Reasoning process summary (reasoning models only) |
| `function_call` | OpenAI calls function (old format) |
| `tool_call` | Tool call (new format) |
| `tool_result` | Tool execution result |
| `response.completed` | OpenAI completes response |
| `agent_output` | Agent output content |

### Complete Event Examples

```json
{
  "type": "tool_call",
  "timestamp": "2025-10-13T00:43:38.345Z",
  "name": "list_available_merchants",
  "arguments": {
    "query": "BTC/USD"
  }
}
```

```json
{
  "type": "tool_result",
  "timestamp": "2025-10-13T00:43:38.678Z",
  "name": "list_available_merchants",
  "result": {
    "merchants": [...]
  }
}
```

---

## 💡 Summary

- ✅ **Formatted output you saw before** = Easy-to-read summary version
- ✅ **Now added RAW JSON** = Real API payload
- ✅ **Both formats are printed** = Can see both raw data and readable version
- ✅ **Need to restart proxy** = To load new logging code

Perfect! Now you can see the complete raw JSON payload! 🎉
