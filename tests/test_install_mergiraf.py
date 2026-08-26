"""install-mergiraf.sh — the decisions every caller delegates to it rather than
re-deriving: whether the install is already done, whether the downloaded tarball
is the pinned one, and what `merge.mergiraf.driver` ends up bound to.

No test reaches the network. `curl` is stubbed throughout: to fail, so a run that
did NOT skip is a non-zero exit naming the stub; or to serve bytes the test
chose. Everything after it — the digest refusal, the PATH guard, the contract
probe, the `git config` pair — runs for real.
"""

import os
import subprocess
import tarfile
from pathlib import Path

import pytest

from tests._helpers import REPO_ROOT

INSTALLER = REPO_ROOT / ".github" / "scripts" / "install-mergiraf.sh"
PINNED_VERSION = "9.9.9"
DRIVER_TAIL = " merge --git %O %A %B -s %S -x %X -y %Y -p %P -t 30000"


def driver_value(binary: Path) -> str:
    """The value install-mergiraf.sh binds. The path is shell-quoted: git hands
    this to a shell, so a destination with a space would split into two words."""
    return f"'{binary}'{DRIVER_TAIL}"


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """A throwaway checkout holding the real installer, a pins file naming a
    version no release has, and a `mergiraf` at the destination that reports it."""
    (tmp_path / ".github" / "scripts").mkdir(parents=True)
    (tmp_path / ".github" / "scripts" / "install-mergiraf.sh").write_bytes(
        INSTALLER.read_bytes()
    )
    (tmp_path / ".github" / "tool-versions.sh").write_text(
        f"MERGIRAF_VERSION=v{PINNED_VERSION}\nMERGIRAF_SHA256_linux_amd64={'f' * 64}\n",
        encoding="utf-8",
    )
    (tmp_path / "bin").mkdir()
    # A stub `curl` ahead of the real one: reaching it means the skip did not fire.
    (tmp_path / "bin" / "curl").write_text(
        '#!/usr/bin/env bash\necho "curl-stub: the skip did not fire" >&2\nexit 1\n',
        encoding="utf-8",
    )
    (tmp_path / "bin" / "curl").chmod(0o755)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def install_binary(sandbox: Path, version: str) -> Path:
    dest = sandbox / "dest"
    dest.mkdir(exist_ok=True)
    binary = dest / "mergiraf"
    binary.write_text(
        f'#!/usr/bin/env bash\necho "mergiraf {version}"\n', encoding="utf-8"
    )
    binary.chmod(0o755)
    return dest


def git_env(sandbox: Path, path_prefix: Path | None = None) -> dict[str, str]:
    entries = [str(sandbox / "bin"), os.environ["PATH"]]
    if path_prefix is not None:
        entries.insert(0, str(path_prefix))
    return {
        **os.environ,
        "PATH": os.pathsep.join(entries),
        # A host with a global merge.mergiraf.driver — mergiraf's own setup docs
        # register one — would answer for this sandbox otherwise.
        "GIT_CONFIG_GLOBAL": str(sandbox / "gitconfig-global"),
        "GIT_CONFIG_SYSTEM": os.devnull,
    }


def bind_driver(sandbox: Path, value: str) -> None:
    subprocess.run(
        ["git", "config", "--local", "merge.mergiraf.driver", value],
        cwd=sandbox,
        check=True,
        env=git_env(sandbox),
    )


def read_driver(sandbox: Path, scope: str) -> str:
    result = subprocess.run(
        ["git", "config", scope, "--get", "merge.mergiraf.driver"],
        cwd=sandbox,
        capture_output=True,
        text=True,
        env=git_env(sandbox),
    )
    return result.stdout.strip()


def local_driver(sandbox: Path) -> str:
    return read_driver(sandbox, "--local")


def global_driver(sandbox: Path) -> str:
    return read_driver(sandbox, "--global")


def run_installer(
    sandbox: Path, dest: Path, path_prefix: Path | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", ".github/scripts/install-mergiraf.sh", str(dest)],
        cwd=sandbox,
        capture_output=True,
        text=True,
        env=git_env(sandbox, path_prefix),
    )


