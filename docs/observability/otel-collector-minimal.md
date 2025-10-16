Minimal OpenTelemetry Collector Setup (Local)

Purpose
- Receive spans from the buyer demo over OTLP/HTTP and print them to the Collector logs.
- No external backend required. Good for quick validation that X-PAYMENT-SECURE is produced from an active span.

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
   export OTEL_CONSOLE=1                     # optional; also prints spans locally
   export OTEL_SERVICE_NAME=buyer-demo       # optional; defaults to buyer-demo

3) Run the buyer demo:
   AGENT_GATEWAY_URL=http://localhost:8060 SELLER_BASE_URL=http://localhost:8010 \
   uv run python packages/x402-secure-client/examples/buyer_agent_openai.py

Expected
- The Collector container logs a line per span (from the logging exporter).
- The buyer console also prints spans if OTEL_CONSOLE=1.

Config Details
- Receiver: OTLP over HTTP only (port 4318). gRPC is disabled for simplicity.
- Processor: batch (default tuning).
- Exporter: logging (info level).
- Pipeline: traces only (no metrics/logs).

Note (Production)
- For production, replace the logging exporter with your APM backend (e.g., OTLP to a vendor) and add auth/headers as needed.

