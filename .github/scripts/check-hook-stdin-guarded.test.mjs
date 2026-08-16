// Behavior test for the isMain `readStdinJson()` try-guard lint. Drives the
// guard's REAL detection (`findProblems`) over fixture sources and asserts the
// observable verdict (the exact 1-based line numbers), plus the CLI's exit code
// and the real `.claude/hooks/*.mjs` tree.
//
// Non-vacuity: the three cases under "AST regressions" each FAIL against the
// retired text-scanning implementation (a brace/`try` token counter), so a
// regression to text matching turns them red rather than passing silently.

import test from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { tmpdir } from "node:os";
import { mkdtempSync } from "node:fs";

import { findProblems, main } from "./check-hook-stdin-guarded.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(here, "..", "..");

const lines = (src) => findProblems(src, "fixture.mjs");

const UNGUARDED = `import { isMain, readStdinJson } from "./lib-hook-io.mjs";
if (isMain(import.meta.url)) {
  const input = await readStdinJson();
  process.stdout.write(JSON.stringify(input));
}
`;

const GUARDED_TRY = `import { isMain, readStdinJson } from "./lib-hook-io.mjs";
if (isMain(import.meta.url)) {
  try {
    const input = await readStdinJson();
    process.stdout.write(JSON.stringify(input));
  } catch {
    process.exit(0);
  }
}
`;

const GUARDED_SINGLE_STATEMENT = `if (isMain(import.meta.url))
  try {
    const input = await readStdinJson();
  } catch (err) {
    process.exit(0);
  }
`;

const RUNJUDGECLI = `if (isMain(import.meta.url)) {
  await runJudgeCli("gate", judge, { transformInput: withDefault });
}
`;

const BARE_REFERENCE = `if (isMain(import.meta.url)) {
  void main(readStdinJson, (chunk) => process.stdout.write(chunk));
}
`;

const NO_ISMAIN = `export async function readStdinJson(maxBytes = 1024) {
  return JSON.parse(await read());
}
`;

const CALL_BEFORE_ISMAIN_ONLY = `async function helper() {
  return await readStdinJson();
}
if (isMain(import.meta.url)) {
  await runJudgeCli("gate", judge);
}
`;

// --- positive: an unguarded call is flagged at its exact line ----------------

test("flags an unguarded call at its line", () => {
  assert.deepEqual(lines(UNGUARDED), [3]);
});

test("flags the call that sits after the try block closed", () => {
  const src = `if (isMain(import.meta.url)) {
  try {
    const a = await readStdinJson();
  } catch {}
  const b = await readStdinJson();
}
`;
  // First call guarded (line 3); second sits after the try closed (line 5).
  assert.deepEqual(lines(src), [5]);
});

// A `catch`/`finally` body is NOT protected by its own statement's handler.
test("flags a call inside the catch clause of the try meant to guard it", () => {
  const src = `if (isMain(import.meta.url)) {
  try {
    noop();
  } catch {
    const retry = await readStdinJson();
  }
}
`;
  assert.deepEqual(lines(src), [5]);
});

// --- negative: every compliant idiom is accepted ------------------------------

for (const [name, src] of Object.entries({
  GUARDED_TRY,
  GUARDED_SINGLE_STATEMENT,
  RUNJUDGECLI,
  BARE_REFERENCE,
  NO_ISMAIN,
  CALL_BEFORE_ISMAIN_ONLY,
})) {
  test(`accepts the compliant shape ${name}`, () => {
    assert.deepEqual(lines(src), []);
  });
}

// --- AST regressions: each case is a misfire of the retired text scan ---------

test("a `try {` inside a COMMENT does not open a phantom try (false negative)", () => {
  const src = `if (isMain(import.meta.url)) {
  // A design note that spells the words \`try {\` in prose.
  const input = await readStdinJson();
}
`;
  assert.deepEqual(lines(src), [3]);
});

test("a `try {` inside a STRING does not open a phantom try (false negative)", () => {
  const src = `if (isMain(import.meta.url)) {
  const HELP = "wrap the body in try { to be safe";
  const input = await readStdinJson();
}
`;
  assert.deepEqual(lines(src), [3]);
});

test('a "}" string literal does not close the real try early (false positive)', () => {
  const src = `if (isMain(import.meta.url)) {
  try {
    const closer = "}";
    const input = await readStdinJson();
  } catch {}
}
`;
  assert.deepEqual(lines(src), []);
});

// A hook may carry several entry-point guards; each one is scanned.
test("inspects every isMain block in a file, not only the first", () => {
  const src = `if (isMain(import.meta.url)) {
  try {
    await readStdinJson();
  } catch {}
}
if (isMain(import.meta.url)) {
  await readStdinJson();
}
`;
  assert.deepEqual(lines(src), [7]);
});

// --- CLI + the real tree ------------------------------------------------------

test("main() returns 1 for an unguarded file and 0 for a guarded one", () => {
  const dir = mkdtempSync(join(tmpdir(), "stdin-guard-"));
  const bad = join(dir, "bad.mjs");
  const good = join(dir, "good.mjs");
  writeFileSync(bad, UNGUARDED);
  writeFileSync(good, GUARDED_TRY);
  assert.equal(main([bad]), 1);
  assert.equal(main([good]), 0);
});

test("every real hook complies", () => {
  const hooksDir = join(REPO_ROOT, ".claude", "hooks");
  const offenders = {};
  let inspected = 0;
  for (const name of readdirSync(hooksDir).sort()) {
    if (!name.endsWith(".mjs") || name.endsWith(".test.mjs")) continue;
    inspected += 1;
    const hits = findProblems(readFileSync(join(hooksDir, name), "utf8"), name);
    if (hits.length > 0) offenders[name] = hits;
  }
  // A glob that matched nothing would report a vacuous "clean".
  assert.ok(inspected > 0, "no hooks were inspected");
  assert.deepEqual(offenders, {});
});
