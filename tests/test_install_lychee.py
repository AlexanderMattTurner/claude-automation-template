"""install-lychee.sh — the decisions every caller delegates to it rather than
re-deriving: whether this checkout declares the hook at all, whether the install
is already done, whether the downloaded tarball is the pinned one, and whether
the binary that reaches PATH still fails a broken link.

No test reaches the network. `curl` is stubbed throughout: to fail, so a run that
did NOT skip is a non-zero exit naming the stub; or to serve a real tarball the
test built. Everything after it — the digest refusal, the `--offline` probe, the
PATH guard — runs for real.
"""

import os
import subprocess
import tarfile
from pathlib import Path

import pytest

from tests._helpers import REPO_ROOT

INSTALLER = REPO_ROOT / ".github" / "scripts" / "install-lychee.sh"
PINNED_VERSION = "9.9.9"
ASSET = "x86_64-unknown-linux-gnu"

# The fake lychee each tarball unpacks to. REFUSES_BROKEN_LINK satisfies the
# `--offline` probe the way a real lychee does; PASSES_EVERYTHING is the drifted
# release the probe exists to reject.
REFUSES_BROKEN_LINK = f'#!/usr/bin/env bash\n[[ "$1" == --version ]] && {{ echo "lychee {PINNED_VERSION}"; exit 0; }}\nexit 2\n'
PASSES_EVERYTHING = f'#!/usr/bin/env bash\n[[ "$1" == --version ]] && echo "lychee {PINNED_VERSION}"\nexit 0\n'


def write_pins(sandbox: Path, digest: str) -> None:
    (sandbox / ".github" / "tool-versions.sh").write_text(
        f"LYCHEE_VERSION={PINNED_VERSION}\nLYCHEE_SHA256_linux_amd64={digest}\n",
        encoding="utf-8",
    )


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """A throwaway checkout holding the real installer, a config that declares the
    hook, a pins file naming a version no release has, and a `curl` that fails."""
    if (os.uname().sysname, os.uname().machine) != ("Linux", "x86_64"):
        pytest.skip("the sandbox pins only the linux_amd64 asset")
    (tmp_path / ".github" / "scripts").mkdir(parents=True)
    (tmp_path / ".github" / "scripts" / "install-lychee.sh").write_bytes(
        INSTALLER.read_bytes()
    )
    (tmp_path / ".pre-commit-config.yaml").write_text(
        "repos:\n  - repo: local\n    hooks:\n      - id: lychee\n        entry: lychee\n",
        encoding="utf-8",
    )
    write_pins(tmp_path, "f" * 64)
    (tmp_path / "bin").mkdir()
    stub_curl(tmp_path, 'echo "curl-stub: the skip did not fire" >&2\nexit 1\n')
    return tmp_path


def stub_curl(sandbox: Path, body: str) -> None:
    curl = sandbox / "bin" / "curl"
    curl.write_text(f"#!/usr/bin/env bash\n{body}", encoding="utf-8")
    curl.chmod(0o755)


def serve_tarball(sandbox: Path, binary: str = REFUSES_BROKEN_LINK) -> str:
    """Build the release tarball the installer expects and have `curl` copy it
    into place. Returns its real SHA-256, so a test can pin or corrupt it."""
    staged = sandbox / "staged" / f"lychee-{ASSET}"
    staged.mkdir(parents=True, exist_ok=True)
    (staged / "lychee").write_text(binary, encoding="utf-8")
    (staged / "lychee").chmod(0o755)
    tarball = sandbox / "staged" / f"lychee-{ASSET}.tar.gz"
    with tarfile.open(tarball, "w:gz") as archive:
        archive.add(staged, arcname=f"lychee-{ASSET}")
    stub_curl(
        sandbox,
        f'while [[ $# -gt 1 ]]; do [[ "$1" == "-o" ]] && out="$2"; shift; done\n'
        f'cp "{tarball}" "$out"\n',
    )
    digest = subprocess.run(
        ["sha256sum", str(tarball)], capture_output=True, text=True, check=True
    )
    return digest.stdout.split()[0]


def run_installer(
    sandbox: Path,
    dest: Path | None = None,
    path_prefix: Path | None = None,
    cache: Path | None = None,
) -> subprocess.CompletedProcess:
    entries = [str(sandbox / "bin"), os.environ["PATH"]]
    if path_prefix is not None:
        entries.insert(0, str(path_prefix))
    env = {**os.environ, "PATH": os.pathsep.join(entries)}
    if cache is not None:
        env["LYCHEE_TARBALL_CACHE"] = str(cache)
    return subprocess.run(
        ["bash", ".github/scripts/install-lychee.sh", str(dest or sandbox / "dest")],
        cwd=sandbox,
        capture_output=True,
        text=True,
        env=env,
    )


def install_binary(sandbox: Path, version: str) -> Path:
    dest = sandbox / "dest"
    dest.mkdir(exist_ok=True)
    binary = dest / "lychee"
    binary.write_text(
        f'#!/usr/bin/env bash\necho "lychee {version}"\n', encoding="utf-8"
    )
    binary.chmod(0o755)
    return dest


def installed(sandbox: Path) -> bool:
    return (sandbox / "dest" / "lychee").exists()


def test_skips_when_the_pinned_binary_already_answers_on_path(sandbox: Path) -> None:
    dest = install_binary(sandbox, PINNED_VERSION)

    result = run_installer(sandbox, path_prefix=dest)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


