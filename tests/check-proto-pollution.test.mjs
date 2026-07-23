// Behavior test for the prototype-pollution guard. Drives the guard's REAL
// detection (`findProblems`) over fixture sources and asserts the observable
// verdict (flagged vs not) plus the exact finding text, and runs the CLI over
// the current tree. Non-vacuous: every positive case asserts the specific
// message a no-op checker could not produce, so disabling detection turns each
// red green and fails the test.

import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  findProblems,
  isScannable,
  scanDirs,
  DEFAULT_SCAN_DIRS,
} from "../.github/scripts/check-proto-pollution.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const SCRIPT = join(
  here,
  "..",
  ".github",
  "scripts",
  "check-proto-pollution.mjs",
);

const flags = (src) => findProblems(src, "fixture.mjs").length > 0;

// --- positive: a `{}` dynamic-key map with a non-literal key IS flagged -------

test("flags a plain-object map written with a computed key, naming the var and line", () => {
  const problems = findProblems(
    "const m = {};\nm[userKey] = v;\n",
    "fixture.mjs",
  );
  assert.equal(problems.length, 1);
  // The specific message a no-op checker cannot fabricate — pins non-vacuity.
  assert.match(problems[0], /^fixture\.mjs:2: /);
  assert.match(problems[0], /`m` is a plain object/);
  assert.match(problems[0], /computed key `userKey`/);
  assert.match(problems[0], /__proto__/);
  assert.match(problems[0], /Object\.create\(null\)` or `new Map\(\)/);
});

test("flags a `= {}` parameter default written by computed key", () => {
  const problems = findProblems(
    "function f(m = {}) { m[k] = v; }\n",
    "fixture.mjs",
  );
  assert.equal(problems.length, 1);
  assert.match(problems[0], /`m` is a plain object/);
});

test("flags a member-expression key (not just a bare identifier key)", () => {
  const problems = findProblems(
    "const m = {};\nm[obj.field] = v;\n",
    "fixture.mjs",
  );
  assert.equal(problems.length, 1);
  assert.match(problems[0], /computed key `obj\.field`/);
});

test("flags a compound-assignment computed write", () => {
  assert.equal(flags("const m = {};\nm[k] ??= v;\n"), true);
});

// --- negatives: the safe accumulator shapes are NOT flagged -------------------

test("does NOT flag Object.create(null) written by computed key", () => {
  assert.equal(
    flags("const m = Object.create(null);\nm[userKey] = v;\n"),
    false,
  );
});

test("does NOT flag a Map (populated via .set, not index assignment)", () => {
  assert.equal(flags("const m = new Map();\nm.set(userKey, v);\n"), false);
});

test("does NOT flag Object.defineProperty on a `{}` (the safe shape)", () => {
  const src =
    "const out = {};\n" +
    "Object.defineProperty(out, key, { value: v, enumerable: true });\n";
  assert.equal(flags(src), false);
});

test("does NOT flag a static-key-only object literal", () => {
  assert.equal(flags("const m = { a: 1, b: 2 };\nm.foo = 1;\n"), false);
});

test("does NOT flag a computed write whose key is a string/number literal", () => {
  assert.equal(flags('const m = {};\nm["a"] = 1;\n'), false);
  assert.equal(flags("const m = {};\nm[0] = 1;\n"), false);
  assert.equal(flags("const m = {};\nm[`static`] = 1;\n"), false);
});

test("does NOT flag a static property assignment `obj.foo = 1`", () => {
  assert.equal(flags("const m = {};\nm.foo = 1;\n"), false);
});

test("does NOT flag a computed write to an undeclared/global target", () => {
  assert.equal(flags("globalThing[k] = v;\n"), false);
});

test("does NOT flag a computed write to a member target (`a.b[k] = …`)", () => {
  assert.equal(flags("const o = {}; o.map = {};\no.map[k] = v;\n"), false);
});

// --- scope resolution: shadowing must not cross-contaminate -------------------

test("a same-named `{}` in another function does not taint an Object.create(null)", () => {
  const src =
    "function a(){ const m = Object.create(null); m[k] = v; }\n" +
    "function b(){ const m = {}; m.staticOnly = 1; }\n";
  assert.equal(flags(src), false);
});

test("an inner `{}` shadowing an outer safe binding IS flagged", () => {
  const src =
    "const m = Object.create(null);\n" +
    "function f(){ const m = {}; m[k] = v; }\n";
  const problems = findProblems(src, "fixture.mjs");
  assert.equal(problems.length, 1);
  assert.match(problems[0], /fixture\.mjs:2:/);
});

test("an inner safe binding shadowing an outer `{}` is NOT flagged", () => {
  const src =
    "const m = {};\n" + "function f(){ const m = new Map(); m.set(k, v); }\n";
  assert.equal(flags(src), false);
});

// --- suppression marker ------------------------------------------------------

