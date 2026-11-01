# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the unauthenticated public surface of the gateway (F-021).

- ``/x402/debug`` must not exist: it used to serialize upstream URLs and the
  last upstream verify/settle response (payer, request ID, raw body).
- ``/health`` must return status only, with no upstream URLs or other config.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

SECRET_VERIFY_URL = "https://upstream-verify.internal.example/verify"
SECRET_SETTLE_URL = "https://upstream-settle.internal.example/settle"


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("PROXY_UPSTREAM_VERIFY_URL", SECRET_VERIFY_URL)
    monkeypatch.setenv("PROXY_UPSTREAM_SETTLE_URL", SECRET_SETTLE_URL)
    # Even an operator who still sets the legacy flag must not get the route back.
    monkeypatch.setenv("PROXY_DEBUG_ENABLED", "true")
    from run_facilitator_proxy import build_app

    return TestClient(build_app())


def test_debug_route_is_not_registered(client: TestClient) -> None:
    resp = client.get("/x402/debug")
    assert resp.status_code == 404
    assert "upstream" not in resp.text


def test_debug_route_absent_from_router() -> None:
    from x402_proxy import router

    paths = {getattr(r, "path", "") for r in router.routes}
    assert not any(p.endswith("/debug") for p in paths)


def test_health_returns_status_only(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    body = resp.text
    assert "http" not in body
    assert SECRET_VERIFY_URL not in body
    assert SECRET_SETTLE_URL not in body
