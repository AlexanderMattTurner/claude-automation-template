"""install-lychee.sh's refusals — the arms that must never degrade into
"install without verifying".

The happy path is not duplicated here: the pre-commit workflow runs this script
for real on every PR and then runs the lychee hook through the binary it
installed, so a broken install is already a red check. What that run cannot show
is a refusal, because a passing install never takes one.

The script is driven for real against a sandbox copy of .github/tool-versions.sh,
which is the file it reads its version and digest from.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._helpers import REPO_ROOT

INSTALLER = REPO_ROOT / ".github" / "scripts" / "install-lychee.sh"
PINS = REPO_ROOT / ".github" / "tool-versions.sh"


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """A throwaway .github/ holding the real script, so it resolves its pin file
    to the copy this test controls rather than to the repo's own."""
    (tmp_path / ".github" / "scripts").mkdir(parents=True)
    shutil.copy2(INSTALLER, tmp_path / ".github" / "scripts" / "install-lychee.sh")
    (tmp_path / "bin").mkdir()
    return tmp_path


def run_installer(
    sandbox: Path, extra_path: Path | None = None
) -> subprocess.CompletedProcess:
    path = os.environ["PATH"]
    if extra_path is not None:
        path = f"{extra_path}:{path}"
    return subprocess.run(
        ["bash", ".github/scripts/install-lychee.sh", str(sandbox / "bin")],
        cwd=sandbox,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": path},
    )


def installed(sandbox: Path) -> bool:
    return (sandbox / "bin" / "lychee").exists()


def test_refuses_when_the_pin_file_is_absent(sandbox: Path) -> None:
    result = run_installer(sandbox)

    assert result.returncode == 1, result.stderr
    assert "tool-versions.sh does not exist" in result.stderr
    assert not installed(sandbox)


def test_refuses_an_empty_digest(sandbox: Path) -> None:
    """A pin file whose digest went missing — a half-finished version bump, or a
    consumer whose template sync dropped the line — must abort, not install."""
    (sandbox / ".github" / "tool-versions.sh").write_text(
        "LYCHEE_VERSION=0.24.2\n", encoding="utf-8"
    )

    result = run_installer(sandbox)

    assert result.returncode == 1, result.stderr
    assert "digest is unset" in result.stderr
    assert not installed(sandbox)


def test_refuses_a_host_with_no_pinned_asset(sandbox: Path) -> None:
    """Stubbing `uname` is the only way to reach this arm: the real one cannot
    report a platform the test host is not."""
    shutil.copy2(PINS, sandbox / ".github" / "tool-versions.sh")
    stub_dir = sandbox / "stub"
    stub_dir.mkdir()
    uname = stub_dir / "uname"
    uname.write_text(
        '#!/usr/bin/env bash\ncase "$1" in\n-s) echo Plan9 ;;\n-m) echo sparc64 ;;\n'
        "*) exit 1 ;;\nesac\n",
        encoding="utf-8",
    )
    uname.chmod(0o755)

    result = run_installer(sandbox, extra_path=stub_dir)

    assert result.returncode == 1, result.stderr
    assert "no pinned lychee release for Plan9/sparc64" in result.stderr
    assert not installed(sandbox)


def test_refuses_a_release_asset_the_digest_does_not_describe(sandbox: Path) -> None:
    """The one refusal that stands between a swapped or re-tagged release asset
    and PATH. Downloads the real asset at the pinned version, so the bytes being
    rejected are the bytes a session would otherwise have run."""
    pins = PINS.read_text(encoding="utf-8")
    # Same length and alphabet as a real digest, so what the check rejects is the
    # value and not the shape. The version stays as pinned, so the download is
    # the real release asset.
    zeroed = re.sub(
        r"(?m)^(?P<key>LYCHEE_SHA256_\w+)=\w+$",
        lambda m: f"{m['key']}={'0' * 64}",
        pins,
    )
    assert zeroed != pins, "tool-versions.sh carries no lychee digest to corrupt"
    (sandbox / ".github" / "tool-versions.sh").write_text(zeroed, encoding="utf-8")

    result = run_installer(sandbox)

    assert result.returncode != 0, result.stdout
    assert "FAILED" in result.stderr or "FAILED" in result.stdout
    assert not installed(sandbox)
