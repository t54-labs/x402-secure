# Documentation

## Start here
- [Project README](../README.md): overview and quick start
- [Quick Start](QUICKSTART.md)
- [Buyer integration](BUYER_INTEGRATION.md): risk sessions, agent traces, payment headers
- [Seller integration](SELLER_INTEGRATION.md): calling `/x402/verify` and `/x402/settle`
- [Development](DEVELOPMENT.md): local setup, tests, self-hosting the proxy
- [Proxy README](../proxy/README.md): proxy routes and environment variables
- [SDK README](../packages/x402-secure/README.md) and its [examples](../packages/x402-secure/examples/README.md)

## Protocol
- [OpenAPI spec](../protocol-spec/openapi.yaml): proxy endpoints, headers and bodies
- [Payment trace and evidence headers](specs/payment-trace-and-evidence-spec.md)
- [Test vectors](../test-vectors/)

## Guides
- [Agent trace guide](guides/AGENT_TRACE_GUIDE.md): what the SDK records and how to inspect a stored trace locally

## Observability
- [Minimal OpenTelemetry Collector](observability/otel-collector-minimal.md) and its [config](observability/otel-collector.yaml)

## Historical documents
[archive/README.md](archive/README.md) lists finished migrations, retired designs and old demo walkthroughs. They are kept for context and are not updated to match the code.
