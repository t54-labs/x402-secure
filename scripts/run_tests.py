#!/usr/bin/env python3
# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
"""
Run test suite locally with proper environment setup.
"""
import os
import sys
import subprocess
from pathlib import Path


def setup_test_env():
    """Set up test environment variables."""
    env = os.environ.copy()
    env.update({
        "PROXY_LOCAL_RISK": "1",
        "UPSTREAM_FACILITATOR_BASE_URL": "https://test.example.com",
        "AGENT_GATEWAY_URL": "http://localhost:8000",
        "BUYER_SIGNING_KEY": "0x" + "a" * 64,
        "BUYER_ADDRESS": "0x" + "b" * 40,
        "SELLER_MERCHANT_NAME": "Test Merchant",
        "SELLER_MERCHANT_DOMAIN": "https://test.example.com"
    })
    return env


def run_command(cmd: list[str], env: dict) -> int:
    """Run a command with the given environment."""
    print(f"\n🚀 Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, env=env)
    return result.returncode


def main():
    """Run the test suite."""
    # Change to project root
    project_root = Path(__file__).parent.parent
    os.chdir(project_root)
    
    # Set up environment
    env = setup_test_env()
    
    # Check if pytest is installed
    if subprocess.run(["python", "-m", "pytest", "--version"], 
                     capture_output=True).returncode != 0:
        print("❌ pytest not installed. Installing test dependencies...")
        run_command(["uv", "pip", "install", "--system", 
                    "pytest", "pytest-asyncio", "pytest-cov", "pytest-mock"], env)
    
    # Install project in development mode
    print("\n📦 Installing project packages...")
    for package in [".", "./proxy", "./packages/x402-secure"]:
        run_command(["uv", "pip", "install", "--system", "-e", package], env)
    
    # Run tests based on arguments
    if len(sys.argv) > 1:
        # Run specific test files or markers
        cmd = ["python", "-m", "pytest"] + sys.argv[1:]
    else:
        # Run all tests with coverage
        cmd = ["python", "-m", "pytest", "tests/", "-v", "--cov", "--cov-report=term"]
    
    exit_code = run_command(cmd, env)
    
    if exit_code == 0:
        print("\n✅ All tests passed!")
    else:
        print(f"\n❌ Tests failed with exit code {exit_code}")
    
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
