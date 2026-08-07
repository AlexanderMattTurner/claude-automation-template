import js from "@eslint/js";
import globals from "globals";
import regexp from "eslint-plugin-regexp";

// Lint the JavaScript the template PROPAGATES, under the rules a consumer is
// likely to point at its whole tree (`eslint .`). A consumer whose lint covers
// the repo root inherits these files; anything that fails here fails there, and
// for a consumer that gates releases on `pnpm test` that blocks publishing.
// The rule set is deliberately broader than eslint's own recommended: it adds
// the regexp plugin and the stylistic rules that flagged template files
// downstream, so a regression is caught here rather than in someone else's CI.
export default [
  {
    // `_template/` is the transient template checkout template-sync.sh creates
    // in the consumer's working tree; linting it would report the template's
    // files as the consumer's.
    ignores: ["node_modules/", ".venv/", "dist/", "build/", "_template/"],
  },
  js.configs.recommended,
  regexp.configs["flat/recommended"],
  {
    files: ["**/*.js", "**/*.mjs", "**/*.cjs"],
    languageOptions: {
      ecmaVersion: 2024,
      sourceType: "module",
      globals: { ...globals.node },
    },
    rules: {
      "prefer-named-capture-group": "error",
      "prefer-template": "error",
      "no-template-curly-in-string": "error",
      "no-new": "error",
      "no-void": "error",
    },
  },
  {
    // `.js` files here are CommonJS (`require`/`module.exports`) — the package
    // is "type": "module", so they are loaded by github-script's `require`,
    // not by node's ESM loader.
    files: [".github/scripts/*.js"],
    languageOptions: { sourceType: "commonjs" },
  },
];
