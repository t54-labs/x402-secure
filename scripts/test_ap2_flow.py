#!/usr/bin/env python3
# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
"""
Test complete AP2 flow: Buyer Agent -> Proxy -> Seller
"""

import asyncio
import base64
import hashlib
import json
from datetime import datetime

import httpx

# Configuration
PROXY_URL = "http://localhost:8000"  # Unified gateway (proxy with PROXY_LOCAL_RISK=1)


async def test_buyer_agent_flow():
    """Simulate Buyer Agent complete flow"""
    print("\n" + "=" * 60)
    print("🤖 Simulating Buyer Agent Flow")
    print("=" * 60)

    async with httpx.AsyncClient() as client:
        # ========== Step 1: Create session, upload trace context ==========
        print("\n📤 Step 1: Upload trace context")

        # This is a sample trace context, recording the Agent's decision process
        # TODO: When integrating with EIP-8004, use did:eip8004:{chain_id}:{contract}:{token_id}
        trace_context = {
            "agent_did": "test-buyer-agent-001",
            "timestamp": datetime.utcnow().isoformat(),
            "task": {"type": "purchase", "description": "User requests purchase of real-time BTC/USD market data"},
            "decision_process": [
                {
                    "step": 1,
                    "action": "search_providers",
                    "input": "BTC/USD market data providers",
                    "output": ["provider_A", "provider_B", "provider_C"],
                    "reasoning": "Found 3 possible data providers",
                },
                {
                    "step": 2,
                    "action": "compare_prices",
                    "input": {
                        "provider_A": {"price": 2.0, "quality": "high"},
                        "provider_B": {"price": 1.0, "quality": "medium"},
                        "provider_C": {"price": 1.5, "quality": "high"},
                    },
                    "output": "provider_B",
                    "reasoning": "Selected provider_B, best price and quality meets requirements",
                },
                {
                    "step": 3,
                    "action": "verify_merchant",
                    "input": "provider_B",
                    "output": {"verified": True, "reputation": 4.5},
                    "reasoning": "Merchant has good reputation, can proceed with transaction",
                },
            ],
            "final_decision": {
                "action": "purchase",
                "merchant": "http://localhost:8000",
                "resource": "http://localhost:8000/api/market-data?symbol=BTC/USD",
                "price": "1.0",
                "currency": "USDC",
            }
        }

        session_request = {
            "trace_context": trace_context,
            "agent_did": "test-buyer-agent-001",
            "metadata": {"sdk_version": "1.0.0", "environment": "test"},
        }

        print(f"Trace context summary:")
        print(f"  - Agent DID: {trace_context['agent_did']}")
        print(f"  - Task: {trace_context['task']['description']}")
        print(f"  - Decision steps: {len(trace_context['decision_process'])}")
        print(f"  - Final decision: Purchase from {trace_context['final_decision']['merchant']}")

        response = await client.post(f"{PROXY_URL}/ap2/session", json=session_request)

        if response.status_code != 200:
            print(f"❌ Error: {response.status_code} - {response.text}")
            return

        session_response = response.json()
        session_url = session_response["session_url"]
        digest = session_response["digest"]

        print(f"\n✅ Session created successfully!")
        print(f"  - Session URL: {session_url}")
        print(f"  - Digest: {digest}")

        # ========== Step 2: Request PaymentMandate ==========
        print("\n📤 Step 2: Request PaymentMandate VC")

        # Prepare payment binding information
        nonce = f"nonce-{int(datetime.utcnow().timestamp())}"

        mandate_request = {
            "binding": {
                "merchant_id": "did:web:localhost:8000",
                "resource_url": "http://localhost:8000/api/market-data?symbol=BTC/USD",
                "amount": "1.0",
                "asset": "USDC",
                "nonce": nonce,
                "ttl_sec": 300,
            },
            "agent_signal": {"agent_presence": True, "modality": "HUMAN_NOT_PRESENT"},
            "risk_payload": {
                "trace_digest": digest,
                "func_stack_hashes": [
                    "hash_search_providers",
                    "hash_compare_prices",
                    "hash_verify_merchant",
                ],
                "session_url": session_url,
                "agent_fingerprint": "test-buyer-agent-001",
                "confidence_score": 0.85,
            },
            "subject": "did:agent:test-buyer-001",
        }

        print(f"PaymentMandate request:")
        print(f"  - Merchant: {mandate_request['binding']['merchant_id']}")
        print(f"  - Resource: {mandate_request['binding']['resource_url']}")
        print(
            f"  - Amount: {mandate_request['binding']['amount']} {mandate_request['binding']['asset']}"
        )
        print(f"  - Session URL: {mandate_request['risk_payload']['session_url']}")

        response = await client.post(f"{PROXY_URL}/ap2/payment/issue", json=mandate_request)

        if response.status_code != 200:
            print(f"❌ Error: {response.status_code} - {response.text}")
            return

        mandate_response = response.json()
        payment_uid = mandate_response["payment_uid"]
        approved = mandate_response["approved"]
        risk_score = mandate_response["risk_score"]
        reason = mandate_response["reason"]

        print(f"\n✅ Risk approval received!")
        print(f"  - Payment UID: {payment_uid}")
        print(f"  - Approved: {approved}")
        print(f"  - Risk Score: {risk_score}")
        print(f"  - Reason: {reason}")

        # ========== Step 3: Build x402 payment request ==========
        print("\n📤 Step 3: Build x402 payment request (with ap2Ref)")

        # This is the x402 payment request that Buyer Agent would send to Seller
        # In real scenarios, this would be sent via X-PAYMENT header
        x402_payment = {
            "x402Version": 1,
            "scheme": "exact",
            "network": "base-sepolia",
            "payload": {
                "authorization": {
                    "from": "0x809829fc420EdCDaacBc9830C30c93859FE955bf",
                    "to": "0x01704e0610e9FBd71F5e96188b690b54857D484D",
                    "value": "1000000",  # 1 USDC (6 decimals)
                    "validAfter": str(int(datetime.utcnow().timestamp())),
                    "validBefore": str(int(datetime.utcnow().timestamp()) + 300),
                    "nonce": nonce,
                },
                "ap2Ref": {
                    "url": session_url,
                    "digest": digest,
                    "payment_uid": payment_uid,
                },
            },
        }

        print(f"x402 payment request built:")
        print(f"  - ap2Ref.url: {x402_payment['payload']['ap2Ref']['url']}")
        print(f"  - ap2Ref.digest: {x402_payment['payload']['ap2Ref']['digest']}")
        print(f"  - Payment UID: {payment_uid}")

        return {
            "session_url": session_url,
            "digest": digest,
            "payment_uid": payment_uid,
            "x402_payment": x402_payment,
            "nonce": nonce,
        }


