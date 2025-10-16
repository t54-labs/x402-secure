# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from typing import Any, Dict, Optional

import httpx


class SellerClient:
    def __init__(self, proxy_base: str):
        if not proxy_base:
            raise ValueError("proxy_base required")
        base = proxy_base.rstrip("/")
        self.verify_url = f"{base}/verify"
        self.settle_url = f"{base}/settle"
        self.http = httpx.AsyncClient(timeout=15.0, follow_redirects=True)

    async def verify(
        self,
        payment_payload: Dict[str, Any],
        payment_requirements: Dict[str, Any],
        *,
        x_payment_b64: str,
        origin: str,
        x_payment_secure: str,
        risk_sid: str,
        x_ap2_evd: Optional[str] = None,
    ) -> Dict[str, Any]:
        headers = {
            "X-PAYMENT": x_payment_b64,
            "Origin": origin,
            "X-PAYMENT-SECURE": x_payment_secure,
            "X-RISK-SESSION": risk_sid,
        }
        if x_ap2_evd:
            headers["X-AP2-EVIDENCE"] = x_ap2_evd
        r = await self.http.post(
            self.verify_url,
            json={"x402Version": 1, "paymentPayload": payment_payload, "paymentRequirements": payment_requirements},
            headers=headers,
        )
        r.raise_for_status()
        if (r.headers.get("content-type") or "").split(";", 1)[0].strip().lower() != "application/json":
            raise httpx.HTTPError("invalid content-type from /x402/verify")
        return r.json()

    async def settle(
        self,
        payment_payload: Dict[str, Any],
        payment_requirements: Dict[str, Any],
        *,
        x_payment_b64: str,
        origin: str,
        x_payment_secure: str,
        risk_sid: str,
        x_ap2_evd: Optional[str] = None,
    ) -> Dict[str, Any]:
        headers = {
            "X-PAYMENT": x_payment_b64,
            "Origin": origin,
            "X-PAYMENT-SECURE": x_payment_secure,
            "X-RISK-SESSION": risk_sid,
        }
        if x_ap2_evd:
            headers["X-AP2-EVIDENCE"] = x_ap2_evd
        r = await self.http.post(
            self.settle_url,
            json={"x402Version": 1, "paymentPayload": payment_payload, "paymentRequirements": payment_requirements},
            headers=headers,
        )
        r.raise_for_status()
        if (r.headers.get("content-type") or "").split(";", 1)[0].strip().lower() != "application/json":
            raise httpx.HTTPError("invalid content-type from /x402/settle")
        return r.json()

    async def verify_then_settle(
        self,
        payment_payload: Dict[str, Any],
        payment_requirements: Dict[str, Any],
        *,
        x_payment_b64: str,
        origin: str,
        x_payment_secure: str,
        risk_sid: str,
        x_ap2_evd: Optional[str] = None,
    ) -> Dict[str, Any]:
        v = await self.verify(
            payment_payload,
            payment_requirements,
            x_payment_b64=x_payment_b64,
            origin=origin,
            x_payment_secure=x_payment_secure,
            risk_sid=risk_sid,
            x_ap2_evd=x_ap2_evd,
        )
        if not v.get("isValid"):
            raise RuntimeError(v.get("invalidReason") or "verification failed")
        return await self.settle(
            payment_payload,
            payment_requirements,
            x_payment_b64=x_payment_b64,
            origin=origin,
            x_payment_secure=x_payment_secure,
            risk_sid=risk_sid,
            x_ap2_evd=x_ap2_evd,
        )

