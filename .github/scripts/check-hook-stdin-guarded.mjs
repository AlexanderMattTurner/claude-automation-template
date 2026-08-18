#!/usr/bin/env node
// Require a `readStdinJson()` call in a hook's `isMain` block to sit inside a `try`.
//
// A hook's `if (isMain(import.meta.url)) { ... }` block is its CLI entry point.
// `await readStdinJson()` there REJECTS on empty or malformed stdin; an unhandled
// rejection exits the process non-zero, which Claude Code treats as NON-BLOCKING —
// the guarded tool call then proceeds UNGUARDED (fail OPEN) with no verdict
// emitted. Wrapping the call in a `try` lets the hook take its declared failure
// posture (deny/ask for a gate, silent exit for an advisory) instead of crashing
// open.
//
// The compliant hooks either route stdin through `runJudgeCli` (which owns the
// try) or wrap `readStdinJson()` directly. Either satisfies this check: a CALL
// inside an isMain block is a violation only when no `try` block encloses it.
// Passing `readStdinJson` as a bare reference (`main(readStdinJson, …)`) is not a
// call and is never flagged.
//
// The enclosing-`try` question is answered by walking a `typescript` AST (the
// package is already a dev dependency here, as the sibling check-proto-pollution.mjs
// shows), so a comment or a string carrying the word `try` cannot open a phantom
// one and a `"}"` string literal cannot close a real one early. That matters most
// in the false-NEGATIVE direction: the remedy this check prints tells authors to
// write the word `try`, so the token that would blind a text scan is the one its
// own message asks for. Every isMain block in a file is inspected, not just the
// first.
//
// Usage: `check-hook-stdin-guarded.mjs <hook.mjs> ...` (pre-commit passes the
// matched `.claude/hooks/*.mjs` non-test files).

import { readFileSync } from "node:fs";
import process from "node:process";
import ts from "typescript";

const READ_STDIN = "readStdinJson";

export const MESSAGE =
  "`readStdinJson()` in the isMain block is not inside a `try` — an empty or " +
  "malformed stdin rejects, the hook exits non-zero (non-blocking), and the " +
  "tool call proceeds UNGUARDED (fail OPEN).";

export const FIX =
  "fix: wrap it in a `try` or route stdin through `runJudgeCli`.";

// `isMain(import.meta.url)` — the hook entry-point guard this check scopes to.
/** @param {import("typescript").Node} node @returns {boolean} */
function isMainGuard(node) {
  return (
    ts.isCallExpression(node) &&
    ts.isIdentifier(node.expression) &&
    node.expression.text === "isMain" &&
    node.arguments.length === 1 &&
    node.arguments[0].getText().replace(/\s+/g, "") === "import.meta.url"
  );
}

// A CALL to readStdinJson, not a bare reference handed to something else.
/** @param {import("typescript").Node} node @returns {boolean} */
function isReadStdinCall(node) {
  return (
    ts.isCallExpression(node) &&
    ts.isIdentifier(node.expression) &&
    node.expression.text === READ_STDIN
  );
}

// True when some ancestor up to STOP is the TRY BLOCK of a try statement. The
// `tryBlock` test is what makes this precise: a call in a `catch` or `finally`
// clause is NOT protected by that statement's own handler, so only the try block
// counts.
/**
 * @param {import("typescript").Node} node
 * @param {import("typescript").Node} stop the ancestor to stop at
 * @returns {boolean}
 */
function insideTry(node, stop) {
  for (let cur = node; cur && cur !== stop; cur = cur.parent) {
    if (
      cur.parent &&
      ts.isTryStatement(cur.parent) &&
      cur.parent.tryBlock === cur
    ) {
      return true;
    }
  }
  return false;
}

/**
 * @param {import("typescript").Node} node
 * @param {(node: import("typescript").Node) => void} visit
 */
function walk(node, visit) {
  visit(node);
  ts.forEachChild(node, (child) => walk(child, visit));
}

// The 1-based line numbers of the unguarded `readStdinJson()` calls in SOURCE.
// Exported for the behavior test, which drives this rather than the CLI.
/**
 * @param {string} source the file's text.
 * @param {string} filename its path, used as the parser's file name.
 * @returns {number[]} 1-based line numbers, ascending.
 */
export function findProblems(source, filename) {
  const file = ts.createSourceFile(
    filename,
    source,
    ts.ScriptTarget.Latest,
    /* setParentNodes */ true,
    ts.ScriptKind.JS,
  );
  /** @type {import("typescript").Node[]} */
  const blocks = [];
  walk(file, (node) => {
    if (ts.isIfStatement(node) && isMainGuard(node.expression)) {
      blocks.push(node.thenStatement);
    }
  });
  /** @type {number[]} */
  const lines = [];
  for (const block of blocks) {
    walk(block, (node) => {
      if (isReadStdinCall(node) && !insideTry(node, block)) {
        lines.push(
          file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1,
        );
      }
    });
  }
  return [...new Set(lines)].sort((a, b) => a - b);
}

/** @param {string[]} argv paths to check @returns {number} process exit code */
export function main(argv) {
  let found = false;
  for (const path of argv) {
    for (const line of findProblems(readFileSync(path, "utf8"), path)) {
      found = true;
      process.stderr.write(`${path}:${line}: ${MESSAGE}\n`);
    }
  }
  if (!found) return 0;
  process.stderr.write(`${FIX}\n`);
  return 1;
}

// Run as a CLI, but stay importable for the test suite.
// `process.exitCode` rather than `process.exit()`: stderr can be a pipe, whose
// writes are asynchronous, and an immediate exit truncates the findings.
if (import.meta.url === `file://${process.argv[1]}`) {
  process.exitCode = main(process.argv.slice(2));
}
