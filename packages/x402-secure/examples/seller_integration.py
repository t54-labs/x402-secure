# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
import os
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from x402_secure_client import SellerClient
from dotenv import load_dotenv


load_dotenv()
app = FastAPI(title="Seller Example (OSS)")

PROXY_BASE = os.getenv("PROXY_BASE", "http://localhost:8010/x402")
seller_sdk = SellerClient(PROXY_BASE)


@app.get("/api/market-data")
async def market_data(req: Request, symbol: str = "BTC/USD"):
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
    print("\n" + "="*80)
    print("📥 SELLER RECEIVED HEADERS")
    print("="*80)
    print(f"🌐 Origin: {origin}")
    print(f"🆔 X-RISK-SESSION: {sid}")
    print(f"🔒 X-PAYMENT-SECURE: {xps[:120] if xps else 'N/A'}...")
    print(f"💳 X-PAYMENT: {x_payment[:80] if x_payment else 'N/A'}...")
    if xps:
        parts = dict(p.split('=', 1) for p in xps.split(';') if '=' in p)
        print(f"\n📊 Trace Context:")
        print(f"   traceparent: {parts.get('tp', 'N/A')}")
        if 'ts' in parts:
            import base64 as b64, json as js, urllib.parse as up
            try:
                ts_decoded = b64.b64decode(up.unquote(parts['ts']))
                ts_data = js.loads(ts_decoded)
                print(f"   tracestate: {js.dumps(ts_data, indent=6)}")
            except:
                print(f"   tracestate: {parts['ts'][:60]}...")
    print("="*80 + "\n")
    
    if not x_payment or not xps or not sid or not origin:
        return JSONResponse({"x402Version": 1, "accepts": [pr], "error": "Payment required"}, status_code=402)

    import base64, json as _json
    try:
        payment_payload = _json.loads(base64.b64decode(x_payment))
    except Exception:
        return JSONResponse({"x402Version": 1, "accepts": [pr], "error": "Invalid X-PAYMENT"}, status_code=402)

    v = await seller_sdk.verify_then_settle(
        payment_payload,
        pr,
        x_payment_b64=x_payment,
        origin=origin,
        x_payment_secure=xps,
        risk_sid=sid,
    )

    import json
    payload_resp_b64 = base64.b64encode(json.dumps(v).encode()).decode()
    data = {"symbol": symbol, "price": 63500.12, "source": "oss-demo"}
    return JSONResponse(data, headers={"X-PAYMENT-RESPONSE": payload_resp_b64})
