# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx


def _now() -> int:
    return int(time.time())


def _normalize_pr_keys(pr: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(pr)
    out["payTo"] = pr.get("payTo") or pr.get("pay_to")
    out["maxAmountRequired"] = pr.get("maxAmountRequired") or pr.get("max_amount_required")
    out["resource"] = pr.get("resource")
    out["network"] = pr.get("network")
    out["asset"] = pr.get("asset")
    return out


@dataclass
class BuyerConfig:
    seller_base_url: str
    agent_gateway_url: str
    network: str = "base-sepolia"
    buyer_private_key: Optional[str] = None


class BuyerClient:
    def __init__(self, cfg: BuyerConfig):
        if not cfg.agent_gateway_url:
            raise ValueError("agent_gateway_url is required")
        if not cfg.seller_base_url:
            raise ValueError("seller_base_url is required")
        self.cfg = cfg
        self.http = httpx.AsyncClient(timeout=15.0)
        if cfg.buyer_private_key:
            from eth_account import Account

            self.address = Account.from_key(cfg.buyer_private_key).address
        else:
            self.address = os.getenv("BUYER_ADDRESS") or ("0x" + os.urandom(20).hex())

    async def _first_request_402(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        r = await self.http.get(url, params=params)
        if r.status_code != 402:
            raise RuntimeError(f"Expected 402 Payment Required, got {r.status_code}")
        ctype = (r.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            raise RuntimeError("seller preflight content-type must be application/json")
        body = r.json()
        accepts = body.get("accepts")
        if not accepts or not isinstance(accepts, list):
            raise RuntimeError("seller preflight missing 'accepts'")
        return accepts[0]

    async def execute_paid_request(
        self,
        endpoint: str,
        *,
        task: str,
        params: Optional[Dict[str, Any]] = None,
        risk_sid: str,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        params = params or {}
        url = f"{self.cfg.seller_base_url.rstrip('/')}{endpoint}"
        pr = await self._first_request_402(url, params)
        pr = _normalize_pr_keys(pr)

        if not self.cfg.buyer_private_key:
            raise RuntimeError("BUYER_PRIVATE_KEY required for signing X-PAYMENT")

        # Build signed X-PAYMENT (EIP-3009) using x402
        from eth_account import Account
        from x402 import exact
        from x402.types import PaymentRequirements as X402PR

        pr_model = X402PR(**pr)
        header = exact.prepare_payment_header(self.address, 1, pr_model)
        auth = header["payload"]["authorization"]
        if isinstance(auth.get("nonce"), (bytes, bytearray)):
            auth["nonce"] = auth["nonce"].hex()
        acct = Account.from_key(self.cfg.buyer_private_key)
        encoded = exact.sign_payment_header(acct, pr_model, header)
        payload = exact.decode_payment(encoded)

        origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        headers: Dict[str, str] = {
            "X-PAYMENT": encoded,
            "Origin": origin,
            "X-RISK-SESSION": risk_sid,
        }
        if extra_headers:
            headers.update(extra_headers)
        if "X-PAYMENT-SECURE" not in headers:
            raise RuntimeError("X-PAYMENT-SECURE missing in extra_headers")

        final = await self.http.get(url, params=params, headers=headers)
        final.raise_for_status()
        return final.json()

