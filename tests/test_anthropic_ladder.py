"""Behavioral tests for .github/scripts/lib/anthropic-ladder.bash.

Each test runs real bash sourcing the library with a stubbed `curl` on PATH:
the stub pops the next HTTP code from a control file, writes a canned response
body, and records its full argv, so rung-stepping, retries, and header
selection are asserted from observed process behavior. `sleep` is stubbed so
retry backoff costs no wall-clock.
"""

import os
import subprocess
from pathlib import Path

from tests._helpers import REPO_ROOT

LIB_DIR = REPO_ROOT / ".github" / "scripts" / "lib"

CURL_STUB = """#!/usr/bin/env bash
set -u
printf '%s\\n' "$*" >>"$STUB_DIR/curl-args.log"
out=""
prev=""
for a in "$@"; do
  [[ "$prev" == "-o" ]] && out="$a"
  prev="$a"
done
code=$(head -n1 "$STUB_DIR/codes")
if [[ -z "$code" ]]; then
  echo "curl stub: ran out of scripted HTTP codes" >&2
  exit 97
fi
tail -n +2 "$STUB_DIR/codes" >"$STUB_DIR/codes.next"
mv "$STUB_DIR/codes.next" "$STUB_DIR/codes"
if [[ "$code" == "200" ]]; then
  printf '%s' '{"content":[{"type":"text","text":"ok"}]}' >"$out"
else
  printf '%s' "{\\"error\\":{\\"message\\":\\"stub $code\\"}}" >"$out"
fi
printf '%s' "$code"
"""

DRIVER = """set -euo pipefail
source "$LIB_DIR/retry.bash"
source "$LIB_DIR/anthropic-ladder.bash"
anthropic_messages '{"model":"m"}' "$1"
"""


def _write_stub(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def _run_messages(
    tmp_path: Path, creds: dict[str, str], codes: list[str]
) -> tuple[subprocess.CompletedProcess, list[str], Path]:
    """Run anthropic_messages with stubbed curl/sleep; return (proc, curl argv
    lines, response file path)."""
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir(exist_ok=True)
    _write_stub(stub_dir / "curl", CURL_STUB)
    _write_stub(stub_dir / "sleep", "#!/usr/bin/env bash\nexit 0\n")
    (stub_dir / "codes").write_text("".join(f"{c}\n" for c in codes))
    args_log = stub_dir / "curl-args.log"
    args_log.write_text("")
    response_file = tmp_path / "response.json"

    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("CLAUDE_CODE_OAUTH_TOKEN")
    }
    env["PATH"] = f"{stub_dir}{os.pathsep}{env['PATH']}"
    env["STUB_DIR"] = str(stub_dir)
    env["LIB_DIR"] = str(LIB_DIR)
    env.update(creds)

    proc = subprocess.run(
        ["bash", "-c", DRIVER, "driver", str(response_file)],
        env=env,
        capture_output=True,
        text=True,
    )
    calls = [line for line in args_log.read_text().splitlines() if line]
    return proc, calls, response_file


