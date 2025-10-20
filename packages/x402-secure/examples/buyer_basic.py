# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
import asyncio
import os

from dotenv import load_dotenv
from x402_secure_client import BuyerClient, BuyerConfig, setup_otel_from_env

load_dotenv()


async def main():
    # Initialize OpenTelemetry (console + OTLP by env)
    setup_otel_from_env()
    seller = os.getenv("SELLER_BASE_URL", "http://localhost:8010")
    gateway = os.getenv("AGENT_GATEWAY_URL", "http://localhost:8000")
    buyer = BuyerClient(
        BuyerConfig(
            seller_base_url=seller,
            agent_gateway_url=gateway,
            network=os.getenv("NETWORK", "base-sepolia"),
            buyer_private_key=os.getenv("BUYER_PRIVATE_KEY"),
        )
    )

    # TODO: When integrating with EIP-8004, pass did:eip8004:{chain_id}:{contract}:{token_id}
    sid = (await buyer.create_risk_session(app_id=None, device={"ua": "oss-buyer"}))["sid"]
    tid = await buyer.store_agent_trace(
        sid=sid,
        task="Get BTC price",
        params={"symbol": "BTC/USD"},
        environment={"network": os.getenv("NETWORK", "base-sepolia")},
    )

    res = await buyer.execute_with_tid(
        endpoint="/api/market-data",
        task="Get BTC price",
        params={"symbol": "BTC/USD"},
        sid=sid,
        tid=tid,
    )
    print(res)


if __name__ == "__main__":
    asyncio.run(main())
