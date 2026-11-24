#!/bin/bash
# Point this clone at .githooks/ (core.hooksPath). That replaces the hooks
# directory git currently uses, so first check what would stop running:
# - an existing pre-push hook is kept and chained: .githooks/pre-push runs it
#   and returns its exit status;
# - any other active hook (pre-commit secret scanning, for example) cannot be
#   chained, so installation stops unless you pass --force.
set -euo pipefail

FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

REPO_ROOT="$(git rev-parse --show-toplevel)"
PRE_PUSH_HOOK="$REPO_ROOT/.githooks/pre-push"

if [ ! -f "$PRE_PUSH_HOOK" ]; then
  echo "Missing hook file: $PRE_PUSH_HOOK"
  exit 1
fi

current_dir="$(cd "$REPO_ROOT" && git rev-parse --path-format=absolute --git-path hooks)"
if [ "$current_dir" = "$REPO_ROOT/.githooks" ]; then
  echo "Already installed: core.hooksPath = .githooks"
  exit 0
fi

others=()
if [ -d "$current_dir" ]; then
  for hook in "$current_dir"/*; do
    [ -f "$hook" ] && [ -x "$hook" ] || continue
    case "$(basename "$hook")" in
      *.sample|pre-push) ;;
      *) others+=("$hook") ;;
    esac
  done
fi

if [ "${#others[@]}" -gt 0 ] && [ "$FORCE" -ne 1 ]; then
  echo "Not installed: these active hooks in $current_dir would stop running:"
  printf '  - %s\n' "${others[@]}"
  echo "Move them into a hook manager that runs .githooks/pre-push too, or rerun"
  echo "with --force to accept that they no longer run in this clone."
  exit 1
fi

chmod +x "$PRE_PUSH_HOOK"
if [ -x "$current_dir/pre-push" ]; then
  git config x402secure.previousPrePush "$current_dir/pre-push"
  chained="yes: $current_dir/pre-push (its exit status is kept)"
else
  git config --unset x402secure.previousPrePush 2>/dev/null || true
  chained="no (no existing pre-push hook)"
fi
git config core.hooksPath .githooks

echo "Installed git hooks for this repo:"
echo "  core.hooksPath     = .githooks (was $current_dir)"
echo "  pre-push hook      = .githooks/pre-push"
echo "  previous pre-push  = $chained"
[ "${#others[@]}" -gt 0 ] && echo "  no longer running  = ${others[*]}"
echo ""
echo "From now on, every git push prints a documentation reminder. The reminder never blocks the push."
