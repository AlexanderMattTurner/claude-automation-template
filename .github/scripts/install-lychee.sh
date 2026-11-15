#!/usr/bin/env bash
# Install the pinned lychee binary onto PATH. Args: [dest-dir] (default
# /usr/local/bin, which is on PATH on every hosted runner).
#
# lychee backs the `lychee` pre-commit hook, which fails a commit on an internal
# Markdown link pointing at a missing repo-relative file.
#
# PROBLEM CLASS — a pre-commit hook that installs ITSELF over the network cannot
# run where that network is closed. The upstream auto-installing `lychee` hook
# shells out to cargo-binstall, which resolves the release through
# api.github.com; a sandboxed agent session is refused there, binstall falls back
# to a source build and dies on `cargo-install does not support --install-path`.
# The hook can then never install, so the push gate it belongs to can never
# pass. This script removes the discovery step: the version and the per-platform
# tarball SHA-256 live in .github/tool-versions.sh, and the release asset is
# fetched by its own URL, which lets .pre-commit-config.yaml declare lychee
# `language: system`.
set -euo pipefail

dest="${1:-/usr/local/bin}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

pins="${here}/../tool-versions.sh"
# The file check has to come BEFORE the source. Under `set -euo pipefail` a
# source of a missing file aborts the script on the spot, so the refusal below
# could never be what named the cause.
[[ -f "$pins" ]] || {
  echo "install-lychee: ${pins} does not exist, so no pinned version or digest" >&2
  echo "  can be read; refusing to install an unverified binary." >&2
  exit 1
}
# shellcheck source=/dev/null
source "$pins"

# Each arm names its own digest pin, so the lookup is a plain read rather than an
# indirect expansion: this is the value that decides whether a downloaded binary
# reaches PATH.
case "$(uname -s)/$(uname -m)" in
Linux/x86_64) asset=x86_64-unknown-linux-gnu digest="${LYCHEE_SHA256_linux_amd64:-}" ;;
Linux/aarch64 | Linux/arm64) asset=aarch64-unknown-linux-gnu digest="${LYCHEE_SHA256_linux_arm64:-}" ;;
Darwin/x86_64) asset=x86_64-apple-darwin digest="${LYCHEE_SHA256_darwin_amd64:-}" ;;
Darwin/arm64) asset=aarch64-apple-darwin digest="${LYCHEE_SHA256_darwin_arm64:-}" ;;
*)
  echo "install-lychee: no pinned lychee release for $(uname -s)/$(uname -m);" >&2
  echo "  add the asset name and its digest to .github/tool-versions.sh." >&2
  exit 1
  ;;
esac

# An absent or empty pin must never degrade into "install without verifying" —
# that is a supply-chain check reporting green because its input went missing.
[[ -n "${LYCHEE_VERSION:-}" && -n "$digest" ]] || {
  echo "install-lychee: LYCHEE_VERSION or the ${asset} digest is unset in" >&2
  echo "  .github/tool-versions.sh; refusing to install an unverified binary. Set the" >&2
  echo "  version and its digest together, from the release tarball's own sha256sum." >&2
  exit 1
}

# Already done: the destination holds the pinned version AND bare `lychee`
# resolves to it. The PATH half is not redundant — the environment is what
# changes between runs, and a foreign lychee that appeared since would otherwise
# be certified by a skip.
dest_abs=""
if [[ -d "$dest" ]]; then dest_abs="$(cd "$dest" && pwd)"; fi
resolved_dir=""
if resolved="$(command -v lychee)"; then resolved_dir="$(cd "$(dirname "$resolved")" && pwd)"; fi
if [[ -n "$dest_abs" && "$resolved_dir" == "$dest_abs" ]] &&
  [[ "$("${dest_abs}/lychee" --version 2>/dev/null)" == "lychee ${LYCHEE_VERSION}" ]]; then
  exit 0
fi

tarball="lychee-${asset}.tar.gz"
workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

# --fail so a 5xx is an error rather than an error page saved as the tarball,
# and --max-time because --connect-timeout does not cap an established transfer,
# so a stalled download would otherwise hang the caller forever.
curl -fsSL --retry 6 --retry-all-errors --retry-delay 15 \
  --connect-timeout 30 --max-time 300 \
  -o "${workdir}/${tarball}" \
  "https://github.com/lycheeverse/lychee/releases/download/lychee-v${LYCHEE_VERSION}/${tarball}"

# This refusal is what blocks a swapped, re-tagged or corrupted release asset
# from reaching PATH: the digest is the reviewed one from tool-versions.sh, so a
# mismatch aborts the install rather than certifying a binary nobody vetted.
# macOS ships shasum, not sha256sum; both read this one-line check format.
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum --check <<<"${digest}  ${workdir}/${tarball}"
else
  shasum -a 256 --check <<<"${digest}  ${workdir}/${tarball}"
fi
# The release tarball nests everything under a single lychee-<asset>/ directory,
# so name the member and strip that one level.
tar xzf "${workdir}/${tarball}" -C "$workdir" --strip-components=1 "lychee-${asset}/lychee"

# Prove the one contract the hook depends on, BEFORE the binary reaches PATH:
# `--offline` must FAIL on a repo-relative link whose target is missing. A
# release that stopped reporting that would leave the hook green on exactly the
# link rot it exists to catch — no red anywhere — so the drift is caught here, at
# the version bump, where it is loud.
# Run from the workdir, not the caller's: lychee reads a lychee.toml in the
# current directory, and a repo whose config excluded the probe's link would make
# this check pass without checking anything.
printf '%s\n' '[missing](./no-such-file.md)' >"${workdir}/probe.md"
if (cd "$workdir" && ./lychee --offline --no-progress probe.md) >/dev/null 2>&1; then
  echo "install-lychee: 'lychee --offline' exited 0 on a link to a missing file, so the" >&2
  echo "  ${LYCHEE_VERSION} hook would pass every broken internal link. Refusing to install it." >&2
  exit 1
fi

# sudo only when the destination is not already writable, so this works both on a
# hosted runner (root-owned /usr/local/bin) and in a local checkout writing to a
# user-owned dir.
mkdir -p "$dest" # bare-mkdir-ok: `install` below is the post-condition and fails loudly
if [[ -w "$dest" ]]; then
  install -m 0755 "${workdir}/lychee" "${dest}/lychee"
else
  sudo install -m 0755 "${workdir}/lychee" "${dest}/lychee"
fi

# The guard's success is the post-condition, not the exit status of the install:
# the hook invokes bare `lychee`, so a destination off PATH leaves it looking at
# a command it cannot find — and a different lychee earlier on PATH is worse,
# because the hook would run a version no digest vetted.
dest_abs="$(cd "$dest" && pwd)"
lychee_bin="${dest_abs}/lychee"
resolved="$(command -v lychee)" || resolved=""
[[ -n "$resolved" && "$(cd "$(dirname "$resolved")" && pwd)" == "$dest_abs" ]] || {
  echo "install-lychee: installed ${lychee_bin}, but 'lychee' on PATH resolves to" >&2
  echo "  '${resolved:-nothing}'; refusing to certify a binary this run did not verify." >&2
  echo "  Put ${dest_abs} ahead of it on PATH, then re-run this script." >&2
  exit 1
}

"$lychee_bin" --version
