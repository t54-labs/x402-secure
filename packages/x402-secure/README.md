# x402-secure

Open-source x402 client SDKs for buyers and sellers that integrate with a unified gateway for risk session/trace and a seller proxy for verify/settle.

- Buyer SDK: create risk session/trace via `AGENT_GATEWAY_URL`, build `X-PAYMENT-SECURE` from OpenTelemetry context.
- Seller SDK: call proxy `/x402/verify` and `/x402/settle` with `verify_then_settle(...)` or XRPL `verify_then_settle_xrpl(...)`.
- XRPL helpers: encode/decode v2 `PAYMENT-SIGNATURE` payloads and build XRPL 402 responses.

Fail-fast principles:
- Non-200 responses raise immediately (`HTTPStatusError`).
- Invalid or missing `traceparent` context raises.
- Header size limit enforced (`X-PAYMENT-SECURE` ≤ 4096 bytes).

## Install

```
pip install x402-secure
```

## Quickstart

```python
import os, asyncio
from x402_secure_client import BuyerConfig, BuyerClient

async def main():
    buyer = BuyerClient(BuyerConfig(
        seller_base_url=os.getenv("SELLER_BASE_URL", "http://localhost:8010"),
        agent_gateway_url=os.getenv("AGENT_GATEWAY_URL", "http://localhost:8000"),
        network=os.getenv("NETWORK", "base-sepolia"),
        buyer_private_key=os.getenv("BUYER_PRIVATE_KEY"),
    ))

    # Create risk session + trace (single client!)
    # agent_did: currently wallet address, future: EIP-8004 DID (did:eip8004:chain:contract:tokenId)
    sid = (await buyer.create_risk_session(app_id=None, device={"ua": "oss-example"}))['sid']
    tid = await buyer.store_agent_trace(
        sid=sid,
        task="Buy BTC price",
        params={"symbol": "BTC/USD"},
        environment={"network": os.getenv("NETWORK", "base-sepolia")},
    )

    # Execute payment with trace ID
    res = await buyer.execute_paid_request(
        endpoint="/api/market-data",
        task="Buy BTC price",
        params={"symbol": "BTC/USD"},
        sid=sid,
        tid=tid,
    )
    print(res)

asyncio.run(main())
```

See `examples/` for runnable scripts.

## XRPL

XRPL x402 payments use `x402Version=2` and the `PAYMENT-SIGNATURE` header. The
signature payload is JSON containing the accepted XRPL payment requirements and
`payload.signedTxBlob`.

```python
from x402_secure_client import (
    SellerClient,
    build_xrpl_payment_payload,
    encode_xrpl_payment_signature,
)

seller = SellerClient("https://x402-proxy.t54.ai")

requirements = {
    "scheme": "exact",
    "network": "xrpl:1",
    "asset": "XRP",
    "payTo": "rMerchantAddress",
    "amount": "1000",
    "maxTimeoutSeconds": 600,
    "extra": {"sourceTag": 804681468, "invoiceId": "INV-123"},
}
payload = build_xrpl_payment_payload(requirements, signed_tx_blob="...")
payment_signature = encode_xrpl_payment_signature(payload)

result = await seller.verify_then_settle_xrpl(
    payload,
    requirements,
    payment_signature_b64=payment_signature,
    origin="https://seller.example",
    x_payment_secure="w3c.v1;tp=...",
    risk_sid="925ca6ee-aa4b-4508-955b-10b1c02c69bb",
)
```

## License

Apache-2.0