def test_skips_when_the_pinned_binary_is_installed_resolved_and_bound(
    sandbox: Path,
) -> None:
    dest = install_binary(sandbox, PINNED_VERSION)
    bind_driver(sandbox, driver_value(dest / "mergiraf"))

    result = run_installer(sandbox, dest, path_prefix=dest)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


@pytest.mark.parametrize(
    "installed_version, driver_dir, on_path",
    [
        ("0.0.1", "dest", True),
        (PINNED_VERSION, "elsewhere", True),
        (PINNED_VERSION, None, True),
        (PINNED_VERSION, "dest", False),
        (PINNED_VERSION, "stale-args", True),
    ],
    ids=[
        "stale-binary",
        "driver-names-another-path",
        "no-driver",
        "another-mergiraf-wins-on-path",
        "driver-carries-an-older-argument-string",
    ],
)
def test_reinstalls_when_the_pin_or_the_binding_does_not_match(
    sandbox: Path, installed_version: str, driver_dir: str | None, on_path: bool
) -> None:
    """Each arm is a state where the destination's binary is not provably the one
    this checkout merges through, so the download must be attempted. Two are the
    environment or the config changing under a checkout the first three call
    settled: a foreign mergiraf ahead on PATH is what auto-resolve/prepare.sh
    would run, and a driver an older revision of this script bound names the
    right binary with arguments this revision no longer writes."""
    dest = install_binary(sandbox, installed_version)
    if driver_dir == "stale-args":
        bind_driver(sandbox, f"'{dest / 'mergiraf'}' merge --git %O %A %B -t 5")
    elif driver_dir is not None:
        bind_driver(sandbox, driver_value(sandbox / driver_dir / "mergiraf"))
    if not on_path:
        foreign = sandbox / "bin" / "mergiraf"
        foreign.write_text(
            f'#!/usr/bin/env bash\necho "mergiraf {PINNED_VERSION}"\n', encoding="utf-8"
        )
        foreign.chmod(0o755)

    result = run_installer(sandbox, dest, path_prefix=dest if on_path else None)

    assert result.returncode != 0
    assert "curl-stub: the skip did not fire" in result.stderr


# The binary the stubbed tarball unpacks to. `REJECTED` never has to answer,
# because a refusal fires before the contract probe; `ACCEPTED` satisfies both the
# version read and `solve -p`, which is what a successful install needs.
REJECTED_BINARY = "echo unused\n"
ACCEPTED_BINARY = (
    f'[[ "$1" = "--version" ]] && {{ echo "mergiraf {PINNED_VERSION}"; exit 0; }}\n'
    'printf \'{\\n  "a": 1,\\n  "b": 2,\\n  "c": 3\\n}\\n\'\n'
)


def stub_the_download(sandbox: Path, binary: str = REJECTED_BINARY) -> None:
    """Replace the network and the archive tools, so a run reaches the PATH guard.

    `sha256sum` is stubbed only because no real tarball is involved; the refusal
    it stands in for is driven for real by the digest-mismatch test below. Every
    refusal after the install is left in place for the tests here to drive.
    """
    stubs = {
        # `-o <path>`: the tarball's bytes never matter, only that the file exists.
        "curl": 'while [[ $# -gt 1 ]]; do [[ "$1" = "-o" ]] && out="$2"; shift; done\n: >"$out"\n',
        "sha256sum": "exit 0\n",
        # `xzf <tarball> -C <workdir> mergiraf`
        "tar": 'while [[ $# -gt 1 ]]; do [[ "$1" = "-C" ]] && into="$2"; shift; done\n'
        "cat >\"${into}/mergiraf\" <<'FAKE'\n"
        f"#!/usr/bin/env bash\n{binary}"
        "FAKE\n"
        'chmod 0755 "${into}/mergiraf"\n',
        # On PATH and outside $dest — the state the guard exists to refuse.
        "mergiraf": 'echo "mergiraf 0.0.0"\n',
    }
    for name, body in stubs.items():
        stub = sandbox / "bin" / name
        stub.write_text(f"#!/usr/bin/env bash\n{body}", encoding="utf-8")
        stub.chmod(0o755)


