# Agent guide

Read [CONTRIBUTING.md](CONTRIBUTING.md) first; everything there applies.

## Keep documentation accurate

A task prompt rarely mentions documentation, so check it yourself. Before you open or update a pull request, ask whether the change makes any existing document inaccurate. Look beyond the folder you edited. These are the documents most likely to drift:

- The protocol: `protocol-spec/openapi.yaml` and `docs/specs/`. Other parties build against them, so a header, endpoint or request/response shape change must update both (see CONTRIBUTING.md, "Keep the spec in sync"). New or changed header formats also need vectors in `test-vectors/`.
- Integration guides that buyers and sellers copy code from: `README.md`, `docs/QUICKSTART.md`, `docs/BUYER_INTEGRATION.md`, `docs/SELLER_INTEGRATION.md`, `packages/x402-secure/README.md` and its `examples/README.md`.
- `proxy/README.md` and `docs/DEVELOPMENT.md`: proxy routes, environment variables, ports and run commands. Keep `env.example` and `env.example.full` in step with the settings the code reads.
- The guides in `docs/guides/`, which walk through scripts in `scripts/` and the client SDK API.

Then:

- If a document is now wrong, fix it in the same PR.
- If a document describes something that no longer exists, such as a finished migration, a removed endpoint or a retired design, move it to `docs/archive/` as described in [docs/archive/README.md](docs/archive/README.md). Do not leave it in place.
- If no document is affected, add a one-line `Docs:` note to the PR description saying so and why.

This is a judgement call, not a gate; nothing blocks the PR. Files under `docs/archive/` are historical records: do not update them to match new behavior.

This repository is public. Do not put internal hostnames, deployment details, credentials or private URLs in any document.

## Git hooks

Run `scripts/install-git-hooks.sh` once per clone. The pre-push hook prints a documentation reminder that lists documents mentioning names from the changed lines (environment variables, header and function names, codes, URL paths). It cannot find a document that describes the same behavior in words, such as "returns 503 without a store", so it does not replace the check above. It never blocks the push. The installer keeps an existing pre-push hook (it runs after the reminder and its exit status still decides the push) and stops if other active hooks, such as those from `pre-commit install`, would stop running; CI runs the same Ruff checks, so `--force` is safe if you accept that.
