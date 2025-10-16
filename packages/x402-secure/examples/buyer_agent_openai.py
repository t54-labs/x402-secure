# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
"""
Buyer agent example demonstrating the complete OSS SDK flow:
- Create risk session first
- Register Python tools via tracer.tool decorator
- Let tracer.process_stream capture events and execute tools
- Store agent trace at /risk/trace to get tid
- Execute payment with tid in X-PAYMENT-SECURE

Env:
- AGENT_GATEWAY_URL (default http://localhost:8000)
- SELLER_BASE_URL (default http://localhost:8010)
- NETWORK (default base-sepolia)
- BUYER_PRIVATE_KEY (required)
- OPENAI_API_KEY (required)
- OPENAI_MODEL (default gpt-5-mini)
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict

from x402_secure_client import (
    BuyerClient,
    BuyerConfig,
    RiskClient,
    OpenAITraceCollector,
    store_agent_trace,
    execute_payment_with_tid,
    setup_otel_from_env,
)
from dotenv import load_dotenv


def _usdc_for_network(network: str) -> str:
    return (
        "0x036CbD53842c5426634e7929541eC2318f3dCF7e" if network == "base-sepolia" else "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    )


async def main() -> None:
    # Load environment variables from .env (repo root)
    load_dotenv()
    if not os.getenv("BUYER_PRIVATE_KEY"):
        raise RuntimeError("BUYER_PRIVATE_KEY is required for signing X-PAYMENT")

    # Initialize OpenTelemetry (console + OTLP by env)
    setup_otel_from_env()
    gateway = os.getenv("AGENT_GATEWAY_URL", "http://localhost:8000")
    seller_base = os.getenv("SELLER_BASE_URL", "http://localhost:8010")
    network = os.getenv("NETWORK", "base-sepolia")

    cfg = BuyerConfig(
        seller_base_url=seller_base,
        agent_gateway_url=gateway,
        network=network,
        buyer_private_key=os.getenv("BUYER_PRIVATE_KEY"),
    )
    buyer = BuyerClient(cfg)

    # Create session early
    rc = RiskClient(gateway)
    # create_session input payload: agent_did (currently wallet address, future: EIP-8004 DID)
    # TODO: When integrating with EIP-8004, pass did:eip8004:{chain_id}:{contract}:{token_id}
    sid = (await rc.create_session(agent_did=buyer.address, app_id=None, device={"ua": "oss-agent"}))['sid']

    # Tracer + tools
    tracer = OpenAITraceCollector()
    
    # Set model configuration for trace context
    import uuid
    import hashlib
    model_name = os.getenv("OPENAI_MODEL", "gpt-5-mini")
    tracer.set_model_config(
        provider="openai",
        model=model_name,
        tools_enabled=["list_available_merchants", "prepare_payment"],
        reasoning_enabled=True,
    )
    
    # Build session context for risk evaluation
    request_id = str(uuid.uuid4())
    session_context = {
        "session_id": sid,  # Reuse risk session id
        "request_id": request_id,
        "agent_did": buyer.address,  # TODO: Support EIP-8004 DID format
        "sdk_version": "x402-agent/1.0.0",
        "origin": os.getenv("AGENT_ORIGIN", "cli"),
    }
    # Add client IP hash if available (from env or placeholder)
    client_ip = os.getenv("CLIENT_IP", "127.0.0.1")
    if client_ip:
        session_context["client_ip_hash"] = hashlib.sha256(client_ip.encode()).hexdigest()
    
    from pydantic import BaseModel  # type: ignore
    from openai import pydantic_function_tool, OpenAI  # type: ignore

    class ListMerchantsArgs(BaseModel):
        query: str

    class PreparePaymentArgs(BaseModel):
        merchant_id: str
        symbol: str
        max_amount_atomic: int = 100000

    tools_def = [
        pydantic_function_tool(ListMerchantsArgs, name="list_available_merchants", description="List merchants"),
        pydantic_function_tool(PreparePaymentArgs, name="prepare_payment", description="Prepare payment plan"),
    ]

    @tracer.tool
    def list_available_merchants(query: str) -> Dict[str, Any]:
        pr = {
            "scheme": "exact",
            "network": network,
            "maxAmountRequired": "100000",
            "resource": f"{seller_base}/api/market-data",
            "description": "BTC market data",
            "mimeType": "application/json",
            "payTo": os.getenv("MERCHANT_PAYTO", "0x0987654321098765432109876543210987654321"),
            "asset": _usdc_for_network(network),
            "maxTimeoutSeconds": 30,
            "extra": {"name": "USDC", "version": "2", "ap2": {"requireTrace": True}},
        }
        merchant = {"id": "price-demo-1", "name": "Demo Price API", "seller_base_url": seller_base, "endpoint": "/api/market-data", "notes": "x402 + AP2 supported", "accepts": [pr]}
        return {"merchants": [merchant]}

    @tracer.tool
    def prepare_payment(merchant_id: str, symbol: str, max_amount_atomic: int) -> Dict[str, Any]:
        merchants = list_available_merchants(symbol)["merchants"]
        m = next((x for x in merchants if x["id"] == merchant_id), None)
        if not m:
            return {"error": True, "message": "unknown merchant"}
        pr = dict(m["accepts"][0])
        pr["maxAmountRequired"] = str(max_amount_atomic)
        return {
            "merchant_id": merchant_id,
            "seller_base_url": m["seller_base_url"],
            "endpoint": m["endpoint"],
            "params": {"symbol": symbol},
            "payment_info": {"max_amount_atomic": max_amount_atomic, "network": pr["network"]},
            "payment_requirements": pr,
        }

    # Multi-turn agent conversation (like buyer_proxy_demo.py)
    # Turn 1: List merchants
    user_input_turn1 = "Use list_available_merchants to find merchants that provide BTC/USD price data."
    messages = [{
        "role": "user",
        "content": user_input_turn1
    }]
    
    # Record user input for risk evaluation
    tracer.record_user_input(user_input_turn1)
    
    # Optional: Record system prompt if you have one
    # system_prompt = "You are a helpful payment assistant..."
    # tracer.record_system_prompt(system_prompt, version="v1.0")
    
    print(f"🤖 Turn 1: Discovering merchants...")
    
    client = OpenAI()
    with client.responses.stream(
        model=os.getenv("OPENAI_MODEL", "gpt-5-mini"),  
        input=messages,
        tools=tools_def,
        reasoning={"effort": "low", "summary": "auto"},
    ) as stream:
        result1 = await tracer.process_stream(
            stream=stream,
            tools={
                "list_available_merchants": list_available_merchants,
                "prepare_payment": prepare_payment,
            },
        )
    
    merchants_result = result1["tool_results"].get("list_available_merchants")
    if not merchants_result or not merchants_result.get("merchants"):
        raise RuntimeError("❌ No merchants found")
    
    print(f"✅ Found {len(merchants_result['merchants'])} merchant(s)\n")
    
    # Turn 2: Prepare payment with the discovered merchant
    first_merchant = merchants_result["merchants"][0]
    assistant_response_turn1 = f"Found merchant: {first_merchant['id']} - {first_merchant['name']}"
    user_input_turn2 = f"Now use prepare_payment to validate merchant '{first_merchant['id']}' and create a payment plan for BTC/USD within 100000 atomic units."
    
    messages.append({
        "role": "assistant",
        "content": assistant_response_turn1
    })
    messages.append({
        "role": "user",
        "content": user_input_turn2
    })
    
    # Record agent intermediate output and next user input
    tracer.record_agent_output(assistant_response_turn1)
    tracer.record_user_input(user_input_turn2)
    
    print(f"🤖 Turn 2: Preparing payment plan...")
    
    with client.responses.stream(
        model=os.getenv("OPENAI_MODEL", "gpt-5-mini"),
        input=messages,
        tools=tools_def,
        reasoning={"effort": "low", "summary": "auto"},
    ) as stream:
        result2 = await tracer.process_stream(
            stream=stream,
            tools={
                "list_available_merchants": list_available_merchants,
                "prepare_payment": prepare_payment,
            },
        )
    
    # Extract payment plan from second turn
    plan = result2["tool_results"].get("prepare_payment")
    
    if not plan:
        print(f"❌ Agent did not prepare a payment plan. Tool results: {result2.get('tool_results')}")
        raise RuntimeError("payment plan not prepared")
    
    if plan.get("error"):
        print(f"❌ Payment preparation failed: {plan}")
        raise RuntimeError("payment plan not prepared")
    
    print(f"✅ Payment plan ready: {plan.get('merchant_id')}\n")
    
    # Record final agent output (successful plan preparation)
    final_output = f"Payment plan prepared for {plan.get('merchant_id')}: endpoint={plan.get('endpoint')}, symbol={plan.get('params', {}).get('symbol')}, max_amount={plan.get('payment_info', {}).get('max_amount_atomic')} atomic units"
    tracer.record_agent_output(final_output)

    # Store agent trace, execute payment
    tid = await store_agent_trace(
        risk=rc,
        sid=sid,
        task="Buy BTC price",
        params=plan.get("params") or {"symbol": "BTC/USD"},
        environment={"network": network, "seller_base_url": seller_base},
        events=tracer.events,
        model_config=tracer.model_config,
        session_context=session_context,
    )
    result = await execute_payment_with_tid(
        buyer=buyer,
        endpoint=plan["endpoint"],
        task="Buy BTC price",
        params=plan.get("params") or {"symbol": "BTC/USD"},
        sid=sid,
        tid=tid,
    )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