@pytest.mark.parametrize("dest_name", ["dest", "dest with a space"])
def test_binds_the_driver_to_the_absolute_path_of_the_binary_it_installed(
    sandbox: Path, dest_name: str
) -> None:
    """Git config outlives any one shell's PATH, so the value names the binary
    rather than the bare command — a driver git cannot exec is a conflict it
    reports, not a fall back to the line merge. Git hands the value to a shell,
    so the path is quoted and a destination with a space still merges."""
    dest = sandbox / dest_name
    stub_the_download(sandbox, ACCEPTED_BINARY)

    result = run_installer(sandbox, dest, path_prefix=dest)

    assert result.returncode == 0, result.stderr
    assert local_driver(sandbox) == driver_value(dest / "mergiraf")


def test_refuses_and_unbinds_when_path_resolves_outside_the_destination(
    sandbox: Path,
) -> None:
    """The binary is installed by the time this fires, so a driver an earlier run
    bound would point every merge at the copy just rejected."""
    stub_the_download(sandbox)
    bind_driver(sandbox, driver_value(sandbox / "dest" / "mergiraf"))

    result = run_installer(sandbox, sandbox / "dest")

    assert result.returncode == 1
    assert "refusing to certify a binary this run did not verify" in result.stderr
    assert local_driver(sandbox) == ""


def test_a_refusal_leaves_a_global_driver_alone(sandbox: Path) -> None:
    """`--unset` writes to local whatever `--get` searched. An all-scope read here
    would find the global binding, unset nothing, and exit 5 instead of refusing."""
    stub_the_download(sandbox)
    subprocess.run(
        ["git", "config", "--global", "merge.mergiraf.driver", "global-driver"],
        cwd=sandbox,
        check=True,
        env=git_env(sandbox),
    )

    result = run_installer(sandbox, sandbox / "dest")

    assert result.returncode == 1
    assert local_driver(sandbox) == ""
    assert global_driver(sandbox) == "global-driver"


@pytest.mark.parametrize(
    "binary, expected",
    [
        ("exit 1\n", "exited non-zero on a conflict it must resolve"),
        ('cat "$3"\n', "reported success without merging both sides"),
    ],
    ids=["solve-exits-non-zero", "solve-leaves-the-markers"],
)
def test_a_failed_contract_probe_unbinds_the_driver(
    sandbox: Path, binary: str, expected: str
) -> None:
    """The binary is installed before the probe runs, so a driver an earlier run
    bound would point every merge at the copy the probe just rejected."""
    dest = sandbox / "dest"
    stub_the_download(sandbox, binary)
    bind_driver(sandbox, driver_value(dest / "mergiraf"))

    result = run_installer(sandbox, dest, path_prefix=dest)

    assert result.returncode == 1
    assert expected in result.stderr
    assert local_driver(sandbox) == ""


def test_a_digest_mismatch_refuses_before_installing(sandbox: Path) -> None:
    """The pinned digest is the supply-chain anchor, so a tarball that does not
    match it must abort before anything reaches the destination.

    `sha256sum` and `tar` are the real ones here — only `curl` is stubbed, and it
    serves a WELL-FORMED tarball carrying a working `mergiraf`. That is what
    makes the refusal the only thing standing between this run and an installed
    binary: deleting the digest check leaves `tar` and `install` both succeeding.
    """
    (sandbox / ".github" / "tool-versions.sh").write_text(
        f"MERGIRAF_VERSION=v{PINNED_VERSION}\nMERGIRAF_SHA256_linux_amd64={'0' * 64}\n",
        encoding="utf-8",
    )
    served = sandbox / "served.tar.gz"
    payload = sandbox / "mergiraf"
    payload.write_text(f"#!/usr/bin/env bash\n{ACCEPTED_BINARY}", encoding="utf-8")
    payload.chmod(0o755)
    with tarfile.open(served, "w:gz") as archive:
        archive.add(payload, arcname="mergiraf")
    (sandbox / "bin" / "curl").write_text(
        "#!/usr/bin/env bash\n"
        'while [[ $# -gt 1 ]]; do [[ "$1" = "-o" ]] && out="$2"; shift; done\n'
        f'cp "{served}" "$out"\n',
        encoding="utf-8",
    )
    dest = sandbox / "dest"

    result = run_installer(sandbox, dest, path_prefix=dest)

    assert result.returncode != 0
    assert not (dest / "mergiraf").exists()
    assert local_driver(sandbox) == ""
