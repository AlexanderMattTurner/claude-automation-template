/** CLI entry-point helper for the resolver's own scripts.
 *
 * A SIBLING of the resolver, not an import from the calling repository. The
 * resolver is cloned on its own into `${RUNNER_TEMP}/resolver` and runs against
 * a workspace holding an untrusted pull-request head, so every module it loads
 * must resolve inside its own tree. A reach up into the caller's `scripts/`
 * would either fail to resolve or load bytes the pull request wrote.
 */

import { pathToFileURL } from "node:url";

/**
 * True when this module is the process entry point (run directly as a CLI, not
 * imported). Guards an undefined `process.argv[1]` (e.g. the REPL) before
 * resolving it: the bare `import.meta.url === pathToFileURL(process.argv[1])`
 * form throws there. Resolving argv[1] through pathToFileURL also normalizes a
 * relative invocation path to an absolute file URL before comparing.
 * @param {string} importMetaUrl  the caller's `import.meta.url`
 * @returns {boolean}
 */
export function isMain(importMetaUrl) {
  // argv[1] is Node's own entry-point slot, holding the invoked script's path.
  // Nothing a caller passes can shift a value into it.
  return (
    Boolean(process.argv[1]) &&
    importMetaUrl === pathToFileURL(process.argv[1]).href
  );
}
