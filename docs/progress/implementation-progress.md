Implementation Progress Log

Owner: engineering
Start: 2025-10-11
Mode: fail-fast (no defensive fallbacks)

Scope
- Gateway/Proxy: parse/validate new headers, forward to Risks evaluate.
- Risks Service: /risk/session, /risk/trace, /risk/evaluate.
- Mandate Upload: JSON-only, return mandate_ref + ms + sz.

Changelog

2025-10-11
- Added header spec doc: docs/specs/payment-trace-and-evidence-spec.md (earlier step)
- [ADDED] Header parsing utilities (not yet wired):
  - X-PAYMENT-SECURE: w3c.v1;tp=...;ts=...
  - X-AP2-EVIDENCE: evd.v1;mr=...;ms=...;mt=application/json;sz=...
 - X-RISK-SESSION / X-RISK-TRACE validation
- Next planned step (pending review):
  - Wire parser into proxy (/x402/verify,/x402/settle) to construct evaluate requests. (DONE)
  - Proxy now calls Risks /risk/evaluate and sets X-Risk-Decision header; still forwards to upstream.
  - Add Risks endpoints (/risk/session,/risk/trace,/risk/evaluate) to risk_engine.server. (DONE)
 - Add short Relationship section to spec clarifying X-PAYMENT-SECURE vs Agent Trace Context. (DONE)
  - Drop optional header X-RISK-TRACE; spec and proxy accept only X-RISK-SESSION. (DONE)
- Buyer SDK: added OpenTelemetry helper `build_payment_secure_header()` and context manager `start_client_span()`.
- Buyer SDK: added `RiskClient` for /risk/session and /risk/trace (renamed from RiskV2Client).
 - Proxy client: verify/settle methods send X-PAYMENT-SECURE and X-RISK-SESSION with optional AP2 evidence.
 - Seller demo: now requires X-PAYMENT, X-PAYMENT-SECURE, and X-RISK-SESSION; calls verify/settle.
- Buyer demo: creates sid via RiskClient, wraps payment call in a client span, builds X-PAYMENT-SECURE, and calls BuyerClient.execute_paid_request with sid and headers.
 - Buyer demo: removed legacy /ap2/session usage; relies solely on /risk/session for sid.
- Removed legacy buyer path and mandate flow:
   - Deleted RiskEngineClient, AP2EvidenceBuilder, and BuyerClient.execute_paid_request (legacy) in sdk/payments.py.
 - Buyer demo OTel setup: added setup_otel_from_env() call; supports console exporter by default and OTLP via OTEL_EXPORTER_OTLP_ENDPOINT.
 - Docs: added minimal OpenTelemetry Collector example and config:
   - docs/observability/otel-collector-minimal.md (how to run)
   - docs/observability/otel-collector.yaml (ready-to-run config)
 
2025-10-11 (Tests)
- Added unit tests for header parsing: tests/test_headers.py
- Updated proxy tests to new header flow with X-PAYMENT-SECURE + X-RISK-SESSION and stubbed risk evaluate: tests/test_proxy_ap2_checks.py
- Updated seller proxy smoke test to use new headers and risk stub: tests/test_seller_proxy_smoke.py
- Added local Risk endpoints smoke test (guarded by ENABLE_LOCAL_RISK): tests/test_risk_endpoints.py

2025-10-11 (Risk proxying)
- Server now always exposes /risk/session and /risk/trace for the buyer agent.
  - If ENABLE_LOCAL_RISK=true → uses in-memory local store (dev).
  - Else → forwards to remote Risk service at RISK_API_URL (or RISK_ENGINE_URL) and returns the upstream response.

2025-10-11 (Session schema)
- Switched /risk/session to accept `agent_id` (buyer wallet address) instead of `merchant_id`.
- Buyer demo now instantiates BuyerClient early and uses `buyer.address` for `agent_id`.

Details 2025-10-11
- risk_engine/server.py
  - Added Pydantic models: RiskSessionRequest/Response, RiskTraceRequest/Response, TraceContext, MandateMeta, EvaluateRequest/Response.
  - Added in-memory TTL caches for sid/tid.
  - Endpoints:
    - POST /risk/session → returns sid + expires_at (Z) (fail if merchant_id missing).
    - POST /risk/trace → returns tid (fail if sid unknown).
    - POST /risk/evaluate → validates sid/tid linkage, W3C traceparent, optional mandate.
      - Mandate: if URL, enforce allowlist, https, content-type, size, and SHA-256 integrity; hash mismatch/fetch errors → 400.
      - Opaque ref accepted without fetch (no persistence in Risks).
      - Decision is stubbed to "allow" with TTL=300; records used_mandate flag.
  - Added Mandate Upload API:
    - POST /mandates → accepts application/json body, size ≤ 25 MB; returns 201 with mandate_id, mandate_ref (opaque), sha256_b64url, size, mime.
    - Stores raw JSON bytes under `${MANDATE_STORAGE_DIR}/mandates/{merchant_id}/{mandate_id}.json` (default storage/).
    - Generates merchant_id/mandate_id if not provided as query params.
  - Guarded local Risk endpoints behind env flag:
    - ENABLE_LOCAL_RISK=true to expose /risk/session, /risk/trace, /risk/evaluate (default disabled).

- sdk/secure_x402/proxy.py
  - Added risk decision gating: if decision == "deny", fail fast with 403 and error code RISK_DENIED. For other outcomes, forward to upstream and include headers `X-Risk-Decision`, `X-Risk-Decision-ID`, `X-Risk-TTL-Seconds`.
