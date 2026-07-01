#!/bin/bash
# One-command setup for the Claude automation template

set -euo pipefail

echo "Setting up Claude automation template..."

# Configure git hooks
git config core.hooksPath .hooks

if [[ -f package.json ]]; then
  # Route through corepack so the pnpm version actually used matches the
  # "packageManager" pin in package.json — a bare `pnpm` on PATH (e.g. from
  # `npm install -g pnpm`) bypasses that pin and can rewrite the lockfile
  # into an off-version format.
  if command -v corepack &>/dev/null; then
    corepack enable
  else
    echo "Installing pnpm..."
    npm install -g pnpm
  fi

  # Install dependencies (postinstall also sets core.hooksPath, redundantly)
  pnpm install
fi

# Install Python dependencies if applicable
if [[ -f uv.lock ]] && command -v uv &>/dev/null; then
  uv sync
fi

# Verify setup
if [[ "$(git config core.hooksPath)" = ".hooks" ]]; then
  echo ""
  echo "✓ Setup complete!"
  echo ""
  echo "Next steps:"
  echo "  1. Edit CLAUDE.md with your project details"
  if [[ -f package.json ]]; then
    echo "  2. Configure scripts in package.json"
  fi
  echo "  Start coding!"
else
  echo ""
  echo "⚠ Warning: Git hooks may not be configured correctly."
  echo "  Run: git config core.hooksPath .hooks"
fi
