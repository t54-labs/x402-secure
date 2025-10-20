# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
"""Seller integration example demonstrating payment verification and settlement."""

import base64
import json
import os
import urllib.parse

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from x402_secure_client import SellerClient

load_dotenv()
app = FastAPI(title="Seller Example (OSS)")

GATEWAY = os.getenv("AGENT_GATEWAY_URL", "http://localhost:8000")
seller_sdk = SellerClient(GATEWAY)


@app.get("/api/market-data")
async def market_data(req: Request, symbol: str = "BTC/USD"):
    """Handle market data requests with X-PAYMENT verification."""
    pr = {
        "scheme": "exact",
        "network": os.getenv("NETWORK", "base-sepolia"),
        "maxAmountRequired": "100000",
        "resource": str(req.url),
        "description": "BTC market data",
        "mimeType": "application/json",
        "payTo": os.getenv("MERCHANT_PAYTO", "0x0987654321098765432109876543210987654321"),
        "asset": os.getenv("USDC_ADDRESS", "0x036CbD53842c5426634e7929541eC2318f3dCF7e"),
        "maxTimeoutSeconds": 30,
        "extra": {"name": "USDC", "version": "2"},
    }

    x_payment = req.headers.get("X-PAYMENT")
    xps = req.headers.get("X-PAYMENT-SECURE")
    sid = req.headers.get("X-RISK-SESSION")
    origin = req.headers.get("Origin")

    # Log received headers
    print("\n" + "=" * 80)
    print("📥 SELLER RECEIVED HEADERS")
    print("=" * 80)
    print(f"🌐 Origin: {origin}")
    print(f"🆔 X-RISK-SESSION: {sid}")
    print(f"🔒 X-PAYMENT-SECURE: {xps[:120] if xps else 'N/A'}...")
    print(f"💳 X-PAYMENT: {x_payment[:80] if x_payment else 'N/A'}...")
    if xps:
        parts = dict(p.split("=", 1) for p in xps.split(";") if "=" in p)
        print("\n📊 Trace Context:")
        print(f"   traceparent: {parts.get('tp', 'N/A')}")
        if "ts" in parts:
            try:
                ts_decoded = base64.b64decode(urllib.parse.unquote(parts["ts"]))
                ts_data = json.loads(ts_decoded)
                print(f"   tracestate: {json.dumps(ts_data, indent=6)}")
            except Exception:
                print(f"   tracestate: {parts['ts'][:60]}...")
    print("=" * 80 + "\n")

    if not x_payment or not xps or not sid or not origin:
        return JSONResponse(
            {"x402Version": 1, "accepts": [pr], "error": "Payment required"}, status_code=402
        )

    try:
        payment_payload = json.loads(base64.b64decode(x_payment))
    except Exception:
        return JSONResponse(
            {"x402Version": 1, "accepts": [pr], "error": "Invalid X-PAYMENT"}, status_code=402
        )

    v = await seller_sdk.verify_then_settle(
        payment_payload,
        pr,
        x_payment_b64=x_payment,
        origin=origin,
        x_payment_secure=xps,
        risk_sid=sid,
    )

    payload_resp_b64 = base64.b64encode(json.dumps(v).encode()).decode()

    print(
        f"✅ Settlement success={v.get('success')} "
        f"network={v.get('network')} "
        f"tx={v.get('transaction')} "
        f"payer={v.get('payer')}"
    )

    data = {
        "symbol": symbol,
        "price": 113500.12,
        "source": "oss-demo",
        "settlement": v,
    }
    return JSONResponse(data, headers={"X-PAYMENT-RESPONSE": payload_resp_b64})
