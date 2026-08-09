"""A `gh` CLI stub for tests that drive a script's `gh api` calls.

The scripts under test shell out to `gh api <path>`, so the honest seam is the
CLI itself: put a stub first on PATH and the real command is never reached, with
no network and no token. The stub answers from a JSON table the test writes, and
exits non-zero for a path the table does not model — which is what lets a test
assert the caller's fallback on an API fault.
"""

import json
from pathlib import Path

STUB = """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

TABLE = json.loads(Path(__file__).with_name("gh-table.json").read_text())

if len(sys.argv) < 3 or sys.argv[1] != "api":
    sys.stderr.write("fake gh: only `gh api <path>` is modelled\\n")
    raise SystemExit(1)
path = sys.argv[2]
for prefix, body in TABLE:
    if path.startswith(prefix):
        print(json.dumps(body))
        raise SystemExit(0)
sys.stderr.write(f"gh: Not Found (HTTP 404) for {path}\\n")
raise SystemExit(1)
"""


def write_gh_stub(bindir: Path, routes: list[tuple[str, object]]) -> Path:
    """Install a `gh` stub in `bindir` answering `routes` (path prefix, body).

    Prefixes are tried in order, so a test lists the most specific first.
    """
    bindir.mkdir(parents=True, exist_ok=True)
    (bindir / "gh-table.json").write_text(json.dumps(routes), encoding="utf-8")
    stub = bindir / "gh"
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(0o755)
    return stub


def workflow_runs(runs: list[tuple[int, str, str]]) -> dict:
    """A `GET .../workflows/<file>/runs` body from (id, head_sha, started_at)."""
    return {
        "workflow_runs": [
            {"id": run_id, "head_sha": head, "run_started_at": started}
            for run_id, head, started in runs
        ]
    }


def run_jobs(jobs: list[tuple[str, str]]) -> dict:
    """A `GET .../runs/<id>/jobs` body from (job name, conclusion) pairs."""
    return {"jobs": [{"name": name, "conclusion": end} for name, end in jobs]}
