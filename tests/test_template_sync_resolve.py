"""template-sync-resolve.sh installs the claude CLI only when the model tier runs.

fanout.py shells out to `claude`, which the runner image does not provide. The
install runs inside the model tier, so a sync that mergiraf settles, or that has
no token, never depends on an npm download. A failed install costs the model
tier alone: the script still exits 0 and reports the file unresolved, so the
marker gate restores it instead of the whole resolve step being skipped.

The script and its lib/ run from a scratch copy beside a stub
install-claude-cli.sh, with a stub mergiraf and a stub fanout.py, so each
tier's choice is observed without a network or a model.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._helpers import REPO_ROOT

SCRIPTS = REPO_ROOT / ".github" / "scripts"
CONFLICTED = "<<<<<<< local\nmine\n=======\ntheirs\n>>>>>>> template\n"

STUB_INSTALLER = """#!/usr/bin/env bash
echo install >>"$CALLS"
exit "$INSTALL_EXIT"
"""
STUB_MERGIRAF = """#!/usr/bin/env bash
echo merged
"""
STUB_FANOUT = """import os
with open(os.environ["CALLS"], "a") as calls:
    calls.write("fanout\\n")
for path in os.environ["CONFLICT_LIST"].split():
    with open(path, "w") as f:
        f.write("resolved\\n")
"""


def run_resolve(tmp_path: Path, *, mergiraf: bool, token: str, install_exit: int):
    scripts = tmp_path / "scripts"
    shutil.copytree(SCRIPTS / "lib", scripts / "lib")
    shutil.copy(SCRIPTS / "template-sync-resolve.sh", scripts)
    (scripts / "install-claude-cli.sh").write_text(STUB_INSTALLER, encoding="utf-8")
    resolver = tmp_path / "resolver"
    (resolver / "auto-resolve").mkdir(parents=True)
    (resolver / "auto-resolve" / "fanout.py").write_text(STUB_FANOUT, encoding="utf-8")
    mergiraf_bin = tmp_path / "mergiraf"
    mergiraf_bin.write_text(STUB_MERGIRAF, encoding="utf-8")
    mergiraf_bin.chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()
    (work / "f.txt").write_text(CONFLICTED, encoding="utf-8")
    calls = tmp_path / "calls"
    calls.touch()
    output = tmp_path / "output"
    result = subprocess.run(
        ["bash", str(scripts / "template-sync-resolve.sh")],
        cwd=work,
        env={
            **os.environ,
            "CONFLICT_FILES": "f.txt",
            "PR_NUMBER": "1",
            "RESOLVER_DIR": str(resolver),
            "GITHUB_OUTPUT": str(output),
            "CLAUDE_CODE_OAUTH_TOKEN": token,
            "MERGIRAF_BIN": str(mergiraf_bin) if mergiraf else "no-such-mergiraf",
            "CALLS": str(calls),
            "INSTALL_EXIT": str(install_exit),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    outputs = dict(
        line.split("=", 1)
        for line in output.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    return calls.read_text(encoding="utf-8").split(), outputs


@pytest.mark.parametrize(
    (
        "mergiraf",
        "token",
        "install_exit",
        "calls",
        "deterministic",
        "by_model",
        "unresolved",
    ),
    [
        # mergiraf settles everything: the model tier, and its install, never run.
        (True, "tok", 0, [], "f.txt", "", ""),
        # No token: the model tier is skipped, so nothing is installed.
        (False, "", 0, [], "", "", "f.txt"),
        # The install fails: fanout is not run and the file stays unresolved.
        (False, "tok", 1, ["install"], "", "", "f.txt"),
        # The install succeeds: fanout runs and resolves the file.
        (False, "tok", 0, ["install", "fanout"], "", "f.txt", ""),
    ],
)
def test_the_claude_cli_is_installed_only_when_the_model_tier_runs(
    tmp_path, mergiraf, token, install_exit, calls, deterministic, by_model, unresolved
):
    seen, outputs = run_resolve(
        tmp_path, mergiraf=mergiraf, token=token, install_exit=install_exit
    )
    assert seen == calls
    assert (outputs["deterministic"], outputs["by_model"], outputs["unresolved"]) == (
        deterministic,
        by_model,
        unresolved,
    )
