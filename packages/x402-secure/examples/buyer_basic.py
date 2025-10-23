# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
"""Basic buyer example demonstrating simple payment flow without AI agent."""

import asyncio
import os

import httpx
from dotenv import load_dotenv
from x402_secure_client import BuyerClient, BuyerConfig, setup_otel_from_env

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

    try:
        res = await buyer.execute_paid_request(
            endpoint="/api/market-data",
            task="Get BTC price",
            params={"symbol": "BTC/USD"},
            sid=sid,
            tid=tid,
        )
        print("✅ Payment successful!")
        print(res)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            error_detail = e.response.json().get("detail", "Unknown risk denial")
            print(f"\n❌ Payment denied by risk engine: {error_detail}")
            # Access additional information from response headers
            decision_id = e.response.headers.get("X-Risk-Decision-ID")
            risk_decision = e.response.headers.get("X-Risk-Decision")
            ttl_seconds = e.response.headers.get("X-Risk-TTL-Seconds")
            if decision_id:
                print(f"   Decision ID: {decision_id}")
            if risk_decision:
                print(f"   Risk Decision: {risk_decision}")
            if ttl_seconds:
                print(f"   TTL: {ttl_seconds}s")
        else:
            print(f"\n❌ HTTP error {e.response.status_code}: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
