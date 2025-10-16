# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
"""X-402 Proxy Server

Provides a FastAPI router that validates AP2 evidence and forwards to upstream x402 facilitators.

Usage:
    from x402_proxy import router, ProxyRuntimeConfig
    
    app = FastAPI()
    app.include_router(router)
"""

from .routes import (
    router,
    ProxyRuntimeConfig,
    get_proxy_cfg,
    ProxyVerifyRequest,
    ProxySettleRequest,
    VerifyResponse,
    SettleResponse,
    AP2Evidence,
    AP2Policy,
    extract_ap2_policy,
    parse_ap2_evidence_b64,
    verify_ap2,
    fetch_and_validate_ap2_context,
)
from .headers import (
    HeaderError,
    parse_x_payment_secure,
    parse_x_ap2_evidence,
    parse_risk_ids,
)
from .risk_routes import router as risk_router

__version__ = "1.0.0"

__all__ = [
    "router",
    "ProxyRuntimeConfig",
    "get_proxy_cfg",
    "ProxyVerifyRequest",
    "ProxySettleRequest",
    "VerifyResponse",
    "SettleResponse",
    "AP2Evidence",
    "AP2Policy",
    "extract_ap2_policy",
    "parse_ap2_evidence_b64",
    "verify_ap2",
    "fetch_and_validate_ap2_context",
    "HeaderError",
    "parse_x_payment_secure",
    "parse_x_ap2_evidence",
    "parse_risk_ids",
    "risk_router",
]

