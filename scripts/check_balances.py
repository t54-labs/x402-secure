# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
"""
Check wallet balances on Base Sepolia testnet
"""

import asyncio
import os
import sys
from datetime import datetime

from dotenv import load_dotenv
from web3 import Web3

# Load environment variables
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

# Base Sepolia configuration
BASE_SEPOLIA_RPC = "https://sepolia.base.org"
USDC_CONTRACT_ADDRESS = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"

# USDC ABI (only necessary functions)
USDC_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function",
    },
]


async def check_balances():
    """Check buyer and seller balances"""

    print("""
╔══════════════════════════════════════════════════════════╗
║          Base Sepolia Wallet Balance Check               ║
╚══════════════════════════════════════════════════════════╝
    """)

    # Connect to Base Sepolia
    w3 = Web3(Web3.HTTPProvider(BASE_SEPOLIA_RPC))

    if not w3.is_connected():
        print("❌ Unable to connect to Base Sepolia RPC")
        return

    print(f"✅ Connected to Base Sepolia (Chain ID: {w3.eth.chain_id})")
    print(f"📅 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # USDC contract
    usdc_contract = w3.eth.contract(
        address=Web3.to_checksum_address(USDC_CONTRACT_ADDRESS), abi=USDC_ABI
    )

    # Get wallet addresses
    buyer_address = os.getenv("BUYER_ADDRESS")
    seller_address = os.getenv("SELLER_ADDRESS")

    if not buyer_address or not seller_address:
        print("\n❌ Wallet configuration not found")
        print("Please run first: python create_wallets.py")
        return

    print("\n" + "=" * 60)
    print("Wallet Balances:")
    print("=" * 60)

    # Check buyer balance
    print(f"\n💰 Buyer wallet: {buyer_address}")

    # ETH balance
    eth_balance_wei = w3.eth.get_balance(buyer_address)
    eth_balance = w3.from_wei(eth_balance_wei, "ether")
    print(f"   ETH:  {eth_balance:.6f} ETH")

    # USDC balance
    usdc_balance_raw = usdc_contract.functions.balanceOf(buyer_address).call()
    usdc_balance = usdc_balance_raw / 10**6  # USDC has 6 decimals
    print(f"   USDC: {usdc_balance:.2f} USDC")

    # Check seller balance
    print(f"\n💵 Seller wallet: {seller_address}")

    # ETH balance
    eth_balance_wei = w3.eth.get_balance(seller_address)
    eth_balance = w3.from_wei(eth_balance_wei, "ether")
    print(f"   ETH:  {eth_balance:.6f} ETH")

    # USDC balance
    usdc_balance_raw = usdc_contract.functions.balanceOf(seller_address).call()
    usdc_balance = usdc_balance_raw / 10**6  # USDC has 6 decimals
    print(f"   USDC: {usdc_balance:.2f} USDC")

    # Show block explorer links
    print("\n" + "=" * 60)
    print("Block Explorer Links:")
    print("=" * 60)
    print(f"\nBuyer: https://sepolia.basescan.org/address/{buyer_address}")
    print(f"Seller: https://sepolia.basescan.org/address/{seller_address}")
    print(f"USDC: https://sepolia.basescan.org/token/{USDC_CONTRACT_ADDRESS}")

    # Show Faucet links
    print("\n" + "=" * 60)
    print("Get Test Tokens:")
    print("=" * 60)

    print("\n🚰 Base Sepolia ETH:")
    print("  1. Coinbase: https://www.coinbase.com/faucets/base-sepolia-faucet")
    print("  2. Alchemy: https://sepoliafaucet.com/")

    print("\n💵 Base Sepolia USDC:")
    print("  1. Circle: https://faucet.circle.com/")
    print("  2. Select 'Base Sepolia' network")
    print(f"  3. Enter buyer address: {buyer_address}")

    # Check if ready for testing
    print("\n" + "=" * 60)
    print("Test Readiness Status:")
    print("=" * 60)

    buyer_eth = w3.eth.get_balance(buyer_address)
    buyer_usdc = usdc_contract.functions.balanceOf(buyer_address).call()

    if buyer_eth < w3.to_wei(0.001, "ether"):
        print("❌ Buyer ETH insufficient (need at least 0.001 ETH)")
    else:
        print("✅ Buyer ETH sufficient")

    if buyer_usdc < 10 * 10**6:  # 10 USDC
        print("❌ Buyer USDC insufficient (recommend at least 10 USDC)")
    else:
        print("✅ Buyer USDC sufficient")

    if buyer_eth >= w3.to_wei(0.001, "ether") and buyer_usdc >= 10 * 10**6:
        print("\n✅ Ready! You can run: python test_base_sepolia.py")
    else:
        print("\n⚠️  Please get test tokens first")


if __name__ == "__main__":
    asyncio.run(check_balances())
