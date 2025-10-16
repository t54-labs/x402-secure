AP2 Trace Context and Evidence Spec (Draft)

Status: Draft for review
Date: 2025-10-10

Summary
- `trace_context` travels only in `X-PAYMENT-SECURE` using W3C Trace Context.
- `X-AP2-EVIDENCE` carries mandate reference only (optional), no `trace_context`.
- PaymentMandate is upload-only on our side; Risks fetches by reference when provided.
- No signing in this phase. Transport protections: TLS (client→gateway), mTLS (gateway→Risks).

Relationship: X-PAYMENT-SECURE and Agent Trace Context
- X-PAYMENT-SECURE carries only the W3C Trace Context (`traceparent` + optional `tracestate`) for correlation/observability.
- The Agent Trace Context (rich decision data) is created by the buyer agent and stored in Risks under `sid`/`tid` via `POST /risk/session` and `POST /risk/trace`.
- On payment, the buyer sends both: `X-PAYMENT-SECURE` and `X-RISK-SESSION`. Our backend forwards these to `POST /risk/evaluate`, where Risks loads the rich data by `sid` (and may internally correlate to a `tid`) and uses `tp/ts` to link the decision to the distributed trace.
- X-AP2-EVIDENCE never contains trace context; it is mandate reference metadata only, and optional.

End-to-End Flow (High Level)
- Buyer agent (client SDK) creates the risk session via Risks `POST /risk/session`.
- Buyer agent optionally posts device/telemetry via `POST /risk/trace`.
- Buyer agent calls our backend to perform the payment, sending:
  - `X-PAYMENT-SECURE` (trace context),
  - `X-AP2-EVIDENCE` (optional mandate reference),
  - `X-RISK-SESSION` (sid).
- Our backend forwards `sid/tid` + parsed trace context (+ optional mandate) to Risks `POST /risk/evaluate` and uses the decision.

Headers

1) X-PAYMENT-SECURE (Trace Context)
- Purpose: Carry W3C Trace Context for risk evaluation; contains no mandate data.
- Format: `w3c.v1;tp=<traceparent>[;ts=<url-encoded-tracestate>]`
- Fields
  - `tp` (required): W3C traceparent `00-<32hex trace-id>-<16hex span-id>-<2hex flags>`
  - `ts` (optional): URL-encoded W3C tracestate string
- Limits & Rules
  - Max header length: 4096 bytes
  - Only version `w3c.v1` accepted
  - Validate `traceparent`: correct lengths, not all-zero IDs
  - If missing/invalid: proceed without trace context; emit metric and warning
- Examples
  - `X-PAYMENT-SECURE: w3c.v1;tp=00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01`
  - `X-PAYMENT-SECURE: w3c.v1;tp=00-...-01;ts=rojo%3D00f067aa0ba902b7,congo%3Dt61rcWkgMzE`

2) X-AP2-EVIDENCE (Mandate Reference Only)
- Purpose: Optionally reference a stored PaymentMandate; no trace context allowed.
- Format: `evd.v1;mr=<mandate_ref>;ms=<sha256_b64url>;mt=application/json;sz=<bytes>`
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
  - URL `mr` must match allowlist; no IP literals; HTTPS only; no arbitrary redirects (same-host only if enabled)
  - Unknown keys or missing required keys → 400
- Allowlist
  - Env: `MANDATE_URL_ALLOWLIST` (CSV). Examples: `cdn.yourco.com,*.s3.amazonaws.com`.
  - Default: `*` (accept any HTTPS host). Baseline safeguards still apply: HTTPS only; no IP literals; size/timeouts; Content-Type must be `application/json`; SHA‑256 hash must match.
- Examples
  - Opaque: `X-AP2-EVIDENCE: evd.v1;mr=mandates/merch_123/mand_abc.json;ms=V4fS...;mt=application/json;sz=18345`
  - URL: `X-AP2-EVIDENCE: evd.v1;mr=https://cdn.yourco.com/mandates/merch_123/mand_abc.json?sig=...;ms=V4fS...;mt=application/json;sz=18345`

Gateway/Backend Behavior
- Parse `X-PAYMENT-SECURE`; extract and validate `tp`/`ts`.
- Parse `X-AP2-EVIDENCE`; extract and validate `mr`/`ms`/`mt`/`sz` (mandate optional).
- Attach parsed fields to request metadata (IDs only for logs; redact raw values).
- Forward to Risks `/risk/evaluate`:
  - `sid` (required), optional `tid`
  - `trace_context: { tp, ts? }`
  - `mandate` if present: `{ ref: mr, sha256_b64url: ms, mime: mt, size: sz }`