@pytest.mark.parametrize(
    "installed_version, on_path",
    [("0.0.1", True), (PINNED_VERSION, False)],
    ids=["stale-binary", "another-lychee-wins-on-path"],
)
def test_reinstalls_when_the_destination_is_not_provably_the_pin(
    sandbox: Path, installed_version: str, on_path: bool
) -> None:
    """Both are states where bare `lychee` is not provably the pinned binary, so
    the download must be attempted rather than certified by a skip."""
    dest = install_binary(sandbox, installed_version)
    if not on_path:
        foreign = sandbox / "bin" / "lychee"
        foreign.write_text(
            f'#!/usr/bin/env bash\necho "lychee {PINNED_VERSION}"\n', encoding="utf-8"
        )
        foreign.chmod(0o755)

    result = run_installer(sandbox, path_prefix=dest if on_path else None)

    assert result.returncode != 0
    assert "curl-stub: the skip did not fire" in result.stderr


def test_does_nothing_when_the_config_declares_no_lychee_hook(sandbox: Path) -> None:
    """A consumer owns .pre-commit-config.yaml and template-sync never delivers
    one, so a repo without the hook must not download — nor be refused for a pin
    file it trimmed."""
    (sandbox / ".pre-commit-config.yaml").write_text(
        "repos:\n  - repo: local\n    hooks:\n      - id: shellcheck\n",
        encoding="utf-8",
    )
    (sandbox / ".github" / "tool-versions.sh").unlink()

    result = run_installer(sandbox)

    assert result.returncode == 0, result.stderr
    assert "nothing to install" in result.stdout
    assert not installed(sandbox)


def test_refuses_when_the_pin_file_is_absent(sandbox: Path) -> None:
    (sandbox / ".github" / "tool-versions.sh").unlink()

    result = run_installer(sandbox)

    assert result.returncode == 1, result.stderr
    assert "tool-versions.sh does not exist" in result.stderr
    assert not installed(sandbox)


def test_refuses_an_empty_digest(sandbox: Path) -> None:
    """A pin file whose digest went missing — a half-finished version bump, or a
    consumer whose template sync dropped the line — must abort, not install."""
    write_pins(sandbox, "")

    result = run_installer(sandbox)

    assert result.returncode == 1, result.stderr
    assert "digest is unset" in result.stderr
    assert not installed(sandbox)


def test_refuses_a_release_asset_the_digest_does_not_describe(sandbox: Path) -> None:
    """The refusal that stands between a swapped or re-tagged release asset and
    PATH. `sha256sum` and `tar` are the real ones; only `curl` is stubbed, and it
    serves a well-formed tarball carrying a working `lychee`."""
    serve_tarball(sandbox)

    result = run_installer(sandbox)

    assert result.returncode != 0, result.stdout
    assert "FAILED" in result.stderr or "FAILED" in result.stdout
    assert not installed(sandbox)


def test_refuses_a_release_that_passes_a_broken_internal_link(sandbox: Path) -> None:
    """The hook exists to fail a repo-relative link whose target is missing. A
    release that stopped reporting that would leave it green on exactly that, so
    the probe refuses before the binary reaches PATH."""
    write_pins(sandbox, serve_tarball(sandbox, PASSES_EVERYTHING))

    result = run_installer(sandbox)

    assert result.returncode == 1, result.stdout
    assert "exited 0 on a link to a missing file" in result.stderr
    assert not installed(sandbox)


def test_refuses_when_another_lychee_wins_on_path(sandbox: Path) -> None:
    """The hook invokes bare `lychee`, so an install that does not own that name
    would hand the hook a binary no digest vetted."""
    write_pins(sandbox, serve_tarball(sandbox))
    foreign_dir = sandbox / "foreign"
    foreign_dir.mkdir()
    (foreign_dir / "lychee").write_text(
        "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8"
    )
    (foreign_dir / "lychee").chmod(0o755)

    result = run_installer(sandbox, path_prefix=foreign_dir)

    assert result.returncode == 1, result.stdout
    assert "refusing to certify a binary this run did not verify" in result.stderr


def test_reuses_a_cached_tarball_rather_than_downloading_it_again(
    sandbox: Path,
) -> None:
    """What the CI cache buys. `curl` is the failing stub, so a run that reached
    the network would exit non-zero naming it."""
    digest = serve_tarball(sandbox)
    write_pins(sandbox, digest)
    cache = sandbox / "cache"
    cache.mkdir()
    (cache / f"lychee-{ASSET}.tar.gz").write_bytes(
        (sandbox / "staged" / f"lychee-{ASSET}.tar.gz").read_bytes()
    )
    stub_curl(sandbox, 'echo "curl-stub: the cache was not reused" >&2\nexit 1\n')

    result = run_installer(sandbox, path_prefix=sandbox / "dest", cache=cache)

    assert result.returncode == 0, result.stderr
    assert installed(sandbox)


def test_refuses_and_discards_a_cached_tarball_the_digest_does_not_describe(
    sandbox: Path,
) -> None:
    """The cache is a store this repo does not own, so it sits upstream of the
    digest check and is refused by it. The entry is deleted, or one poisoned
    restore would fail every later run against the same key."""
    write_pins(sandbox, serve_tarball(sandbox))
    cache = sandbox / "cache"
    cache.mkdir()
    poisoned = cache / f"lychee-{ASSET}.tar.gz"
    poisoned.write_bytes(b"not the pinned release asset")

    result = run_installer(sandbox, cache=cache)

    assert result.returncode != 0, result.stdout
    assert not installed(sandbox)
    assert not poisoned.exists()


def test_installs_the_pinned_binary_when_every_refusal_passes(sandbox: Path) -> None:
    write_pins(sandbox, serve_tarball(sandbox))
    dest = sandbox / "dest"

    result = run_installer(sandbox, path_prefix=dest)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == f"lychee {PINNED_VERSION}"
    assert (dest / "lychee").stat().st_mode & 0o111
