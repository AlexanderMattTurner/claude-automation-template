import { strict as assert } from "node:assert";
import { spawnSync } from "node:child_process";
import {
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const TEMPLATE_SYNC_YAML = join(
  REPO_ROOT,
  ".github",
  "workflows",
  "template-sync.yaml",
);

// SYNC_PATHS is the whole contract: a consumer receives these paths and nothing
// else. Reading it here rather than restating the list is what makes the tests
// below fail when a synced file's data dependency stops being delivered.
function syncPaths() {
  const yaml = readFileSync(TEMPLATE_SYNC_YAML, "utf8");
  const match = /^\s*SYNC_PATHS:\s*"(?<paths>[^"]*)"/m.exec(yaml);
  assert.ok(match, "template-sync.yaml declares SYNC_PATHS");
  return match.groups.paths.split(/\s+/).filter(Boolean);
}

// The tree a consumer actually gets: every SYNC_PATHS entry copied out of the
// template, and nothing outside them. An installer that reads a pin the sync
// does not deliver dies here exactly as it dies in the consumer's CI.
function consumerTree() {
  const root = mkdtempSync(join(tmpdir(), "consumer-"));
  for (const path of syncPaths()) {
    mkdirSync(join(root, dirname(path)), { recursive: true });
    cpSync(join(REPO_ROOT, path), join(root, path), { recursive: true });
  }
  return root;
}

function stubDir(root, stubs) {
  const dir = join(root, "stub");
  mkdirSync(dir, { recursive: true });
  for (const [name, body] of Object.entries(stubs)) {
    writeFileSync(join(dir, name), `#!/bin/sh\n${body}\n`, { mode: 0o755 });
  }
  return dir;
}

function runInstaller(root, script, stubs, args = []) {
  const dir = stubDir(root, stubs);
  return spawnSync(
    "bash",
    [join(root, ".github", "scripts", script), ...args],
    {
      encoding: "utf8",
      env: { ...process.env, PATH: `${dir}:${process.env.PATH}` },
    },
  );
}

test("template-sync delivers the mergiraf pin install-mergiraf reads", () => {
  const root = consumerTree();
  try {
    const run = runInstaller(
      root,
      "install-mergiraf.sh",
      { curl: 'echo "REACHED-DOWNLOAD" >&2\nexit 42' },
      [join(root, "dest")],
    );
    assert.doesNotMatch(run.stderr, /No such file or directory/);
    assert.match(run.stderr, /REACHED-DOWNLOAD/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

// The CLI pin is consumer-owned: each repo's Dependabot bumps it, so the sync
// must not deliver it. The installer reads the copy the consumer supplies, and
// refuses by name when there is none.
const CLI_PIN = join(".github", "claude-cli", "package.json");
const CLI_STUBS = {
  npm: 'echo "REACHED-INSTALL $*" >&2\nexit 0',
  claude: 'echo "2.0.0"',
};

test("install-claude-cli installs the version the consumer's pin names", () => {
  const root = consumerTree();
  try {
    mkdirSync(join(root, dirname(CLI_PIN)), { recursive: true });
    cpSync(join(REPO_ROOT, CLI_PIN), join(root, CLI_PIN));
    const pinned = JSON.parse(readFileSync(join(REPO_ROOT, CLI_PIN), "utf8"))
      .dependencies["@anthropic-ai/claude-code"];
    const run = runInstaller(root, "install-claude-cli.sh", CLI_STUBS);
    assert.equal(run.status, 0, run.stderr);
    assert.ok(
      run.stderr.includes(
        `REACHED-INSTALL install -g @anthropic-ai/claude-code@${pinned}\n`,
      ),
      run.stderr,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("install-claude-cli refuses by name when the consumer has no pin", () => {
  const root = consumerTree();
  try {
    assert.ok(
      !existsSync(join(root, CLI_PIN)),
      "template-sync delivers no CLI pin",
    );
    const run = runInstaller(root, "install-claude-cli.sh", CLI_STUBS);
    assert.equal(run.status, 1);
    assert.match(run.stderr, /claude-cli\/package\.json/);
    assert.doesNotMatch(run.stderr, /REACHED-INSTALL/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

// The commit-msg hook passes this path to commitlint with no fallback, and the
// hook runs under `set -euo pipefail`. Undelivered, every commit in the consumer
// fails with a commitlint ENOENT rather than a message about the hook.
test("template-sync delivers the commitlint config the commit-msg hook names", () => {
  const hook = readFileSync(join(REPO_ROOT, ".hooks", "commit-msg"), "utf8");
  const declared = /^config="(?<path>[^"]+)"$/m.exec(hook);
  assert.ok(declared, ".hooks/commit-msg declares its commitlint config path");

  const root = consumerTree();
  try {
    assert.ok(
      existsSync(join(root, declared.groups.path)),
      `${declared.groups.path} is read by .hooks/commit-msg but no SYNC_PATHS entry delivers it`,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
