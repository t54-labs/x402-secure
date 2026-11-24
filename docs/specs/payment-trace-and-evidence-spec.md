AP2 Trace Context and Evidence Spec

Status: Current. Normative source for request/response shapes: `protocol-spec/openapi.yaml`.

Summary
- `trace_context` travels only in `X-PAYMENT-SECURE` using W3C Trace Context.
- `X-AP2-EVIDENCE` carries either a mandate reference (`evd.v1`) or base64 signed AP2 evidence JSON; it never carries `trace_context`.
- The proxy does not store or fetch mandates; it forwards the mandate reference to the risk engine.
- Transport: TLS client→proxy. Proxy→risk engine uses HTTPS (deploy-dependent) with a bearer token (`RISK_INTERNAL_TOKEN`). `/x402/verify` returns an HMAC-signed `X-PAYMENT-AUTHORIZATION` that `/x402/settle` requires.

Relationship: X-PAYMENT-SECURE and Agent Trace Context
- X-PAYMENT-SECURE carries the W3C `traceparent` and an optional `ts` value for correlation/observability.
- The Agent Trace Context (rich decision data) is created by the buyer agent and stored under `sid`/`tid` via `POST /risk/session` and `POST /risk/trace`.
- On payment, the buyer sends `X-PAYMENT-SECURE` and `X-RISK-SESSION` (and optionally `X-RISK-TRACE`). The proxy forwards these to `POST /risk/evaluate`, where the risk engine loads the trace by `tid` (from `X-RISK-TRACE` or `ts`) and checks it belongs to `sid`, and uses `tp/ts` to link the decision to the distributed trace.
- X-AP2-EVIDENCE never contains trace context.

End-to-End Flow (High Level)
- Buyer agent (client SDK) creates the risk session via `POST /risk/session`.
- Buyer agent posts the agent trace (and optional fingerprint/telemetry) via `POST /risk/trace`.
- Buyer agent calls the seller with the payment, sending:
  - `X-PAYMENT` (signed x402 payment),
  - `X-PAYMENT-SECURE` (trace context),
  - `X-RISK-SESSION` (sid),
  - `X-AP2-EVIDENCE` (optional).
- The seller calls `POST /x402/verify`. The proxy forwards `sid/tid` + parsed trace context (+ optional mandate) to `POST /risk/evaluate` and uses the decision.
- On success the seller calls `POST /x402/settle` with the `X-PAYMENT-AUTHORIZATION` returned by verify.

Headers

1) X-PAYMENT-SECURE (Trace Context)
- Purpose: Carry W3C Trace Context for risk evaluation; contains no mandate data.
- Format: `w3c.v1;tp=<traceparent>[;ts=<url-encoded value>]`
- Fields
  - `tp` (required): W3C traceparent `00-<32hex trace-id>-<16hex span-id>-<2hex flags>`
  - `ts` (optional): URL-encoded value. The x402-secure SDK sends base64(JSON `{"tid": "<uuid>"}`); the proxy uses that `tid` when `X-RISK-TRACE` is absent and ignores values it cannot decode.
- Limits & Rules
  - Max header length: 4096 bytes
  - Only version `w3c.v1` accepted
  - Validate `traceparent`: correct lengths, version `00`, not all-zero IDs
  - If missing or invalid: `/x402/verify` and `/x402/settle` reject the request with 400 (`TRACE_HEADER_INVALID`).
- Examples
  - `X-PAYMENT-SECURE: w3c.v1;tp=00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01`
  - `X-PAYMENT-SECURE: w3c.v1;tp=00-...-01;ts=eyJ0aWQiOiAiLi4uIn0%3D`

2) X-AP2-EVIDENCE
- Purpose: Optionally reference a stored PaymentMandate, or carry signed AP2 evidence; no trace context allowed.
- A value starting with `evd.v1;` is a mandate reference. Any other value is read as base64(JSON signed AP2 evidence), the same as the request body's `ap2EvidenceHeader`. Signed evidence is required when the merchant policy sets AP2 flags (see `docs/SELLER_INTEGRATION.md`, "AP2 Policy Enforcement").
- Mandate reference format: `evd.v1;mr=<mandate_ref>;ms=<sha256_b64url>;mt=application/json;sz=<bytes>`
- Fields
  - `mr` (required): mandate reference; either opaque key or HTTPS URL
    - Opaque: `mandates/{merchant_id}/{mandate_id}.json`
    - URL: HTTPS only; preferably pre‑signed and short‑lived
  - `ms` (required): base64url(SHA‑256 of raw JSON bytes as stored/fetched)
  - `mt` (required): must equal `application/json`
  - `sz` (required): size in bytes (decimal)
