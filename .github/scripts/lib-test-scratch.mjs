// PROBLEM CLASS — a test scratch directory that outlives the test process.
// `mkdtempSync` in a test leaves the directory behind unless the test removes
// it, and a test that throws never reaches its own cleanup. One suite run then
// leaks a directory per case; repeated runs filled a container's whole disk
// allowance with ~25000 of them. `scratchDir` makes the removal unconditional:
// it registers on the process's exit, so a thrown assertion, a `--test-only`
// filter and a normal pass all clean up the same way.

import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const created = [];

// node:test runs each test FILE in its own process, so this fires at the end of
// one file's run rather than at the end of the whole suite.
process.on("exit", () => {
  for (const dir of created) rmSync(dir, { recursive: true, force: true });
});

/**
 * A fresh temporary directory, removed when this process exits.
 * @param {string} prefix the mkdtemp prefix, e.g. "auto-resolve-land-".
 * @returns {string} the absolute path of the new directory.
 */
export function scratchDir(prefix) {
  const dir = mkdtempSync(join(tmpdir(), prefix));
  created.push(dir);
  return dir;
}
