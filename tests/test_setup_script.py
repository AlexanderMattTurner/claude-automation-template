"""setup.sh's two tool-install legs — mergiraf, which wires the merge driver, and
lychee, which backs the `lychee` pre-commit hook.

Both installers are stubbed: the real ones download a pinned tarball, which a
unit test must not depend on. What is driven for real is setup.sh's decision —
whether it calls each installer, where it installs, and whether it reports a
post-condition that never held.
"""

import os
import subprocess
from pathlib import Path

import pytest

from tests._helpers import REPO_ROOT

SETUP = REPO_ROOT / "setup.sh"

# The installer, as setup.sh sees it. Each body ends in a different post-state.
REGISTERS = 'git config merge.mergiraf.driver "stub-driver"\n'
FAILS = "exit 1\n"
# install-mergiraf.sh's own not-a-work-tree arm: the binary is installed, the
# driver is not bound, and it exits 0.
SUCCEEDS_WITHOUT_REGISTERING = "exit 0\n"


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """A throwaway repository holding setup.sh and nothing setup.sh's other legs
    act on — no package.json, no uv.lock — so only the mergiraf leg runs."""
    # Not under-provisioning: setup.sh installs nothing off Linux/x86_64 because
    # the pinned asset is linux_amd64, so there is no install to assert.
    if (os.uname().sysname, os.uname().machine) != ("Linux", "x86_64"):
        pytest.skip("setup.sh installs mergiraf only on Linux/x86_64")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "setup.sh").write_bytes(SETUP.read_bytes())
    (tmp_path / ".github" / "scripts").mkdir(parents=True)
    return tmp_path


def stub_installer(sandbox: Path, name: str, body: str) -> None:
    """An installer that records the destination setup.sh asked for, then ends in
    the post-state `body` describes."""
    installer = sandbox / ".github" / "scripts" / name
    installer.write_text(
        f'#!/usr/bin/env bash\nprintf "%s\\n" "$1" >>{name}-calls\n{body}',
        encoding="utf-8",
    )
    installer.chmod(0o755)


def run_setup(
    sandbox: Path, installer_body: str, lychee_body: str = "exit 0\n"
) -> subprocess.CompletedProcess:
    stub_installer(sandbox, "install-mergiraf.sh", installer_body)
    stub_installer(sandbox, "install-lychee.sh", lychee_body)
    home = sandbox / "home"
    home.mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", "setup.sh"],
        cwd=sandbox,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "HOME": str(home),
            # Deliberately WITHOUT $HOME/.local/bin: setup.sh must not fall back
            # to a root-owned /usr/local/bin, which would make the installer
            # escalate with sudo and stall the one-command setup on a prompt.
            "PATH": os.environ["PATH"],
            # A host with a global merge.mergiraf.driver — mergiraf's own setup
            # docs register one — would answer for this sandbox otherwise.
            "GIT_CONFIG_GLOBAL": str(sandbox / "gitconfig-global"),
            "GIT_CONFIG_SYSTEM": os.devnull,
        },
    )


def registered_driver(sandbox: Path) -> str:
    result = subprocess.run(
        ["git", "config", "--get", "merge.mergiraf.driver"],
        cwd=sandbox,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": str(sandbox / "gitconfig-global"),
            "GIT_CONFIG_SYSTEM": os.devnull,
        },
    )
    return result.stdout.strip()


def test_setup_registers_the_merge_driver(sandbox: Path) -> None:
    result = run_setup(sandbox, REGISTERS)

    assert result.returncode == 0, result.stderr
    assert registered_driver(sandbox) == "stub-driver"
    # Under $HOME even when it is not on PATH, and created before the call: a
    # root-owned destination would send the installer through `sudo install`.
    bindir = sandbox / "home" / ".local" / "bin"
    assert installer_calls(sandbox, "install-mergiraf.sh") == [str(bindir)]
    assert bindir.is_dir()


def installer_calls(sandbox: Path, name: str) -> list[str]:
    record = sandbox / f"{name}-calls"
    if not record.exists():
        return []
    return record.read_text(encoding="utf-8").splitlines()


def test_setup_installs_lychee_under_home(sandbox: Path) -> None:
    """install-lychee.sh owns the pin, the digest and the does-this-repo-need-it
    decision, so setup.sh calls it unconditionally and only chooses where."""
    result = run_setup(sandbox, REGISTERS)

    assert result.returncode == 0, result.stderr
    assert installer_calls(sandbox, "install-lychee.sh") == [
        str(sandbox / "home" / ".local" / "bin")
    ]


def test_setup_completes_and_warns_when_lychee_is_not_the_pinned_binary(
    sandbox: Path,
) -> None:
    """An adopter whose lychee install failed still gets a configured checkout.
    The warn says what the hook does now, which is run an unvetted binary or
    refuse — not the install step that failed."""
    result = run_setup(sandbox, REGISTERS, lychee_body=FAILS)

    assert result.returncode == 0, result.stderr
    assert "unvetted binary or refuses to run" in result.stderr
    assert "Setup complete" in result.stdout


@pytest.mark.parametrize(
    "installer_body, expected_warning",
    [
        (FAILS, "mergiraf install failed"),
        (SUCCEEDS_WITHOUT_REGISTERING, "merge.mergiraf.driver is unset"),
    ],
    ids=["installer-fails", "installer-succeeds-without-registering"],
)
def test_setup_completes_and_warns_when_no_driver_is_bound(
    sandbox: Path, installer_body: str, expected_warning: str
) -> None:
    """An adopter without mergiraf still gets a configured checkout — it merges
    as it did before .gitattributes named the driver, and is told so."""
    result = run_setup(sandbox, installer_body)

    assert result.returncode == 0, result.stderr
    assert expected_warning in result.stderr
    assert registered_driver(sandbox) == ""
    assert "Setup complete" in result.stdout


def test_a_global_driver_does_not_answer_for_this_checkout(sandbox: Path) -> None:
    """install-mergiraf.sh binds the driver locally and nowhere else, so the
    post-condition must read the local scope. mergiraf's own setup docs tell users
    to register a global one, and it would otherwise silence the warning while
    merges ran through a binary this run never verified."""
    (sandbox / "gitconfig-global").write_text(
        '[merge "mergiraf"]\n\tdriver = global-driver\n', encoding="utf-8"
    )

    result = run_setup(sandbox, SUCCEEDS_WITHOUT_REGISTERING)

    assert result.returncode == 0, result.stderr
    assert "merge.mergiraf.driver is unset" in result.stderr
