# shellcheck shell=bash
# shellcheck disable=SC2034  # every value here is consumed by a script that SOURCES this file
# Pinned versions and digests for tools CI downloads directly, rather than
# through a package manager that would do its own integrity check.
#
# Sourced by the install-*.sh scripts beside it. Not executable, and it must stay
# free of side effects: a consumer sources it purely to read these values.
#
# Bump a version and its digest TOGETHER. A version bumped without its digest
# fails the install closed, which is the intended direction — the opposite
# (a digest that no longer describes the pinned artifact) is the one that would
# certify an unreviewed binary.

# pre-commit runs the merged tree's own hooks before the resolver bundles a
# resolution, so pip-install-ci-tools.sh installs it at this pin rather than at
# PyPI's newest. Read by the resolver, which is why it is pinned beside a digest
# it does not need: pre-commit arrives through pip, which verifies its own.
PRE_COMMIT_VERSION=4.6.1

# mergiraf backs the structural pre-pass in the resolver repository's auto-resolve/prepare.sh:
# a syntax-aware merge that resolves the structural subset of a PR's conflicts so
# only genuinely semantic conflicts reach the paid LLM pass.
#
# install-mergiraf.sh downloads the pinned release tarball from Codeberg and
# sha256-verifies it before extracting. The digest HERE is the anchor, not the
# checksum manifest published alongside the release: a manifest fetched from the
# same tag as the artifact is re-published by anyone who can re-tag that release,
# so it proves the download was not corrupted in transit and says nothing about
# the release being the one we reviewed.
MERGIRAF_VERSION=v0.18.0
MERGIRAF_SHA256_linux_amd64=4de0986ff9155411dd105958b94362056d0055025db75369eddd3ecd25334cd2

# lychee backs the `lychee` pre-commit hook, which fails a commit on an internal
# Markdown link pointing at a missing repo-relative file. install-lychee.sh reads
# these, downloads the matching release tarball and sha256-verifies it before the
# binary reaches PATH; its header says why that hook takes a pinned binary
# instead of installing itself.
LYCHEE_VERSION=0.24.2
LYCHEE_SHA256_linux_amd64=1f4e0ef7f6554a6ed33dd7ac144fb2e1bbed98598e7af973042fc5cd43951c9a
LYCHEE_SHA256_linux_arm64=91a7bd65685da41b90ccb9bc867a3d649a7818042dae04ff405e55a25bddee4c
LYCHEE_SHA256_darwin_amd64=887503a9cff667d322b8d0892b40bf49976eb9507af8483220a3706cdad55978
LYCHEE_SHA256_darwin_arm64=c9d3740ea2d891854d37116c9fba840f37b6e7c89d330e7db84ac333631c4977

# PyYAML for the workflow Python that reads YAML (resolver-ref.py, template-sync.sh's SYNC_PATHS
# reader). run-with-pyyaml.sh installs it through uv at this pin, so no step relies on the runner
# image shipping it.
PYYAML_VERSION=6.0.3
