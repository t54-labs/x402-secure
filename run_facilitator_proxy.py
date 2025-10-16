#!/usr/bin/env python3
# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
"""
Standalone runner for the x402 Facilitator Proxy.

Env:
  - PROXY_PORT (default: 8000)
  - PROXY_HOST (default: 0.0.0.0)
  - PROXY_UPSTREAM_VERIFY_URL (default: http://localhost:8001/verify)
  - PROXY_UPSTREAM_SETTLE_URL (default: http://localhost:8001/settle)
  - PROXY_TIMEOUT_S (default: 15)
"""

import logging
import os
import sys
from datetime import datetime, timezone

# Add repo root to Python path
repo_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, repo_root)
sys.path.insert(0, os.path.join(repo_root, 'proxy', 'src'))
sys.path.insert(0, os.path.join(repo_root, 'packages', 'x402-secure', 'src'))

# Load .env BEFORE importing secure_x402 so env vars are available during module init
from dotenv import load_dotenv  # type: ignore
load_dotenv()

from fastapi import Depends, FastAPI

from x402_proxy import ProxyRuntimeConfig, get_proxy_cfg, router, risk_router


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("facilitator_proxy")


def build_app() -> FastAPI:
    app = FastAPI(
        title="x402 Facilitator Proxy",
        description="AP2-enforcing proxy for x402 verify/settle",
        version="0.1.0",
    )

    # Wire runtime config via dependency
    def cfg_factory() -> ProxyRuntimeConfig:
        return ProxyRuntimeConfig()  # reads env defaults

    app.dependency_overrides[get_proxy_cfg] = cfg_factory  # type: ignore[arg-type]

    # Health
    @app.get("/health")
    async def health(cfg: ProxyRuntimeConfig = Depends(get_proxy_cfg)) -> dict:
        return {
            "status": "ok",
            "time": datetime.now(timezone.utc).isoformat(),
            "upstream_verify": cfg.upstream_verify_url,
            "upstream_settle": cfg.upstream_settle_url,
        }

    # Mount proxy router (/x402/*)
    app.include_router(router)
    # Mount public Risk API (/risk/*) at the unified gateway
    app.include_router(risk_router)

    logger.info("Facilitator Proxy app initialized")
    return app


app = build_app()


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("PROXY_HOST", "0.0.0.0")
    port = int(os.getenv("PROXY_PORT", "8000"))
    uvicorn.run("run_facilitator_proxy:app", host=host, port=port, reload=True, log_level="info")
