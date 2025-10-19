# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0

FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set working directory
WORKDIR /app

# Copy project files
COPY pyproject.toml uv.lock ./
COPY proxy/pyproject.toml proxy/pyproject.toml
COPY proxy/README.md proxy/README.md
COPY packages/x402-secure/pyproject.toml packages/x402-secure/pyproject.toml
COPY packages/x402-secure/README.md packages/x402-secure/README.md

# Copy source code first (needed for editable install)
COPY proxy/src/ proxy/src/
COPY packages/x402-secure/src/ packages/x402-secure/src/
COPY run_facilitator_proxy.py .

# Install dependencies directly (skip the root package)
RUN uv pip install --system \
    x402==0.2.1 \
    fastapi==0.117.1 \
    uvicorn==0.24.0 \
    requests==2.31.0 \
    python-dotenv==1.1.1 \
    httpx==0.28.1 \
    web3==7.13.0 \
    eth-account==0.13.7 \
    pyjwt==2.8.0 \
    jwcrypto==1.5.0 \
    cryptography==41.0.7 \
    cachetools==5.3.2 \
    pydantic>=2.10.3 \
    opentelemetry-api>=1.24.0 \
    opentelemetry-sdk>=1.24.0 \
    opentelemetry-exporter-otlp>=1.24.0

# Install the local packages
RUN uv pip install --system -e ./proxy
RUN uv pip install --system -e ./packages/x402-secure

# Default environment variables for local risk mode
ENV PROXY_LOCAL_RISK=1
ENV UPSTREAM_FACILITATOR_BASE_URL=https://facilitator.example.com
ENV AGENT_GATEWAY_URL=http://0.0.0.0:8000
ENV PROXY_HOST=0.0.0.0
ENV PROXY_PORT=8000

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the proxy
CMD ["python", "run_facilitator_proxy.py"]
