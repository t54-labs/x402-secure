# Contributing to x402-secure

Thanks for your interest in x402-secure. This is an open protocol as much as
it is an implementation, so contributions to the spec and the docs matter as
much as contributions to the code.

## Reporting security issues

**Do not open a public issue.** See [SECURITY.md](SECURITY.md).

## Development setup

Requires Python 3.11 or 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/t54-labs/x402-secure
cd x402-secure
uv sync
cp env.example .env
```

Run the proxy locally:

```bash
uv run python run_facilitator_proxy.py
```

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the full seller + buyer
demo flow.

## Before you open a pull request

Run what CI runs:

```bash
uv run pytest
uvx ruff@0.8.4 check .
uvx ruff@0.8.4 format --check .
```

Optionally install the pre-commit hooks, which cover the same ground:

```bash
uv run pre-commit install
```

Docker integration tests:

```bash
./scripts/test_docker.sh
```

## Pull request guidelines

- **One concern per PR.** A dependency bump, a spec change and a refactor
  belong in three PRs, not one.
- **Explain the why in the commit body.** What breaks without this change,
  and how did you verify the fix?
- **Add or update tests** for behavior changes. New proxy behavior belongs in
  `tests/`; new header formats should get vectors in `test-vectors/`.
- **Keep the spec in sync.** If you change a header, an endpoint, or a
  request/response shape, update `protocol-spec/openapi.yaml` and the
  relevant document under `docs/specs/` in the same PR. A proxy that accepts
  a header the spec does not describe is a bug.
- **Do not commit secrets.** `.env`, private keys and deploy configs are
  gitignored; keep it that way.

## Changes to the protocol

Protocol changes need more care than implementation changes, because other
parties build against them. For anything that alters the wire format:

1. Open an issue describing the problem before writing code.
2. Say explicitly whether the change is backward compatible. Header formats
   are versioned (`w3c.v1`, `evd.v1`, `vi.v1`) — a breaking change means a
   new version token, not a redefinition of an existing one.
3. Include test vectors for new or changed formats.

## Code style

Ruff handles formatting and linting; line length is 100. Public functions
use Google-convention docstrings (enforced by pydocstyle in pre-commit).
Match the conventions of the file you are editing.

## Licensing

x402-secure is Apache 2.0. By contributing you agree that your contributions
are licensed under the same terms. Keep the SPDX header on new files:

```python
# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0
```
