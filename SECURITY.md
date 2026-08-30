# Security Policy

x402-secure sits on the payment path for autonomous agents. Vulnerabilities
here can move real money, so we take reports seriously and we would much
rather hear about a problem privately than read about it publicly.

## Supported Versions

Security fixes land on `main` and are released from there. Please confirm an
issue reproduces against the current `main` before reporting.

## Reporting a Vulnerability

**Do not open a public GitHub issue for a security vulnerability.**

Report privately through either channel:

- **GitHub Security Advisories** (preferred) —
  [Report a vulnerability](https://github.com/t54-labs/x402-secure/security/advisories/new)
- **Email** — dev@t54.ai

Please include:

- A description of the issue and its impact
- The affected component (proxy, client SDK, protocol spec, deployment config)
- Steps to reproduce, ideally a minimal proof of concept
- The commit SHA or release you tested against
- Any suggested remediation

### What to expect

| Stage | Target |
|-------|--------|
| Acknowledgement of your report | 3 business days |
| Initial assessment and severity triage | 10 business days |
| Fix or documented mitigation for high/critical findings | 90 days |

We will keep you updated as we work, credit you in the advisory unless you
prefer otherwise, and coordinate disclosure timing with you.

## Scope

In scope:

- The facilitator proxy (`proxy/`) — header parsing, risk gating, upstream
  forwarding, the internal facilitator API
- The client SDK (`packages/x402-secure/`) — payment header construction,
  trace collection, seller verify/settle helpers
- The protocol specification (`protocol-spec/`, `docs/specs/`) — design
  flaws that let an attacker bypass risk evaluation or forge evidence
- Deployment artifacts in this repository (Dockerfiles, compose files)

Out of scope:

- The hosted Trustline risk engine and the hosted proxy at
  `x402-proxy.t54.ai` — report those to dev@t54.ai directly
- Third-party upstream facilitators
- Vulnerabilities in dependencies with no exploitable path through this
  codebase (please still tell us, but they are usually not embargoed)
- Findings that require an already-compromised buyer private key

## Areas we care about most

If you are looking for somewhere to start:

- Bypassing risk gating on `/x402/verify` or `/x402/settle`
- Forging or replaying `X-PAYMENT-SECURE`, `X-AP2-EVIDENCE`,
  `X-VERIFIABLE-INTENT`, or `X-RISK-SESSION`
- SSRF through the mandate fetcher, including allowlist bypass
  (note `MANDATE_URL_ALLOWLIST` defaults to `*`)
- Leaking mandate contents, buyer keys, or internal tokens into logs
- Authentication weaknesses on `/internal/x402-secure/facilitator/*`

## Safe Harbor

We will not pursue or support legal action against researchers who act in
good faith: who report promptly and privately, avoid privacy violations and
service degradation, and only interact with accounts and data they own or
have permission to test.
