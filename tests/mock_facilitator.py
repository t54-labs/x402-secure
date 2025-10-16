# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
"""
Mock upstream facilitator for testing.
"""
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Mock Facilitator")


class PaymentRequest(BaseModel):
    x402Version: int
    paymentPayload: Dict[str, Any]
    paymentRequirements: Dict[str, Any]
    paymentHeader: Optional[str] = None


class VerifyResponse(BaseModel):
    isValid: bool
    payer: str
    invalidReason: Optional[str] = None


class SettleResponse(BaseModel):
    success: bool
    payer: str
    transaction: Optional[str] = None
    network: Optional[str] = None
    errorReason: Optional[str] = None


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "service": "mock-facilitator"}


@app.post("/verify", response_model=VerifyResponse)
async def verify_payment(
    request: PaymentRequest,
    x_payment: Optional[str] = Header(None, alias="X-PAYMENT"),
):
    """Mock payment verification."""
    logger.info(f"Verify request: {request.dict()}")
    
    # Check for paymentHeader in request body (sent by proxy)
    payment_header = getattr(request, 'paymentHeader', None) or x_payment
    if not payment_header and hasattr(request, '__dict__') and 'paymentHeader' in request.__dict__:
        payment_header = request.__dict__['paymentHeader']
    
    # Simple validation logic
    payload = request.paymentPayload
    requirements = request.paymentRequirements
    
    # Extract payer from nested payload structure
    payer = None
    if isinstance(payload, dict):
        # Handle nested structure from x402 types
        inner_payload = payload.get("payload", {})
        if isinstance(inner_payload, dict):
            auth = inner_payload.get("authorization", {})
            if isinstance(auth, dict):
                payer = auth.get("from")
        # Fallback to direct from field
        if not payer:
            payer = payload.get("from")
    
    if not payer:
        payer = "0x" + "b" * 40
    
    # Mock validation - always valid for testing
    return VerifyResponse(
        isValid=True,
        payer=payer
    )


@app.post("/settle", response_model=SettleResponse)
async def settle_payment(
    request: PaymentRequest,
    x_payment: Optional[str] = Header(None, alias="X-PAYMENT"),
):
    """Mock payment settlement."""
    logger.info(f"Settle request: {request.dict()}")
    
    # Check for paymentHeader in request body (sent by proxy)
    payment_header = getattr(request, 'paymentHeader', None) or x_payment
    if not payment_header and hasattr(request, '__dict__') and 'paymentHeader' in request.__dict__:
        payment_header = request.__dict__['paymentHeader']
    
    payload = request.paymentPayload
    requirements = request.paymentRequirements
    
    # Mock settlement - always successful for testing
    accept = requirements.get("accepts", [{}])[0]
    
    return SettleResponse(
        success=True,
        payer=payload.get("from", "0x" + "b" * 40),
        transaction="0x" + "f" * 64,  # Mock transaction hash
        network=accept.get("chain", "base-sepolia")
    )


@app.post("/verify-fail", response_model=VerifyResponse)
async def verify_payment_fail(request: PaymentRequest):
    """Mock payment verification that always fails."""
    return VerifyResponse(
        isValid=False,
        payer=request.paymentPayload.get("from", ""),
        invalidReason="Mock failure for testing"
    )


@app.post("/settle-fail", response_model=SettleResponse)
async def settle_payment_fail(request: PaymentRequest):
    """Mock payment settlement that always fails."""
    return SettleResponse(
        success=False,
        payer=request.paymentPayload.get("from", ""),
        errorReason="Mock settlement failure for testing"
    )