- Limits & Rules
  - Max header length: 2048 bytes
  - Only version `evd.v1` accepted
  - Missing required keys → 400; unknown keys are ignored.
  - The proxy does not fetch `mr`; it forwards `mr/ms/mt/sz` to the risk engine, which is responsible for fetching and SSRF protection.
- Examples
  - Opaque: `X-AP2-EVIDENCE: evd.v1;mr=mandates/merch_123/mand_abc.json;ms=V4fS...;mt=application/json;sz=18345`
  - URL: `X-AP2-EVIDENCE: evd.v1;mr=https://cdn.yourco.com/mandates/merch_123/mand_abc.json?sig=...;ms=V4fS...;mt=application/json;sz=18345`

3) X-PAYMENT-AUTHORIZATION (proxy → seller → proxy)
- Returned by `/x402/verify` when the upstream says `isValid: true` and risk (and VI, when requested) allowed. Opaque string `<base64url(JSON claims)>.<hex HMAC-SHA256>`, max 4096 bytes.
- Required on `/x402/settle`: the proxy recomputes the binding over the same payment, requirements, evidence, policy and upstream pair and rejects with 403 `PAYMENT_AUTHORIZATION_REQUIRED` if the token is missing, altered, for another payment, or expired. Expiry is the earliest of the risk and VI decision TTLs.
- With `PROXY_SETTLE_RISK_ENABLED=1` the proxy also re-runs `/risk/evaluate` at settle.
- Verify and settle also return `X-Request-ID`. Successful responses add `X-Risk-Decision`, `X-Risk-Decision-ID`, `X-Risk-TTL-Seconds`, and when VI was assessed `X-VI-Decision`, `X-VI-Decision-ID`, `X-VI-Verified`, `X-VI-Evidence-Ref`; settle adds `X-VI-Receipt-Status` (`recorded`|`failed`) and `X-VI-Receipt-ID`.

4) X-RISK-TRACE (optional)
- `tid` returned by `POST /risk/trace`; UUID v1 or v4. If absent, the proxy reads `tid` from the `ts` field of `X-PAYMENT-SECURE`.

5) X-VERIFIABLE-INTENT (optional)
- Format: `vi.v1;ref=<ref>;sha256=sha256:<64 hex>;mt=<media type>;sz=<bytes>`, max 4096 bytes.

6) X-SETTLEMENT-ATTEMPT-ID, X-IDEMPOTENCY-KEY (optional, settle only)
- Copied into the VI receipt when VI was assessed.

Gateway/Backend Behavior
- Parse `X-PAYMENT-SECURE`; extract and validate `tp`/`ts`.
- Parse `X-AP2-EVIDENCE`; extract and validate `mr`/`ms`/`mt`/`sz` (mandate optional).
- Attach parsed fields to request metadata (IDs only for logs; redact raw values).
- Forward to the risk engine `/risk/evaluate`:
  - `sid` (required), optional `tid`
  - `trace_context: { tp, ts? }`
  - `mandate` if present: `{ ref: mr, sha256_b64url: ms, mime: mt, size: sz }`
- Client error mapping: all header errors → 400 `{"error": {"code": <code>, "message": ...}, "request_id": ...}` with codes `TRACE_HEADER_INVALID`, `EVIDENCE_HEADER_INVALID`, `RISK_SESSION_INVALID`, `RISK_TRACE_INVALID`, `VI_HEADER_INVALID`, `VI_HEADER_UNSUPPORTED`. See "Error Codes (Gateway)" below.

Mandate Storage
- Mandate storage is out of scope for the proxy. The buyer or merchant stores the mandate where the risk engine can fetch it and passes its reference in `X-AP2-EVIDENCE`.

Risks API

POST /risk/session
- Caller: Buyer agent (client SDK). Public entrypoint on the proxy; forwards to the risk engine unless `PROXY_LOCAL_RISK=1`.
- Auth: none on the proxy. The proxy applies no rate limits or CORS policy; deployments must add them at the edge.
- Request: `{ "agent_did": "0x...wallet", "wallet_address": "0x...wallet", "agent_endpoint": "optional", "app_id": "optional", "device": { ... } }`
- Response: `{ "sid": "uuid", "expires_at": "RFC3339" }`
- Errors: 422 invalid (e.g. `wallet_address` not a 0x EVM address)

