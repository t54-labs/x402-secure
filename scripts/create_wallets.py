# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
"""
Create and manage x402 test wallets
Generate buyer and seller wallets, and save to .env file
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json
from datetime import datetime

from dotenv import load_dotenv, set_key
from eth_account import Account

# Load existing environment variables
load_dotenv()


def create_wallets():
    """Create new buyer and seller wallets"""

    print("""
╔══════════════════════════════════════════════════════════╗
║            x402 Test Wallet Generator                    ║
║                                                          ║
║  Create buyer and seller wallets for Base Sepolia        ║
╚══════════════════════════════════════════════════════════╝
    """)

    # Check if wallets already exist
    existing_buyer_key = os.getenv("BUYER_PRIVATE_KEY")
    existing_seller_key = os.getenv("SELLER_PRIVATE_KEY")

    if existing_buyer_key or existing_seller_key:
        print("\n⚠️  Existing wallet configuration detected:")

        if existing_buyer_key:
            try:
                buyer_account = Account.from_key(existing_buyer_key)
                print(f"  Buyer address: {buyer_account.address}")
            except Exception:
                print("  Invalid buyer private key")

        if existing_seller_key:
            try:
                seller_account = Account.from_key(existing_seller_key)
                print(f"  Seller address: {seller_account.address}")
            except Exception:
                print("  Invalid seller private key")

        response = input("\nCreate new wallets? (y/n): ").lower()
        if response != "y":
            print("Keeping existing wallet configuration")
            return

    # Create new wallets
    print("\n🔑 Creating new wallets...")

    # Create buyer wallet
    buyer_account = Account.create()
    print("\nBuyer wallet:")
    print(f"  Address: {buyer_account.address}")
    print(f"  Private key: {buyer_account.key.hex()}")

    # Create seller wallet
    seller_account = Account.create()
    print("\nSeller wallet:")
    print(f"  Address: {seller_account.address}")
    print(f"  Private key: {seller_account.key.hex()}")

    # Save to .env file
    env_file = ".env"

    # If .env doesn't exist, copy from env.example
    if not os.path.exists(env_file):
        if os.path.exists("env.example"):
            with open("env.example", "r") as f:
                example_content = f.read()
            with open(env_file, "w") as f:
                f.write(example_content)
            print("\n✅ Created .env file")

    # Update environment variables
    set_key(env_file, "BUYER_PRIVATE_KEY", buyer_account.key.hex())
    set_key(env_file, "BUYER_ADDRESS", buyer_account.address)
    set_key(env_file, "SELLER_PRIVATE_KEY", seller_account.key.hex())
    set_key(env_file, "SELLER_ADDRESS", seller_account.address)

    print(f"\n✅ Wallet information saved to {env_file}")

    # Save wallet info to JSON file (backup)
    wallets_info = {
        "created_at": datetime.now().isoformat(),
        "network": "base-sepolia",
        "buyer": {"address": buyer_account.address, "private_key": buyer_account.key.hex()},
        "seller": {"address": seller_account.address, "private_key": seller_account.key.hex()},
    }

    with open("wallets.json", "w") as f:
        json.dump(wallets_info, f, indent=2)

    print("✅ Wallet backup saved to wallets.json")

    # Show next steps
    print("\n" + "=" * 60)
    print("Next steps:")
    print("=" * 60)

    print("\n1. Get Base Sepolia ETH (for gas):")
    print("   Buyer: https://www.coinbase.com/faucets/base-sepolia-faucet")
    print("   Seller: https://www.coinbase.com/faucets/base-sepolia-faucet")

    print("\n2. Get Base Sepolia USDC:")
    print("   Visit: https://faucet.circle.com/")
    print("   Select Base Sepolia network")
    print(f"   Fund buyer address: {buyer_account.address}")

    print("\n3. Check balances:")
    print(f"   Buyer: https://sepolia.basescan.org/address/{buyer_account.address}")
    print(f"   Seller: https://sepolia.basescan.org/address/{seller_account.address}")

    print("\n💡 Tips:")
    print("  - Buyer needs ETH (gas) and USDC (payment)")
    print("  - Seller only needs address to receive payment, no ETH needed")
    print("  - Keep wallets.json file as backup")

    # Show wallet QR codes (optional)
    print("\n📱 Wallet address QR codes:")
    print("  You can use QR code generator to create address QR codes for mobile scanning")
    print(f"  Buyer address: {buyer_account.address}")
    print(f"  Seller address: {seller_account.address}")


def show_existing_wallets():
    """Show existing wallet information"""

    print("\n📋 Current wallet configuration:")
    print("=" * 60)

    # Read from .env
    buyer_key = os.getenv("BUYER_PRIVATE_KEY")
    seller_key = os.getenv("SELLER_PRIVATE_KEY")

    if buyer_key:
        try:
            buyer_account = Account.from_key(buyer_key)
            print("\nBuyer wallet:")
            print(f"  Address: {buyer_account.address}")
            print(f"  Private key: {buyer_key}")
        except Exception:
            print("\nBuyer wallet: Invalid private key")
    else:
        print("\nBuyer wallet: Not configured")

    if seller_key:
        try:
            seller_account = Account.from_key(seller_key)
            print("\nSeller wallet:")
            print(f"  Address: {seller_account.address}")
            print(f"  Private key: {seller_key}")
        except Exception:
            print("\nSeller wallet: Invalid private key")
    else:
        # If no seller private key, try to read from address
        seller_address = os.getenv("SELLER_ADDRESS")
        if seller_address:
            print("\nSeller wallet:")
            print(f"  Address: {seller_address}")
            print("  Private key: Not configured (receive only)")
        else:
            print("\nSeller wallet: Not configured")

    # Read from wallets.json (if exists)
    if os.path.exists("wallets.json"):
        print("\n📄 From wallets.json backup:")
        with open("wallets.json", "r") as f:
            wallets = json.load(f)
        print(f"  Created at: {wallets.get('created_at', 'Unknown')}")
        print(f"  Network: {wallets.get('network', 'Unknown')}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--show":
        show_existing_wallets()
    else:
        create_wallets()