async def test_seller_verify(buyer_result):
    """Simulate Seller calling /verify to validate payment"""
    print("\n" + "=" * 60)
    print("🏪 Simulating Seller Verification Flow")
    print("=" * 60)

    async with httpx.AsyncClient() as client:
        # After receiving Buyer's payment request, Seller calls /verify for risk control validation
        verify_request = {
            "x402Version": 1,
            "paymentPayload": buyer_result["x402_payment"],
            "paymentRequirements": {
                "scheme": "exact",
                "network": "base-sepolia",
                "max_amount_required": "1.0",
                "resource": "http://localhost:8000/api/market-data?symbol=BTC/USD",
                "description": "Real-time BTC/USD market data",
                "pay_to": "0x01704e0610e9FBd71F5e96188b690b54857D484D",
                "max_timeout_seconds": 60,
                "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",  # USDC on Base Sepolia
                "mime_type": "application/json",
                "extra": {
                    "requires": {
                        "ap2": {
                            "attestation_types": ["PaymentMandate"],
                            "mode": "reference",
                            "allowed_issuers": ["did:web:tradar.t54.ai"],
                            "min_fields": ["trace_digest", "func_stack_hashes"],
                        }
                    }
                },
            },
        }

        print("📤 Calling /verify endpoint...")
        print(f"  - Verify ap2Ref")
        print(f"  - Check trace context")
        print(f"  - Verify function stack hashes")
        print(f"  - Evaluate risk")

        response = await client.post(f"{PROXY_URL}/verify", json=verify_request)

        print(f"\nVerification response: {response.status_code}")
        result = response.json()

        if result["is_valid"]:
            print("✅ Payment verification passed!")
            print(f"  - Payer: {result['payer']}")
            if result.get("decision_token"):
                print(f"  - Decision Token: {result['decision_token'][:80]}...")
            if result.get("details"):
                print(f"  - Details: {json.dumps(result['details'], indent=4)}")
        else:
            print("❌ Payment verification failed!")
            print(f"  - Reason: {result.get('invalid_reason', 'Unknown')}")
            if result.get("details"):
                print(f"  - Details: {json.dumps(result['details'], indent=4)}")


async def test_internal_api(session_url):
    """Test internal API to get trace context"""
    print("\n" + "=" * 60)
    print("🔍 Testing Internal API")
    print("=" * 60)

    session_id = session_url.split("/ap2/session/")[-1]

    async with httpx.AsyncClient() as client:
        print(f"📤 Getting session data: {session_id}")

        response = await client.get(
            f"{PROXY_URL}/ap2/session/{session_id}",
            headers={"Authorization": "Bearer test-internal-key"},
        )

        if response.status_code == 200:
            data = response.json()
            print("✅ Successfully retrieved trace context!")
            print(f"  - Digest: {data['digest']}")
            print(f"  - Context keys: {list(data['trace_context'].keys())}")
        else:
            print(f"❌ Error: {response.status_code} - {response.text}")


async def main():
    """Run complete test flow"""
    print(f"""
{"=" * 60}
🧪 AP2 Complete Flow Test
{"=" * 60}
Facilitator URL: {PROXY_URL}

Flow:
1. Buyer Agent uploads trace context
2. Buyer Agent requests PaymentMandate
3. Seller verifies payment request
4. Internal API retrieves detailed information
{"=" * 60}
    """)

    # Ensure proxy is running
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{PROXY_URL}/health")
            if response.status_code != 200:
                print("❌ Proxy not running, please start first: PROXY_LOCAL_RISK=1 python3 run_facilitator_proxy.py")
                return
    except:
        print("❌ Unable to connect to Proxy, please start first: PROXY_LOCAL_RISK=1 python3 run_facilitator_proxy.py")
        return

    # Execute test flow
    buyer_result = await test_buyer_agent_flow()

    if buyer_result:
        await asyncio.sleep(1)  # Brief delay
        await test_seller_verify(buyer_result)

        await asyncio.sleep(1)
        await test_internal_api(buyer_result["session_url"])

    print(f"\n{'=' * 60}")
    print("✅ Test completed!")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    asyncio.run(main())