POST /risk/trace
- Caller: Buyer agent (client SDK). Public entrypoint on the proxy; forwards to the risk engine unless `PROXY_LOCAL_RISK=1`.
- Auth: none on the proxy. The proxy applies no rate limits or CORS policy; deployments must add them at the edge.
- Request: `{ "sid": "uuid", "agent_trace": { ... }, "fingerprint": { ... }, "telemetry": { ... } }`
- Response: `{ "tid": "uuid" }`
- Errors: 404 unknown `sid`, 422 invalid

POST /risk/evaluate
- Request JSON
```
{
  "sid": "uuid",
  "tid": "uuid-optional",
  "trace_context": { "tp": "traceparent", "ts": "optional" },
  "mandate": {
    "ref": "mandate_ref (opaque or https URL)",
    "sha256_b64url": "base64url-of-sha256",
    "mime": "application/json",
    "size": 18345
  },
  "payment": {
    "protocol": "exact",
    "version": 1,
    "network": "base-sepolia",
    "payload": { "signature": "0x...", "authorization": { "from": "0x...", "to": "0x...", "value": "100000", ... } },
    "extra": { "paymentContext": { ... } }
  }
}
```
- Response JSON (200)
```
{
  "decision": "allow|review|deny",
  "reasons": ["..."],
  "decision_id": "string",
  "ttl_seconds": 300,
  "risk_level": "low|medium|high",
  "used_mandate": true,
  "warnings": ["mandate_fetch_failed", "hash_mismatch"],
  "extra": {}
}
```
- Errors: 404 unknown `sid` or `tid`, 400 `tid` not linked to `sid`, 422 invalid inputs
- Behavior (risk engine): If mandate fetch/hash fails, proceed without mandate (emit `warnings`), unless tenant policy requires mandate → 422
- Any decision other than `allow` makes `/x402/verify` answer 403 (`RISK_DENIED` / `RISK_REVIEW`).

Passing sid From Buyer Agent To The Proxy
- Header (required): `X-RISK-SESSION: <sid>`. A body `sid` is ignored.
- Validation: UUID v1 or v4; missing or malformed → 400 `RISK_SESSION_INVALID`.

Risk engine mandate fetcher (requirements for implementers; not implemented in this repository)
- SSRF protections
  - HTTPS only; host must be on allowlist; reject IP literals
  - Resolve DNS to public IP; block RFC1918/loopback/link-local ranges
  - No redirects, or single redirect to the same host if enabled
- Limits
  - Connect timeout: 200 ms; read timeout: 500 ms; up to 2 retries with backoff
  - Cap size at 25 MB; abort if larger
  - Content-Type must be `application/json`
- Integrity
  - Compute SHA‑256 over fetched bytes and compare to `ms`; mismatch → ignore mandate and add `hash_mismatch` warning
- Caching (optional)
  - Ephemeral cache per `mr` with short TTL to reduce repeated fetches during a session

Identifiers & Formats
- `sid`, `tid`: UUID strings (v1 or v4); `decision_id`: string
- `mandate_ref` (`mr`)
  - Opaque key: `mandates/{merchant_id}/{mandate_id}.json`
  - HTTPS URL: pre‑signed link to allowed hosts (e.g., `cdn.yourco.com`, `*.s3.amazonaws.com`)
- `ms`: base64url of SHA‑256 over raw JSON bytes
- `mt`: must be `application/json`
- `sz`: size in bytes (decimal)

OpenTelemetry (Python SDK Guidance)
- Ensure an active span around the payment call (client span).
- Use OTel propagator to inject into a dict and read `traceparent`.
- Build header: `X-PAYMENT-SECURE: w3c.v1;tp=<tp>[;ts=<url-encoded value>]`.
- If no active span exists, create a short client span for the payment request.

Logging & Privacy
- Redact raw header values in logs; log only IDs and parse status.
- Never log or persist mandate JSON in application logs.
- The risk engine should not persist mandates; optional short‑TTL cache only.


Error Codes (Gateway)
- 400: any malformed header (codes listed under "Gateway/Backend Behavior"), including missing or malformed `X-RISK-SESSION`
- 403: risk deny/review (`RISK_DENIED` / `RISK_REVIEW`); missing or invalid settle authorization (`PAYMENT_AUTHORIZATION_REQUIRED`)
- 422: AP2 evidence or policy failures
- 503: missing or too short `PROXY_PAYMENT_AUTHORIZATION_KEY`

Rationale
- Data minimization: mandates live in shared storage; the risk engine fetches by reference.
- Simplicity: unsigned trace context via standard W3C fields.
- Binding: verify issues an HMAC authorization that settle must present for the same payment.
