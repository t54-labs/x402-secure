# x402-secure

Open-source x402 client SDKs for buyers and sellers that integrate with a unified gateway for risk session/trace and a seller proxy for verify/settle.

- Buyer SDK: create risk session/trace via `AGENT_GATEWAY_URL`, build `X-PAYMENT-SECURE` from OpenTelemetry context.
- Seller SDK: call proxy `/x402/verify` and `/x402/settle` with a simple `verify_then_settle(...)` wrapper.

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
from x402_secure_client import BuyerConfig, BuyerClient, RiskClient
from x402_secure_client import build_payment_secure_header, start_client_span

async def main():
    buyer = BuyerClient(BuyerConfig(
        seller_base_url=os.getenv("SELLER_BASE_URL", "http://localhost:8010"),
        agent_gateway_url=os.getenv("AGENT_GATEWAY_URL", "http://localhost:8000"),
        network=os.getenv("NETWORK", "base-sepolia"),
        buyer_private_key=os.getenv("BUYER_PRIVATE_KEY"),
    ))

    # Create risk session + trace
    rc = RiskClient(os.getenv("AGENT_GATEWAY_URL", "http://localhost:8000"))
    # agent_did: currently wallet address, future: EIP-8004 DID (did:eip8004:chain:contract:tokenId)
    sid = (await rc.create_session(agent_did=buyer.address, app_id=None, device={"ua": "oss-example"}))['sid']
    tid = (await rc.create_trace(sid=sid, agent_trace={"task": "buy", "parameters": {"symbol": "BTC/USD"}}))['tid']

    # Build secure header from OTEL context
    with start_client_span("buyer.payment"):
        xps = build_payment_secure_header(agent_trace_context={"tid": tid})
        res = await buyer.execute_paid_request(
            endpoint="/api/market-data",
            task="Buy BTC price",
            params={"symbol": "BTC/USD"},
            risk_sid=sid,
            extra_headers=xps,
        )
    print(res)

asyncio.run(main())
```

See `examples/` for runnable scripts.

## License

Apache-2.0

