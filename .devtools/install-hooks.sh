#!/usr/bin/env bash
#
# Set up local development for this repo. Installs the Node toolchain (commitlint,
# prettier, lefthook, semantic-release) with pnpm and wires the git hooks, then the
# system linters the pre-commit hook uses.
#
# The linters are optional by design: the hooks warn and skip when one is missing, so a
# fresh clone is never blocked from committing. CI runs them unconditionally.
#
# Usage: sh .devtools/install-hooks.sh
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"

if ! command -v pnpm >/dev/null 2>&1; then
  if command -v corepack >/dev/null 2>&1; then
    echo "Enabling pnpm via corepack..."
    corepack enable
  else
    echo "error: pnpm not found and corepack is unavailable. Install Node 24+ first." >&2
    exit 1
  fi
fi

echo "Installing Node dependencies (also runs 'lefthook install' via the prepare script)..."
pnpm install

if command -v brew >/dev/null 2>&1; then
  for tool in actionlint shellcheck zizmor gitleaks; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      echo "Installing $tool via Homebrew..."
      brew install "$tool"
    fi
  done
else
  echo "note: Homebrew not found. Install actionlint, shellcheck, zizmor and gitleaks"
  echo "      manually for the pre-commit lint hooks; they are skipped without them."
fi

echo "Done. Git hooks are installed."
