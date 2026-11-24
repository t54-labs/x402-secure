# 📚 Documentation Index

Welcome to the x402 + AP2 documentation! This directory contains all technical documentation, guides, specifications, and design documents.

## 📖 Quick Navigation

### 🚀 Getting Started
- [Main README](../README.md) - Project overview and quick start
- [Quick Start Guide](QUICKSTART.md) - Fast setup instructions

### 📘 User Guides
Comprehensive guides for using the system:

- [**Run Complete Demo**](archive/guides/RUN_COMPLETE_DEMO.md) - Step-by-step demo walkthrough
- [**Agent Trace Guide**](guides/AGENT_TRACE_GUIDE.md) - Complete guide to agent trace context
- [**View Trace Guide**](archive/guides/VIEW_TRACE_GUIDE.md) - How to query and view trace data
- [**EIP-8004 Migration**](archive/guides/EIP8004_MIGRATION.md) - Migration guide for DID integration

### 📋 Technical Specifications
Formal specifications and data formats:

- [**Payment Trace & Evidence Spec**](specs/payment-trace-and-evidence-spec.md) - Header format and protocol spec
- [**Trace Payload Format**](archive/specs/TRACE_PAYLOAD_FORMAT.md) - Agent trace context structure
- [**Complete Agent Trace**](archive/specs/COMPLETE_AGENT_TRACE.md) - Full trace context specification

### 🎨 Design Documents
Architecture and enhancement proposals:

- [**Agent Trace Enhancement**](design/AGENT_TRACE_ENHANCEMENT.md) - Model config and session context
- [**Open Source & Co-Deploy Plan**](design/OPEN_SOURCE_AND_CO_DEPLOY_PLAN.md) - OSS strategy and architecture

### 📊 Observability
Monitoring and tracing setup:

- [**OpenTelemetry Collector Guide**](observability/otel-collector-minimal.md) - OTEL setup instructions
- [**Collector Config**](observability/otel-collector.yaml) - Ready-to-use OTEL config

---

## 📁 Directory Structure

```
docs/
├── README.md                          # This file
├── guides/                            # User guides and tutorials
│   ├── RUN_COMPLETE_DEMO.md
│   ├── AGENT_TRACE_GUIDE.md
│   ├── VIEW_TRACE_GUIDE.md
│   └── EIP8004_MIGRATION.md
├── specs/                             # Technical specifications
│   ├── payment-trace-and-evidence-spec.md
│   ├── TRACE_PAYLOAD_FORMAT.md
│   └── COMPLETE_AGENT_TRACE.md
├── design/                            # Design documents
│   ├── AGENT_TRACE_ENHANCEMENT.md
│   └── OPEN_SOURCE_AND_CO_DEPLOY_PLAN.md
└── observability/                     # Monitoring and tracing
    ├── otel-collector-minimal.md
    └── otel-collector.yaml
```

---

## 🔍 Finding What You Need

### I want to...

- **Run the complete demo** → [guides/RUN_COMPLETE_DEMO.md](archive/guides/RUN_COMPLETE_DEMO.md)
- **Understand agent tracing** → [guides/AGENT_TRACE_GUIDE.md](guides/AGENT_TRACE_GUIDE.md)
- **View trace data** → [guides/VIEW_TRACE_GUIDE.md](archive/guides/VIEW_TRACE_GUIDE.md)
- **Integrate EIP-8004 DIDs** → [guides/EIP8004_MIGRATION.md](archive/guides/EIP8004_MIGRATION.md)
- **Understand the protocol** → [specs/payment-trace-and-evidence-spec.md](specs/payment-trace-and-evidence-spec.md)
- **Learn about trace format** → [specs/TRACE_PAYLOAD_FORMAT.md](archive/specs/TRACE_PAYLOAD_FORMAT.md)
- **See the architecture** → [design/OPEN_SOURCE_AND_CO_DEPLOY_PLAN.md](design/OPEN_SOURCE_AND_CO_DEPLOY_PLAN.md)
- **Setup observability** → [observability/otel-collector-minimal.md](observability/otel-collector-minimal.md)

---

## 🤝 Contributing to Documentation

When adding new documentation:

1. **Guides** - User-facing tutorials and how-to documents
2. **Specs** - Technical specifications and formal definitions
3. **Design** - Architecture decisions and enhancement proposals
4. **Observability** - Monitoring, tracing, and debugging setup

Update this index when adding new documents!

---

## 📄 License

All documentation is licensed under Apache-2.0, same as the project.
