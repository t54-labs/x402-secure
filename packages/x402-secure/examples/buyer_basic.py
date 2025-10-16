# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
import os
import asyncio
from urllib.parse import urlparse
from dotenv import load_dotenv
from x402_secure_client import BuyerClient, BuyerConfig, RiskClient, build_payment_secure_header, start_client_span, setup_otel_from_env

load_dotenv()

async def main():
    # Initialize OpenTelemetry (console + OTLP by env)
    setup_otel_from_env()
    seller = os.getenv("SELLER_BASE_URL", "http://localhost:8010")
    gateway = os.getenv("AGENT_GATEWAY_URL", "http://localhost:8000")
    buyer = BuyerClient(BuyerConfig(
        seller_base_url=seller,
        agent_gateway_url=gateway,
        network=os.getenv("NETWORK", "base-sepolia"),
        buyer_private_key=os.getenv("BUYER_PRIVATE_KEY"),
    ))

    rc = RiskClient(gateway)
    # TODO: When integrating with EIP-8004, pass did:eip8004:{chain_id}:{contract}:{token_id}
    sid = (await rc.create_session(agent_did=buyer.address, app_id=None, device={"ua": "oss-buyer"}))['sid']
    tid = (await rc.create_trace(sid=sid, agent_trace={"task": "Get BTC price", "parameters": {"symbol": "BTC/USD"}}))['tid']

    with start_client_span("buyer.payment"):
        xps = build_payment_secure_header(agent_trace_context={"tid": tid})
        res = await buyer.execute_paid_request(
            endpoint="/api/market-data",
            task="Get BTC price",
            params={"symbol": "BTC/USD"},
            risk_sid=sid,
            extra_headers=xps,
        )
    print(res)


if __name__ == "__main__":
    asyncio.run(main())

