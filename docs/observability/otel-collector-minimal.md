Minimal OpenTelemetry Collector Setup (Local)

Purpose
- Receive spans from the buyer demo over OTLP/HTTP and print them to the Collector logs.
- No external backend required. Good for quick validation that X-PAYMENT-SECURE is produced from an active span.
- Note: the SDK puts the risk `tid` (base64 JSON) in the X-PAYMENT-SECURE `ts` field, so `ts` is not a W3C tracestate list.

Files
- docs/observability/otel-collector.yaml (Collector config)

Run (Docker)
1) Start the Collector:
   docker run --rm -it \
     -p 4318:4318 \
     -v "$(pwd)/docs/observability/otel-collector.yaml:/etc/otelcol/config.yaml:ro" \
     --name otelcol \
     otel/opentelemetry-collector:latest

2) Set env for the buyer demo (same shell you run the demo):
   export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318/v1/traces
   export OTEL_SERVICE_NAME=buyer-demo       # optional; defaults to x402-buyer
   # Console span output is on by default in the examples (setup_otel_from_env(use_console=True)).

3) Start the proxy and example seller first (see docs/DEVELOPMENT.md), then run the buyer demo
   (needs BUYER_PRIVATE_KEY and OPENAI_API_KEY in .env):
   AGENT_GATEWAY_URL=http://localhost:8000 \
   SELLER_BASE_URL=http://localhost:8010 \
   BUYER_EXPECTED_PAY_TO=<seller payTo address> \
   BUYER_EXPECTED_ASSET=<asset address the seller quotes> \
   BUYER_MAX_AMOUNT_ATOMIC=100000 \
   uv run python packages/x402-secure/examples/buyer_agent_openai.py

Expected
- The Collector container logs a line per span (from the debug exporter).
- The buyer console also prints spans.

Config Details
- Receiver: OTLP over HTTP only (port 4318). gRPC is disabled for simplicity.
- Processor: batch (default tuning).
- Exporter: debug (normal verbosity).
- Pipeline: traces only (no metrics/logs).

Note (Production)
- For production, replace the debug exporter with your APM backend (e.g., OTLP to a vendor) and add auth/headers as needed.
