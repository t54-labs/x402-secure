# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import os


def setup_otel_from_env(use_console: bool = True) -> None:
    """Configure OpenTelemetry tracing from environment variables.

    Env vars:
    - OTEL_EXPORTER_OTLP_ENDPOINT (default http://localhost:4318/v1/traces)
    - OTEL_SERVICE_NAME (default x402-buyer)
    - OTEL_CONSOLE_EXPORTER=1 to force console export in addition to OTLP
    """
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
            SimpleSpanProcessor,
        )
    except Exception as e:  # pragma: no cover - import error path
        raise RuntimeError(
            "OpenTelemetry SDK/exporter not installed. Install extras: pip install x402-secure-client[otel]"
        ) from e

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")  # Only enable if explicitly set
    service_name = os.getenv("OTEL_SERVICE_NAME", "x402-buyer")
    resource = Resource.create({SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(provider)

    # OTLP over HTTP (only if endpoint configured)
    if endpoint:
        otlp = OTLPSpanExporter(endpoint=endpoint)
        provider.add_span_processor(BatchSpanProcessor(otlp))

    # Console exporter (default enabled for demos)
    if use_console or os.getenv("OTEL_CONSOLE_EXPORTER", "0").lower() in {"1", "true", "yes"}:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

