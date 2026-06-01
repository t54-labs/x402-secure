# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from typing import Any, Dict, Optional

import httpx


class SellerClient:
    def __init__(self, gateway_base_url: str):
        """
        Initialize SellerClient with the agent gateway base URL.

        Args:
            gateway_base_url: Base URL of the agent gateway.
        """
        if not gateway_base_url:
            raise ValueError("gateway_base_url required")
        base = gateway_base_url.rstrip("/")
        proxy_base = f"{base}/x402"
        self.verify_url = f"{proxy_base}/verify"
        self.settle_url = f"{proxy_base}/settle"
        self.http = httpx.AsyncClient(timeout=15.0, follow_redirects=True)

    async def _post_payment(
        self,
        url: str,
        payment_payload: Dict[str, Any],
        payment_requirements: Dict[str, Any],
        *,
        x402_version: int,
        payment_header_name: str,
        payment_header_value: str,
        origin: Optional[str],
        x_payment_secure: str,
        risk_sid: str,
        x_ap2_evd: Optional[str] = None,
    ) -> Dict[str, Any]:
        headers = {
            payment_header_name: payment_header_value,
            "X-PAYMENT-SECURE": x_payment_secure,
            "X-RISK-SESSION": risk_sid,
        }
        if origin:
            headers["Origin"] = origin
        if x_ap2_evd:
            headers["X-AP2-EVIDENCE"] = x_ap2_evd

        r = await self.http.post(
            url,
            json={
                "x402Version": x402_version,
                "paymentPayload": payment_payload,
                "paymentRequirements": payment_requirements,
            },
            headers=headers,
        )
        r.raise_for_status()
        if (r.headers.get("content-type") or "").split(";", 1)[
            0
        ].strip().lower() != "application/json":
            raise httpx.HTTPError(f"invalid content-type from {url}")
        return r.json()

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
        return await self._post_payment(
            self.verify_url,
            payment_payload,
            payment_requirements,
            x402_version=1,
            payment_header_name="X-PAYMENT",
            payment_header_value=x_payment_b64,
            origin=origin,
            x_payment_secure=x_payment_secure,
            risk_sid=risk_sid,
            x_ap2_evd=x_ap2_evd,
        )

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
        return await self._post_payment(
            self.settle_url,
            payment_payload,
            payment_requirements,
            x402_version=1,
            payment_header_name="X-PAYMENT",
            payment_header_value=x_payment_b64,
            origin=origin,
            x_payment_secure=x_payment_secure,
            risk_sid=risk_sid,
            x_ap2_evd=x_ap2_evd,
        )

    async def verify_xrpl(
        self,
        payment_payload: Dict[str, Any],
        payment_requirements: Dict[str, Any],
        *,
        payment_signature_b64: str,
        origin: Optional[str],
        x_payment_secure: str,
        risk_sid: str,
        x_ap2_evd: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self._post_payment(
            self.verify_url,
            payment_payload,
            payment_requirements,
            x402_version=2,
            payment_header_name="PAYMENT-SIGNATURE",
            payment_header_value=payment_signature_b64,
            origin=origin,
            x_payment_secure=x_payment_secure,
            risk_sid=risk_sid,
            x_ap2_evd=x_ap2_evd,
        )

    async def settle_xrpl(
        self,
        payment_payload: Dict[str, Any],
        payment_requirements: Dict[str, Any],
        *,
        payment_signature_b64: str,
        origin: Optional[str],
        x_payment_secure: str,
        risk_sid: str,
        x_ap2_evd: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self._post_payment(
            self.settle_url,
            payment_payload,
            payment_requirements,
            x402_version=2,
            payment_header_name="PAYMENT-SIGNATURE",
            payment_header_value=payment_signature_b64,
            origin=origin,
            x_payment_secure=x_payment_secure,
            risk_sid=risk_sid,
            x_ap2_evd=x_ap2_evd,
        )

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

    async def verify_then_settle_xrpl(
        self,
        payment_payload: Dict[str, Any],
        payment_requirements: Dict[str, Any],
        *,
        payment_signature_b64: str,
        origin: Optional[str],
        x_payment_secure: str,
        risk_sid: str,
        x_ap2_evd: Optional[str] = None,
    ) -> Dict[str, Any]:
        v = await self.verify_xrpl(
            payment_payload,
            payment_requirements,
            payment_signature_b64=payment_signature_b64,
            origin=origin,
            x_payment_secure=x_payment_secure,
            risk_sid=risk_sid,
            x_ap2_evd=x_ap2_evd,
        )
        if not v.get("isValid"):
            raise RuntimeError(v.get("invalidReason") or "verification failed")
        return await self.settle_xrpl(
            payment_payload,
            payment_requirements,
            payment_signature_b64=payment_signature_b64,
            origin=origin,
            x_payment_secure=x_payment_secure,
            risk_sid=risk_sid,
            x_ap2_evd=x_ap2_evd,
        )
