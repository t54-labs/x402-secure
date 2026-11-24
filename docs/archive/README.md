# Archived documents

These documents describe finished migrations, old demo walkthroughs, removed features and retired designs. They no longer match the code and are kept for history only: **do not build, operate or review against them.** Each file starts with a banner that says why it is outdated and where the current source is.

Paths mirror where each file used to live: files from `docs/` drop that prefix, and every other file keeps its full repository path. Last checked against `main@b4526b8` on 2026-11-24.

## Archiving a document

1. Move it here with `git mv`, keeping the path rule above.
2. Add the banner used by the other files: why it is outdated, the current source, the original path and the commit you checked against.
3. Update links to it and the code, chart and deploy comments that cite it.
4. Find tests that read it (`git grep -n '<file name>' -- tests`). Remove assertions that only pin the old document's wording; keep real checks by pointing them at a current document.
5. Add a row below.

| Archived file | Moved from | Why it is outdated | Current source |
|---|---|---|---|
| [guides/EIP8004_MIGRATION.md](guides/EIP8004_MIGRATION.md) | `docs/guides/EIP8004_MIGRATION.md` | Records the 2025-10 rename of `agent_id` to `agent_did` and a plan to resolve EIP-8004 identities in the risk engine. That resolution was never built in this repository, and ERC-8004 is still a draft. | [`packages/x402-secure/src/x402_secure_client/risk.py`](../../packages/x402-secure/src/x402_secure_client/risk.py) |
| [guides/RUN_COMPLETE_DEMO.md](guides/RUN_COMPLETE_DEMO.md) | `docs/guides/RUN_COMPLETE_DEMO.md` | A 2025-10 demo walkthrough. Its scripts are gone, and its commands predate the payment authorization key that verify and settle now require. | [`packages/x402-secure/examples/README.md`](../../packages/x402-secure/examples/README.md) |
| [guides/VIEW_TRACE_GUIDE.md](guides/VIEW_TRACE_GUIDE.md) | `docs/guides/VIEW_TRACE_GUIDE.md` | Describes 2025-10 proxy debug logging (raw payload dumps, a proxy log file) that has been removed. | [`docs/guides/AGENT_TRACE_GUIDE.md`](../guides/AGENT_TRACE_GUIDE.md) |
| [specs/COMPLETE_AGENT_TRACE.md](specs/COMPLETE_AGENT_TRACE.md) | `docs/specs/COMPLETE_AGENT_TRACE.md` | A 2025-10 implementation report for the conversation fields of the agent trace. The fields are implemented in the SDK; the risk-checking code it shows is illustrative and not part of this repository. | [`packages/x402-secure/src/x402_secure_client/tracing.py`](../../packages/x402-secure/src/x402_secure_client/tracing.py) |
| [specs/TRACE_PAYLOAD_FORMAT.md](specs/TRACE_PAYLOAD_FORMAT.md) | `docs/specs/TRACE_PAYLOAD_FORMAT.md` | Explains a 2025-10 change to the proxy's local-mode console logging that has since been removed. Its example payloads predate the current SDK event format. | [`protocol-spec/openapi.yaml`](../../protocol-spec/openapi.yaml) |
