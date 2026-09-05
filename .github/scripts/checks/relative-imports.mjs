#!/usr/bin/env node
// Fail when a relative `import`/`export … from` specifier resolves to a file
// that does not exist. Node's ESM resolver does no extension guessing and no
// directory-index fallback, so `./lib-hook-io` for `lib-hook-io.mjs` is
// ERR_MODULE_NOT_FOUND. It also requires the target to be a FILE, so a specifier
// naming a directory is reported too: `import "./lib"` for a `lib/` directory is
// ERR_UNSUPPORTED_DIR_IMPORT, the same broken-script outcome by a different error.
//
// Resolution is PURELY STATIC — the specifier is joined to the importing file's
// directory and the result is stat'd; no module is ever loaded. Checked: static
// `import … from "./x"`, `import "./x"`, `export … from "./x"`,
// `export * from "./x"`, and `import("./x")` when the argument is a plain string
// literal. Not checked: bare specifiers (`node:fs`, a package name), which
// resolve through node_modules, and `#imports` subpath specifiers.

import { execFileSync } from "node:child_process";
import { readFileSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import ts from "typescript";

// The CI-only / hook script surface: code that runs on a runner or in a hook
// rather than under the test suite, so a bad path stays invisible until the
// script runs there. Exported so the test drives the scan set member by member.
export const SCAN_DIRS = [".github/scripts", "scripts", ".claude/hooks"];

/** @param {string} rel a repo-relative path @returns {boolean} */
export function isScannable(rel) {
  const name = rel.slice(rel.lastIndexOf("/") + 1);
  if (!name.endsWith(".mjs")) return false;
  return SCAN_DIRS.some((dir) => rel.startsWith(`${dir}/`));
}

function trackedScanFiles() {
  // Directory pathspecs match every tracked file underneath; `isScannable` does
  // the extension filtering.
  const out = execFileSync("git", ["ls-files", "-z", ...SCAN_DIRS], {
    encoding: "utf8",
  });
  return out.split("\0").filter(Boolean).filter(isScannable);
}

// Every relative module specifier in SOURCE, as {specifier, line} (1-based). A
// `typescript` AST walk, so a specifier inside a string, a comment or a
// template literal is never reported as an import.
/**
 * @param {string} source the file's text.
 * @param {string} rel its repo-relative path, used as the parser's file name.
 * @returns {{specifier: string, line: number}[]}
 */
export function relativeSpecifiers(source, rel) {
  const sourceFile = ts.createSourceFile(
    rel,
    source,
    ts.ScriptTarget.Latest,
    /* setParentNodes */ true,
    ts.ScriptKind.JS,
  );
  /** @type {{specifier: string, line: number}[]} */
  const found = [];
  /** @param {import("typescript").Node} node */
  const record = (node) => {
    if (!ts.isStringLiteralLike(node) || !node.text.startsWith(".")) return;
    found.push({
      specifier: node.text,
      line:
        sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile))
          .line + 1,
    });
  };

  /** @param {import("typescript").Node} node */
  const visit = (node) => {
    if (
      (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) &&
      node.moduleSpecifier
    ) {
      record(node.moduleSpecifier);
    }
    if (
      ts.isCallExpression(node) &&
      node.expression.kind === ts.SyntaxKind.ImportKeyword &&
      node.arguments.length > 0
    ) {
      record(node.arguments[0]);
    }
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  return found;
}

// A one-line problem string per unresolvable specifier in one file.
/**
 * @param {string} source the file's text.
 * @param {string} rel its repo-relative path.
 * @returns {string[]}
 */
export function findProblems(source, rel) {
  const base = dirname(rel);
  const problems = [];
  for (const { specifier, line } of relativeSpecifiers(source, rel)) {
    // Node lets a file specifier carry a query/fragment for cache-busting
    // (`./x.mjs?v=2`); the path it resolves is everything before them.
    const target = resolve(base, specifier.split(/[?#]/)[0]);
    let stat;
    try {
      stat = statSync(target);
    } catch {
      problems.push(
        `${rel}:${line}: '${specifier}' resolves to ${target}, which does not exist`,
      );
      continue;
    }
    if (!stat.isFile()) {
      problems.push(
        `${rel}:${line}: '${specifier}' resolves to a directory (${target}); ` +
          "Node ESM has no directory-index fallback — name the file",
      );
    }
  }
  return problems;
}

function main() {
  const files = trackedScanFiles();
  if (files.length === 0) {
    process.stderr.write(
      `relative-imports: no .mjs files found under ${SCAN_DIRS.join(", ")} — ` +
        "the scan set is empty, so this check verified nothing\n",
    );
    process.exit(1);
  }

  const problems = files.flatMap((rel) =>
    findProblems(readFileSync(rel, "utf8"), rel),
  );
  if (problems.length > 0) {
    process.stderr.write(
      `unresolvable relative imports:\n  ${problems.sort().join("\n  ")}\n` +
        `Node ESM does not guess extensions or fall back to a directory ` +
        `index, so each of these is an ERR_MODULE_NOT_FOUND the moment the ` +
        `script runs.\n`,
    );
    process.exit(1);
  }
}

// Run as a CLI, but stay importable for the test suite.
if (import.meta.url === `file://${process.argv[1]}`) {
  main();
}
