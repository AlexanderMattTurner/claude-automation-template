"""The auto-merge predicate decides whether a template sync may land with nobody
reading it.

Every case here is a REFUSAL case except one. That asymmetry is the point: the
failure this guards is a bad template change propagating unattended across every
downstream repo at once, so the predicate is conjunctive and each clause is
tested for on its own. A clause that silently stopped being checked would not
show up as a failure anywhere else — the PR would just merge.
"""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(
    subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
)
SCRIPT = REPO_ROOT / ".github" / "scripts" / "template-sync-automerge.sh"

CLEAN = {
    "PR_NUMBER": "1",
    "HAS_CONFLICTS": "false",
    "HAS_DELETIONS": "false",
    "HAS_DOWNGRADES": "false",
    "ALL_DETERMINISTIC": "",
    "CHANGED_PATHS": "README.md docs/thing.md",
}


def run(tmp_path: Path, **overrides: str):
    """Run the predicate with a fake `gh` that records its argv, so a test can
    tell "refused" from "armed" by whether gh was called at all."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "gh-calls.txt"
    (bin_dir / "gh").write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        # The read-back the script performs after arming.
        'if [[ "$1 $2" == "pr view" ]]; then echo true; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    (bin_dir / "gh").chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/local/bin",
        "GH_TOKEN": "x",
        "GH_REPO": "o/r",
        **CLEAN,
        **overrides,
    }
    res = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=REPO_ROOT,
    )
    calls = log.read_text(encoding="utf-8") if log.exists() else ""
    return res, calls


def test_a_clean_sync_arms_auto_merge(tmp_path: Path):
    res, calls = run(tmp_path)
    assert res.returncode == 0, res.stderr
    assert "--auto" in calls, "a fully clean sync must arm auto-merge"


def test_a_model_resolved_conflict_refuses(tmp_path: Path):
    # A model resolution is exactly the class the merge-delta reviewer exists to
    # catch, so it must not also be the class that merges unattended.
    res, calls = run(tmp_path, HAS_CONFLICTS="true", ALL_DETERMINISTIC="false")
    assert res.returncode == 0
    assert "--auto" not in calls
    assert "needed a model" in res.stdout


def test_a_structurally_resolved_conflict_still_arms(tmp_path: Path):
    # mergiraf's result is reproducible from the same inputs by anyone, which is
    # what makes it safe to land unattended.
    res, calls = run(tmp_path, HAS_CONFLICTS="true", ALL_DETERMINISTIC="true")
    assert res.returncode == 0
    assert "--auto" in calls


def test_an_adopter_downgrade_refuses(tmp_path: Path):
    # The sync's merge base is a single repo-wide .template-version, so a "clean"
    # merge can silently drop local lines. Merging that unattended is how a
    # downstream customization disappears with no diff anybody read.
    res, calls = run(tmp_path, HAS_DOWNGRADES="true")
    assert "--auto" not in calls
    assert "dropped lines" in res.stdout


def test_a_template_deletion_refuses(tmp_path: Path):
    res, calls = run(tmp_path, HAS_DELETIONS="true")
    assert "--auto" not in calls
    assert "deleted files" in res.stdout


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/lint.yaml",
        ".claude/hooks/thing.mjs",
        ".claude/rules/python-style.md",
    ],
)
def test_a_supervision_surface_change_refuses(tmp_path: Path, path: str):
    # Sync PRs routinely touch these. Letting them merge unattended is the
    # supervision stack approving changes to itself.
    res, calls = run(tmp_path, CHANGED_PATHS=f"README.md {path}")
    assert "--auto" not in calls, f"{path} must not auto-merge"
    assert "supervision surface" in res.stdout


def test_a_rejected_arming_fails_loud(tmp_path: Path):
    # The enable mutation can be refused (auto-merge off for the repository) and
    # still exit 0. Trusting that leaves a PR nobody waits on and nobody merges.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "gh").write_text(
        '#!/usr/bin/env bash\nif [[ "$1 $2" == "pr view" ]]; then echo false; fi\nexit 0\n',
        encoding="utf-8",
    )
    (bin_dir / "gh").chmod(0o755)
    res = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/local/bin",
            "GH_TOKEN": "x",
            "GH_REPO": "o/r",
            **CLEAN,
        },
    )
    assert res.returncode != 0
    assert "did not stick" in res.stdout