def _run_ladder_listing(creds: dict[str, str]) -> list[str]:
    """Print claude_oauth_ladder's output under exactly `creds` configured."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("CLAUDE_CODE_OAUTH_TOKEN")
    }
    env.update(creds)
    proc = subprocess.run(
        [
            "bash",
            "-c",
            'set -euo pipefail; source "$1/retry.bash"; '
            'source "$1/anthropic-ladder.bash"; claude_oauth_ladder',
            "lister",
            str(LIB_DIR),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.splitlines()


def test_401_on_rung_one_steps_to_rung_two_and_succeeds(tmp_path: Path) -> None:
    proc, calls, response_file = _run_messages(
        tmp_path,
        {
            "CLAUDE_CODE_OAUTH_TOKEN_FALLBACK": "sk-ant-oat-first",
            "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-second",
        },
        ["401", "200"],
    )
    assert proc.returncode == 0, proc.stderr
    assert len(calls) == 2, calls
    assert "sk-ant-oat-first" in calls[0]
    assert "sk-ant-oat-second" in calls[1]
    assert "was rejected (HTTP 401)" in proc.stderr
    assert '"content"' in response_file.read_text()


def test_persistent_429_exhausts_three_attempts_then_steps_rung(tmp_path: Path) -> None:
    proc, calls, _ = _run_messages(
        tmp_path,
        {
            "CLAUDE_CODE_OAUTH_TOKEN_FALLBACK": "sk-ant-oat-first",
            "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-second",
        },
        ["429", "429", "429", "200"],
    )
    assert proc.returncode == 0, proc.stderr
    assert len(calls) == 4, calls
    assert all("sk-ant-oat-first" in c for c in calls[:3])
    assert "sk-ant-oat-second" in calls[3]
    assert "still rate-limited after 3 attempts" in proc.stderr


def test_400_fails_immediately_without_touching_rung_two(tmp_path: Path) -> None:
    proc, calls, _ = _run_messages(
        tmp_path,
        {
            "CLAUDE_CODE_OAUTH_TOKEN_FALLBACK": "sk-ant-oat-first",
            "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-second",
        },
        ["400"],
    )
    assert proc.returncode == 1
    assert len(calls) == 1, calls
    assert "rejected the request (HTTP 400)" in proc.stderr
    assert "sk-ant-oat-second" not in "".join(calls)


def test_empty_middle_rung_is_skipped(tmp_path: Path) -> None:
    proc, calls, _ = _run_messages(
        tmp_path,
        {
            "CLAUDE_CODE_OAUTH_TOKEN_FALLBACK": "sk-ant-oat-first",
            "CLAUDE_CODE_OAUTH_TOKEN_FALLBACK_3": "sk-ant-oat-third",
        },
        ["401", "200"],
    )
    assert proc.returncode == 0, proc.stderr
    assert len(calls) == 2, calls
    assert "sk-ant-oat-third" in calls[1]


def test_duplicate_credential_collapses_to_one_rung() -> None:
    rungs = _run_ladder_listing(
        {
            "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-same",
            "CLAUDE_CODE_OAUTH_TOKEN_FALLBACK": "sk-ant-oat-same",
            "CLAUDE_CODE_OAUTH_TOKEN_FALLBACK_2": "sk-ant-oat-other",
        }
    )
    assert rungs == ["sk-ant-oat-same", "sk-ant-oat-other"]


def test_oauth_token_gets_bearer_and_beta_headers(tmp_path: Path) -> None:
    proc, calls, _ = _run_messages(
        tmp_path, {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-tok"}, ["200"]
    )
    assert proc.returncode == 0, proc.stderr
    assert "authorization: Bearer sk-ant-oat-tok" in calls[0]
    assert "anthropic-beta: oauth-2025-04-20" in calls[0]
    assert "x-api-key" not in calls[0]


def test_metered_key_under_oauth_var_gets_x_api_key_and_warns(tmp_path: Path) -> None:
    proc, calls, _ = _run_messages(
        tmp_path, {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-api-key"}, ["200"]
    )
    assert proc.returncode == 0, proc.stderr
    assert "x-api-key: sk-ant-api-key" in calls[0]
    assert "authorization: Bearer" not in calls[0]
    assert "anthropic-beta" not in calls[0]
    assert "metered Anthropic API key" in proc.stderr


def test_anthropic_api_key_var_is_not_a_rung(tmp_path: Path) -> None:
    """The ladder walks subscription tokens only; a metered credit spend must
    be a loud refusal, never a silent fallback."""
    proc, calls, _ = _run_messages(
        tmp_path, {"ANTHROPIC_API_KEY": "sk-ant-api-key"}, []
    )
    assert proc.returncode == 1
    assert calls == []
    assert "no Anthropic credential is configured" in proc.stderr


def test_no_credentials_exits_one_naming_the_vars(tmp_path: Path) -> None:
    proc, calls, _ = _run_messages(tmp_path, {}, [])
    assert proc.returncode == 1
    assert calls == []
    assert "no Anthropic credential is configured" in proc.stderr
    assert "CLAUDE_CODE_OAUTH_TOKEN" in proc.stderr


def test_retry_cmd_rejects_max_zero_with_status_two() -> None:
    proc = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1/retry.bash"; retry_cmd 0 0 true; echo "rc=$?"',
            "guard",
            str(LIB_DIR),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "rc=2" in proc.stdout
    assert "MAX must be at least 1" in proc.stderr
