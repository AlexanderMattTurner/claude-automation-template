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
    # Pin the fallback install to the "packageManager" version so a bare
    # `npm install -g pnpm` can't pull a newer/older pnpm that rewrites the
    # lockfile into an off-version format — the exact hazard corepack avoids.
    pnpm_spec=$(node -e 'process.stdout.write(require("./package.json").packageManager || "pnpm")')
    echo "Installing ${pnpm_spec}..."
    npm install -g "$pnpm_spec"
  fi

  # Install dependencies (postinstall also sets core.hooksPath, redundantly)
  pnpm install
fi

# Install Python dependencies if applicable
if [[ -f uv.lock ]] && command -v uv &>/dev/null; then
  uv sync
fi

# Register the syntax-aware merge driver .gitattributes names. Those attributes
# are inert until the checkout doing the merge has mergiraf on PATH and
# merge.mergiraf.driver set: git reports nothing and line-merges instead. The
# pinned asset is linux_amd64, so every other host is skipped and keeps the line
# merge it had before.
if [[ "$(uname -s) $(uname -m)" = "Linux x86_64" ]]; then
  mergiraf_dest="/usr/local/bin"
  case ":${PATH}:" in
  *":${HOME}/.local/bin:"*) mergiraf_dest="${HOME}/.local/bin" ;;
  esac
  echo "Installing mergiraf (pinned, digest-verified) into ${mergiraf_dest}..."
  # `timeout` because curl's --connect-timeout does not cap an established
  # transfer, so a stalled download would hang the whole setup.
  if ! timeout --kill-after=10 300 bash .github/scripts/install-mergiraf.sh "$mergiraf_dest"; then
    echo "⚠ mergiraf install failed — this checkout keeps git's line merge" >&2
  elif [[ -z "$(git config --get merge.mergiraf.driver)" ]]; then
    # The post-condition, not the exit status: install-mergiraf.sh exits 0 after
    # installing the binary when git refuses the checkout (dubious ownership),
    # which leaves every merge=mergiraf attribute inert and says nothing.
    echo "⚠ mergiraf installed but merge.mergiraf.driver is unset — merges use git's line merge" >&2
  fi
else
  echo "Skipping mergiraf: no pinned asset for this host — this checkout keeps git's line merge"
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
  echo "⚠ Error: Git hooks are not configured correctly (core.hooksPath != .hooks)." >&2
  echo "  Run: git config core.hooksPath .hooks" >&2
  exit 1
fi