- Client error mapping
  - `X-PAYMENT-SECURE`: 400 malformed, 422 unsupported version, 413 too large
  - `X-AP2-EVIDENCE`: 400 malformed, 422 unsupported version, 413 too large

Mandate Upload (Our Server)
- Endpoint: `POST /mandates`
- Auth: Merchant auth (Bearer or mTLS)
- Request
  - Headers: `Content-Type: application/json`
  - Body: raw JSON mandate (no wrapper)
  - Optional query: `merchant_id`, `mandate_id` (server may generate)
- Response (201)
  - `mandate_id` (string)
  - `mandate_ref` (opaque key, e.g., `mandates/{merchant_id}/{mandate_id}.json`)
  - `sha256_b64url` (string)
  - `size` (int)
  - `mime` = `application/json`
- Constraints
  - Max size: 25 MB; type must be JSON
  - On store: compute and persist `sha256_b64url` and `size`
- Errors: 413 (too large), 415 (unsupported media type), 400 (invalid JSON)

Risks API

POST /risk/session
- Caller: Buyer agent (client SDK). Public entrypoint on our server; forwards to remote Risk service when configured.
- Auth: Public (server-side rate limits apply).
- Request: `{ "agent_id": "0x...wallet", "app_id": "optional", "device": { ... } }`
- Response: `{ "sid": "uuid", "expires_at": "RFC3339" }`
- Errors: 400 invalid, 429 rate limit

POST /risk/trace
- Caller: Buyer agent (client SDK). Public entrypoint on our server; forwards to remote Risk service when configured.
- Auth: Same as session; CORS allowlist and rate limits apply.
- Request: `{ "sid": "uuid", "fingerprint": { ... }, "telemetry": { ... } }`
- Response: `{ "tid": "uuid" }`
- Errors: 404 unknown `sid`, 400 invalid

POST /risk/evaluate
- Request JSON
```
{
  "sid": "uuid",
  "tid": "uuid-optional",
  "trace_context": { "tp": "traceparent", "ts": "tracestate-optional" },
  "mandate": {
    "ref": "mandate_ref (opaque or https URL)",
    "sha256_b64url": "base64url-of-sha256",
    "mime": "application/json",
    "size": 18345
  },
  "payment": { "payment_id": "optional", "amount": 1234, "currency": "USD" }
}
```
- Response JSON (200)
```
{
  "decision": "allow|review|deny",
  "reasons": ["..."],
  "decision_id": "uuid",
  "ttl_seconds": 300,
  "used_mandate": true,
  "warnings": ["mandate_fetch_failed", "hash_mismatch"]
}
```
- Errors: 400 invalid inputs, 404 unknown `sid`, 422 unsupported trace version
- Behavior: If mandate fetch/hash fails, proceed without mandate (emit `warnings`), unless tenant policy requires mandate → 422

Passing sid From Buyer Agent To Our Backend
- Header (preferred): `X-RISK-SESSION: <sid>` (required)
- Alternative: include `sid` in the payment request body; avoid duplicating in both.
- Backend validation: `sid` must be a UUIDv4; if missing or malformed, return 400.

Risks Mandate Fetcher (when `mr` is a URL)
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
- `sid`, `tid`, `decision_id`: UUIDv4 strings
- `mandate_ref` (`mr`)
  - Opaque key: `mandates/{merchant_id}/{mandate_id}.json`
  - HTTPS URL: pre‑signed link to allowed hosts (e.g., `cdn.yourco.com`, `*.s3.amazonaws.com`)
- `ms`: base64url of SHA‑256 over raw JSON bytes
- `mt`: must be `application/json`
- `sz`: size in bytes (decimal)

OpenTelemetry (Python SDK Guidance)
- Ensure an active span around the payment call (client span).
- Use OTel propagator to inject into a dict and read `traceparent`/`tracestate`.
- Build header: `X-PAYMENT-SECURE: w3c.v1;tp=<tp>[;ts=<url-encoded-ts>]`.
- If no active span exists, create a short client span for the payment request.

Logging & Privacy
- Redact raw header values in logs; log only IDs and parse status.
- Never log or persist mandate JSON in application logs.
- Risks does not persist mandates; optional short‑TTL cache only.


Error Codes (Gateway)
- 400: malformed header (unknown keys, bad format)
- 413: header too large
- 422: unsupported version/format
- 400: missing or malformed `X-RISK-SESSION` when required

Rationale
- Data minimization: mandates live in shared storage; Risks fetches by reference.
- Simplicity: unsigned trace context via standard W3C fields.
- Security-in-depth: mTLS backend path, SSRF protections, integrity hash, strict parsers.
