# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from typing import Any, Dict, Optional

import httpx


class PaymentVerificationError(RuntimeError):
    """Verification did not establish that the payment is valid."""


class SettlementDeclined(RuntimeError):
    """The facilitator explicitly declined settlement."""


class SettlementUnknown(RuntimeError):
    """The facilitator did not establish a final settlement outcome."""


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
        self.http = httpx.AsyncClient(timeout=15.0, follow_redirects=False)
        self.last_vi_headers: Dict[str, str] = {}

    @staticmethod
    def _vi_headers_from_response(response: httpx.Response) -> Dict[str, str]:
        return {
            key: value
            for key, value in {
                "X-PAYMENT-AUTHORIZATION": response.headers.get("X-PAYMENT-AUTHORIZATION"),
                "X-VI-DECISION-ID": response.headers.get("X-VI-DECISION-ID"),
                "X-VI-EVIDENCE-REF": response.headers.get("X-VI-EVIDENCE-REF"),
            }.items()
            if value
        }

    async def _verify_with_vi_headers(
        self,
        payment_payload: Dict[str, Any],
        payment_requirements: Dict[str, Any],
        *,
        x_payment_b64: str,
        origin: str,
        x_payment_secure: str,
        risk_sid: str,
        x_ap2_evd: Optional[str] = None,
        x_verifiable_intent: Optional[str] = None,
        verifiable_intent: Optional[Dict[str, Any]] = None,
        ap2_context: Optional[Dict[str, Any]] = None,
        vi_policy: Optional[Dict[str, Any]] = None,
        risk_trace: Optional[str] = None,
    ) -> tuple[Dict[str, Any], Dict[str, str]]:
        headers = {
            "X-PAYMENT": x_payment_b64,
            "Origin": origin,
            "X-PAYMENT-SECURE": x_payment_secure,
            "X-RISK-SESSION": risk_sid,
        }
        if x_ap2_evd:
            headers["X-AP2-EVIDENCE"] = x_ap2_evd
        if x_verifiable_intent:
            headers["X-VERIFIABLE-INTENT"] = x_verifiable_intent
        if risk_trace:
            headers["X-RISK-TRACE"] = risk_trace
        body: Dict[str, Any] = {
            "x402Version": 1,
            "paymentPayload": payment_payload,
            "paymentRequirements": payment_requirements,
        }
        if verifiable_intent is not None:
            body["verifiableIntent"] = verifiable_intent
        if ap2_context is not None:
            body["ap2Context"] = ap2_context
        if vi_policy is not None:
            body["viPolicy"] = vi_policy
        self.last_vi_headers = {}
        try:
            r = await self.http.post(self.verify_url, json=body, headers=headers)
            r.raise_for_status()
            if (
                r.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                != "application/json"
            ):
                raise PaymentVerificationError("Unexpected verification content type")
            result = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PaymentVerificationError("Payment verification failed") from exc
        if not isinstance(result, dict) or type(result.get("isValid")) is not bool:
            raise PaymentVerificationError("Invalid payment verification response")
        vi_headers = self._vi_headers_from_response(r) if result["isValid"] else {}
        self.last_vi_headers = vi_headers
        return result, vi_headers

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
        x_verifiable_intent: Optional[str] = None,
        verifiable_intent: Optional[Dict[str, Any]] = None,
        ap2_context: Optional[Dict[str, Any]] = None,
        vi_policy: Optional[Dict[str, Any]] = None,
        risk_trace: Optional[str] = None,
    ) -> Dict[str, Any]:
        result, _vi_headers = await self._verify_with_vi_headers(
            payment_payload,
            payment_requirements,
            x_payment_b64=x_payment_b64,
            origin=origin,
            x_payment_secure=x_payment_secure,
            risk_sid=risk_sid,
            x_ap2_evd=x_ap2_evd,
            x_verifiable_intent=x_verifiable_intent,
            verifiable_intent=verifiable_intent,
            ap2_context=ap2_context,
            vi_policy=vi_policy,
            risk_trace=risk_trace,
        )
        return result

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
        x_verifiable_intent: Optional[str] = None,
        verifiable_intent: Optional[Dict[str, Any]] = None,
        ap2_context: Optional[Dict[str, Any]] = None,
        vi_policy: Optional[Dict[str, Any]] = None,
        risk_trace: Optional[str] = None,
        vi_decision_id: Optional[str] = None,
        vi_evidence_ref: Optional[str] = None,
        use_last_vi_headers: bool = False,
        payment_authorization: Optional[str] = None,
        settlement_attempt_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        headers = {
            "X-PAYMENT": x_payment_b64,
            "Origin": origin,
            "X-PAYMENT-SECURE": x_payment_secure,
            "X-RISK-SESSION": risk_sid,
        }
        authorization = payment_authorization or (
            self.last_vi_headers.get("X-PAYMENT-AUTHORIZATION") if use_last_vi_headers else None
        )
        if authorization:
            headers["X-PAYMENT-AUTHORIZATION"] = authorization
        if x_ap2_evd:
            headers["X-AP2-EVIDENCE"] = x_ap2_evd
        if x_verifiable_intent:
            headers["X-VERIFIABLE-INTENT"] = x_verifiable_intent
        if risk_trace:
            headers["X-RISK-TRACE"] = risk_trace
        resolved_vi_decision_id = vi_decision_id or (
            self.last_vi_headers.get("X-VI-DECISION-ID") if use_last_vi_headers else None
        )
        resolved_vi_evidence_ref = vi_evidence_ref or (
            self.last_vi_headers.get("X-VI-EVIDENCE-REF") if use_last_vi_headers else None
        )
        if resolved_vi_decision_id:
            headers["X-VI-DECISION-ID"] = resolved_vi_decision_id
        if resolved_vi_evidence_ref:
            headers["X-VI-EVIDENCE-REF"] = resolved_vi_evidence_ref
        if settlement_attempt_id:
            headers["X-SETTLEMENT-ATTEMPT-ID"] = settlement_attempt_id
        if idempotency_key:
            headers["X-IDEMPOTENCY-KEY"] = idempotency_key
        body: Dict[str, Any] = {
            "x402Version": 1,
            "paymentPayload": payment_payload,
            "paymentRequirements": payment_requirements,
        }
        if verifiable_intent is not None:
            body["verifiableIntent"] = verifiable_intent
        if ap2_context is not None:
            body["ap2Context"] = ap2_context
        if vi_policy is not None:
            body["viPolicy"] = vi_policy
        try:
            r = await self.http.post(self.settle_url, json=body, headers=headers)
            r.raise_for_status()
            if (
                r.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                != "application/json"
            ):
                raise SettlementUnknown("Unexpected settlement content type")
            result = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SettlementUnknown("Settlement outcome could not be confirmed") from exc

        if not isinstance(result, dict) or type(result.get("success")) is not bool:
            raise SettlementUnknown("Invalid payment settlement response")
        status = result.get("status")
        if status is not None and (
            not isinstance(status, str)
            or status not in {"success", "settled", "failed", "declined"}
        ):
            raise SettlementUnknown("Settlement outcome is pending or unrecognized")
        if result["success"] is False:
            if status in {"success", "settled"}:
                raise SettlementUnknown("Conflicting settlement outcome")
            raise SettlementDeclined(result.get("errorReason") or "Payment settlement failed")
        if status in {"failed", "declined"} or result.get("errorReason") or result.get("error"):
            raise SettlementUnknown("Conflicting settlement outcome")
        if (
            result.get("network")
            and payment_requirements.get("network")
            and result["network"] != payment_requirements["network"]
        ):
            raise SettlementUnknown("Settlement network differs from the verified quote")
        references = [
            result[key] for key in ("transaction", "txHash") if result.get(key) is not None
        ]
        if (
            not references
            or any(not isinstance(ref, str) or not ref.strip() for ref in references)
            or len(set(references)) != 1
        ):
            raise SettlementUnknown(
                "Successful settlement requires an unambiguous transaction reference"
            )
        return result

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
        x_verifiable_intent: Optional[str] = None,
        verifiable_intent: Optional[Dict[str, Any]] = None,
        ap2_context: Optional[Dict[str, Any]] = None,
        vi_policy: Optional[Dict[str, Any]] = None,
        risk_trace: Optional[str] = None,
        settlement_attempt_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        v, vi_headers = await self._verify_with_vi_headers(
            payment_payload,
            payment_requirements,
            x_payment_b64=x_payment_b64,
            origin=origin,
            x_payment_secure=x_payment_secure,
            risk_sid=risk_sid,
            x_ap2_evd=x_ap2_evd,
            x_verifiable_intent=x_verifiable_intent,
            verifiable_intent=verifiable_intent,
            ap2_context=ap2_context,
            vi_policy=vi_policy,
            risk_trace=risk_trace,
        )
        if v.get("isValid") is not True:
            raise PaymentVerificationError(v.get("invalidReason") or "Verification failed")
        return await self.settle(
            payment_payload,
            payment_requirements,
            x_payment_b64=x_payment_b64,
            origin=origin,
            x_payment_secure=x_payment_secure,
            risk_sid=risk_sid,
            x_ap2_evd=x_ap2_evd,
            x_verifiable_intent=x_verifiable_intent,
            verifiable_intent=verifiable_intent,
            ap2_context=ap2_context,
            vi_policy=vi_policy,
            risk_trace=risk_trace,
            payment_authorization=vi_headers.get("X-PAYMENT-AUTHORIZATION"),
            vi_decision_id=vi_headers.get("X-VI-DECISION-ID"),
            vi_evidence_ref=vi_headers.get("X-VI-EVIDENCE-REF"),
            settlement_attempt_id=settlement_attempt_id,
            idempotency_key=idempotency_key,
        )
