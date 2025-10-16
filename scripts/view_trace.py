#!/usr/bin/env python3
# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
"""
View locally stored trace context

Usage:
  python scripts/view_trace.py <tid>
  
Or view all recent sessions/traces:
  python scripts/view_trace.py --list
"""

import asyncio
import json
import sys
import httpx


async def view_trace(tid: str, gateway_url: str = "http://localhost:8060"):
    """View a single trace"""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{gateway_url}/risk/trace/{tid}")
            if resp.status_code == 200:
                trace = resp.json()
                print("\n" + "="*80)
                print(f"📊 Trace Context for tid={tid}")
                print("="*80)
                print(json.dumps(trace, indent=2, ensure_ascii=False))
                print("="*80 + "\n")
                
                # Parse and display key information
                agent_trace = trace.get("agent_trace", {})
                if agent_trace:
                    print(f"📝 Task: {agent_trace.get('task')}")
                    print(f"📋 Parameters: {agent_trace.get('parameters')}")
                    
                    events = agent_trace.get('events', [])
                    print(f"\n📊 Event Statistics: {len(events)} total events")
                    
                    event_types = {}
                    for e in events:
                        etype = e.get('type', 'unknown')
                        event_types[etype] = event_types.get(etype, 0) + 1
                    
                    for etype, count in event_types.items():
                        print(f"  - {etype}: {count}")
                    
                    # Display user inputs
                    user_inputs = [e for e in events if e.get('type') == 'user_input']
                    if user_inputs:
                        print(f"\n👤 User Inputs:")
                        for i, evt in enumerate(user_inputs, 1):
                            print(f"  {i}. {evt.get('content', '')}")
                    
                    # Display tool calls
                    tool_calls = [e for e in events if e.get('type') == 'tool_call']
                    if tool_calls:
                        print(f"\n🔧 Tool Calls:")
                        for i, evt in enumerate(tool_calls, 1):
                            print(f"  {i}. {evt.get('name')} - {evt.get('arguments', {})}")
            else:
                print(f"❌ Error: {resp.status_code} - {resp.text}")
        except Exception as e:
            print(f"❌ Connection failed: {e}")


async def list_info(gateway_url: str = "http://localhost:8060"):
    """Display proxy information and health check"""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{gateway_url}/health")
            if resp.status_code == 200:
                health = resp.json()
                print("\n" + "="*80)
                print("🏥 Proxy Health Status")
                print("="*80)
                print(json.dumps(health, indent=2))
                print("="*80 + "\n")
        except Exception as e:
            print(f"❌ Proxy not running or connection failed: {e}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    gateway_url = "http://localhost:8060"
    
    if sys.argv[1] == "--list":
        asyncio.run(list_info(gateway_url))
    else:
        tid = sys.argv[1]
        asyncio.run(view_trace(tid, gateway_url))


if __name__ == "__main__":
    main()

