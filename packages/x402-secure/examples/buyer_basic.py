# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
"""Basic buyer example demonstrating simple payment flow without AI agent."""

import asyncio
import os

from dotenv import load_dotenv
from x402_secure_client import BuyerClient, BuyerConfig, setup_otel_from_env
from x402_secure_client.headers import start_client_span

load_dotenv()


async def main():
    """Execute a basic buyer payment flow."""
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
        events=[],  # Empty array for basic example (no AI agent events)
        model_config={},  # Empty object for basic example (no LLM used)
        session_context={
            "session_id": sid,
            "agent_did": buyer.address,
            "sdk_version": "x402-agent/1.0.0",
            "origin": "cli",
        },
    )

    # Execute payment within an OpenTelemetry span context
    with start_client_span("buyer.execute_payment"):
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