test("a proto-pollution-ok marker with a reason on the write line exempts it", () => {
  const src =
    "const m = {};\nm[k] = v; // proto-pollution-ok: keys from a fixed allowlist\n";
  assert.equal(flags(src), false);
});

test("the marker on the line just above the write also exempts it", () => {
  const src =
    "const m = {};\n// proto-pollution-ok: trusted allowlist\nm[k] = v;\n";
  assert.equal(flags(src), false);
});

test("a marker with NO reason does not exempt (reason is mandatory)", () => {
  const src = "const m = {};\nm[k] = v; // proto-pollution-ok:\n";
  assert.equal(flags(src), true);
});

// --- string / comment / regex bodies never masquerade as a write --------------
// The AST parse means a `m[k]=v` inside a string or comment is inert; a textual
// heuristic could false-positive on these, so pin them.

test("does NOT flag a computed write appearing inside a string or comment", () => {
  assert.equal(flags('const s = "m[k] = v";\n'), false);
  assert.equal(flags("// const m = {}; m[k] = v\nconst x = 1;\n"), false);
});

// --- file selection: pin isScannable by observable outcome -------------------

test("isScannable includes the hook/CI-script surface", () => {
  for (const rel of [
    ".claude/hooks/parallelism-nudge.mjs",
    ".github/scripts/sanitize-pr-input.mjs",
    ".github/scripts/ci-failure-notify.js",
  ])
    assert.equal(isScannable(rel), true, rel);
});

test("isScannable excludes generated bundles, tests, fuzz files, and out-of-scope dirs", () => {
  for (const rel of [
    ".github/scripts/redact-output.bundle.mjs",
    ".claude/hooks/parallelism-nudge.test.mjs",
    ".claude/hooks/sanitize-output.fuzz.test.mjs",
    "bin/lib/tool.mjs",
    "tests/x.mjs",
  ])
    assert.equal(isScannable(rel), false, rel);
});

// --- integration: the CLI scans the real tree and finds it clean -------------
// The tree carries no prototype-pollution sites, so the CLI must exit 0 with no
// output. But a "clean" exit is also what a vacuous scan (a `**` git pathspec
// matching zero files) produces, so this is paired with a non-vacuity assertion:
// re-derive the scan set the CLI uses (git ls-files over SCAN_DIRS, filtered by
// the exported isScannable) and prove it is non-empty AND includes known hook
// files — so a too-broad exclusion that silently skips a real hook would fail
// here rather than masquerade as a clean tree.
test("CLI scans the real tree and finds it clean", () => {
  let stderr = "";
  let exitCode = 0;
  try {
    execFileSync("node", [SCRIPT], { stdio: ["pipe", "pipe", "pipe"] });
  } catch (err) {
    exitCode = err.status;
    stderr = String(err.stderr);
  }
  assert.equal(exitCode, 0, `expected a clean exit, got:\n${stderr}`);
});

test("the CLI's scan set is non-vacuous and covers the hook/CI-script surface", () => {
  const scanned = execFileSync(
    "git",
    ["ls-files", "-z", ".claude/hooks", ".github/scripts"],
    { encoding: "utf8", cwd: join(here, "..") },
  )
    .split("\0")
    .filter(Boolean)
    .filter(isScannable);
  assert.ok(scanned.length > 0, "scan set is empty — enumeration is vacuous");
  for (const f of [
    ".claude/hooks/lib-control-plane.mjs",
    ".github/scripts/sanitize-pr-input.mjs",
    ".github/scripts/select-resolvable-threads.mjs",
  ]) {
    assert.ok(scanned.includes(f), `scan set is missing ${f}`);
  }
});

// --- SCAN_DIRS extensibility (adopter override) ------------------------------

test("scanDirs always includes the shipped surfaces incl. .github/scripts", () => {
  const dirs = scanDirs({});
  assert.ok(dirs.includes(".claude/hooks"));
  assert.ok(dirs.includes(".github/scripts"));
  assert.deepEqual(dirs, DEFAULT_SCAN_DIRS);
});

test("CHECK_PROTO_SCAN_DIRS widens coverage without dropping defaults", () => {
  const dirs = scanDirs({ CHECK_PROTO_SCAN_DIRS: "src/sanitizers, lib/parse" });
  assert.ok(dirs.includes(".claude/hooks"));
  assert.ok(dirs.includes(".github/scripts"));
  assert.ok(dirs.includes("src/sanitizers"));
  assert.ok(dirs.includes("lib/parse"));
});

test("an override cannot remove a shipped surface, and dedups overlaps", () => {
  const dirs = scanDirs({ CHECK_PROTO_SCAN_DIRS: ".github/scripts extra/dir" });
  // .github/scripts listed once despite the override repeating it.
  assert.equal(dirs.filter((d) => d === ".github/scripts").length, 1);
  assert.ok(dirs.includes("extra/dir"));
});
