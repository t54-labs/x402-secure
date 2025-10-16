# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from typing import Any, Dict, Optional

import httpx


class RiskClient:
    def __init__(self, base_url: str):
        if not base_url:
            raise ValueError("base_url required for RiskClient")
        self.base_url = base_url.rstrip("/")
        self.http = httpx.AsyncClient(timeout=15.0)

    async def create_session(self, *, agent_did: str, app_id: Optional[str], device: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Create a risk session with agent identification.
        
        Args:
            agent_did: Agent decentralized identifier. Currently accepts wallet address (0x...).
                      TODO: Support EIP-8004 DID format (did:eip8004:{chain_id}:{contract}:{token_id})
                      when integrating with on-chain agent identity registry (ERC-721 based).
            app_id: Optional application identifier
            device: Device information dict
        
        Returns:
            Dict containing 'sid' (session ID) and other session metadata
        """
        r = await self.http.post(f"{self.base_url}/risk/session", json={"agent_did": agent_did, "app_id": app_id, "device": device})
        r.raise_for_status()
        if (r.headers.get("content-type") or "").split(";", 1)[0].strip().lower() != "application/json":
            raise httpx.HTTPError("invalid content-type from /risk/session")
        return r.json()

    async def create_trace(
        self,
        *,
        sid: str,
        fingerprint: Optional[Dict[str, Any]] = None,
        telemetry: Optional[Dict[str, Any]] = None,
        agent_trace: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        r = await self.http.post(
            f"{self.base_url}/risk/trace",
            json={"sid": sid, "fingerprint": fingerprint, "telemetry": telemetry, "agent_trace": agent_trace},
        )
        r.raise_for_status()
        if (r.headers.get("content-type") or "").split(";", 1)[0].strip().lower() != "application/json":
            raise httpx.HTTPError("invalid content-type from /risk/trace")
        return r.json()

