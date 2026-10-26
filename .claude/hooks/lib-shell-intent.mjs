/**
 * What a tool call DOES, for every hook that acts on a push or on a new pull
 * request or issue: the PreToolUse push gate and the skill gates.
 *
 * PROBLEM CLASS — two hooks deciding "is this a push / a PR create" with two
 * matchers. A settings.json `if` such as `Bash(git push:*)` missed
 * `git -C /repo push`; the skill gates' regex missed `gh -R o/r pr create`,
 * `GH_TOKEN=x gh pr create` and `env gh pr create`. Which checks ran then
 * depended on how the command was spelled. Every hook asks this module instead.
 *
 * A word scan, not a shell parser: these hooks run on a bare `node` on every tool
 * call, with no node_modules. It finds a program only at COMMAND POSITION — the
 * start of the command, or after a shell separator or keyword — so a MENTION
 * inside a quoted argument or a grep pattern does not count. It cannot see quotes
 * or heredocs across lines, so a LINE of a heredoc body or of a multi-line string
 * that starts with the program counts: the error runs the checks. Before the
 * program it skips variable assignments, an `env` wrapper and a directory prefix;
 * after it, the program's own global flags, consuming the value of each flag that
 * takes one. A flag it does not know counts as one that takes no value.
 */

/** Blanks between two words, a backslash-newline continuation included. */
const WS = String.raw`(?:[ \t]|\\\n)+`;

/** One shell word: a quoted string, or a run with no blank or operator in it. */
const WORD = String.raw`(?:"[^"\n]*"|'[^'\n]*'|[^\s;|&()'"]+)`;

/**
 * Flags, then words. A flag in `valueFlags` consumes the next word as its value.
 * The bare-flag arm refuses those names, so `git -C push log` never backtracks
 * into reading `-C` as bare and `push` as the subcommand.
 * @param {readonly string[]} valueFlags @returns {string}
 */
function flagsSource(valueFlags) {
  const names = `(?:${valueFlags.join("|")})`;
  return String.raw`(?:${WS}(?:${names}${WS}${WORD}|(?!${names}(?:\s|$))-[^\s;|&()]*))*`;
}

/** Any run of `NAME=value` words before a program. */
const ASSIGNMENTS = String.raw`(?:[A-Za-z_]\w*=${WORD}?${WS})*`;

/**
 * The start of a command: the start of the text or a separator, then any shell
 * keywords, assignments and an `env` wrapper with its flags.
 */
const COMMAND_START = [
  String.raw`(?:^|[\n;|&(){}!\`])[ \t]*`,
  String.raw`(?:(?:if|then|elif|else|while|until|do|time)${WS})*`,
  ASSIGNMENTS,
  String.raw`(?:env${flagsSource(["-u", "-C", "-S"])}${WS}${ASSIGNMENTS})?`,
].join("");

/**
 * A regex for `program [global flags] word...` at command position.
 * @param {string} program @param {readonly string[]} valueFlags global flags
 *   whose value is the NEXT word @param {readonly string[]} words
 * @returns {RegExp}
 */
function commandAt(program, valueFlags, words) {
  const dirPrefix = String.raw`(?:[^\s;|&()'"]*/)?`;
  const subcommand = words.map((w) => `${WS}${w}`).join("");
  return new RegExp(
    `${COMMAND_START}${dirPrefix}${program}${flagsSource(valueFlags)}${subcommand}\\b`,
  );
}

/** git's global options that take their value as the next word. */
const GIT_VALUE_FLAGS = Object.freeze([
  "-C",
  "-c",
  "--git-dir",
  "--work-tree",
  "--namespace",
  "--config-env",
  "--attr-source",
]);

/** gh's flags that may precede the subcommand and take a value. */
const GH_VALUE_FLAGS = Object.freeze(["-R", "--repo"]);

const GIT_PUSH_RE = commandAt("git", GIT_VALUE_FLAGS, ["push"]);

/** Suffix-matched: the same tool arrives as `mcp__github__…` or `mcp__github_remote__…`. */
const PR_MCP_TOOL_RE = /^mcp__.*create_pull_request$/;

/**
 * A regex for `gh <group> <action>` at command position. `create\b` counts
 * `create-draft` and not `creates`; `mygh` is not `gh`.
 * @param {string} group @param {string} action @returns {RegExp}
 */
export function ghCommand(group, action) {
  return commandAt("gh", GH_VALUE_FLAGS, [group, action]);
}

const GH_PR_CREATE_RE = ghCommand("pr", "create");

/**
 * The shell text of a Bash tool call, or null for any other call.
 * @param {any} payload @returns {string|null}
 */
export function bashCommand(payload) {
  const command = payload?.tool_input?.command;
  return payload?.tool_name === "Bash" && typeof command === "string"
    ? command
    : null;
}

/**
 * Does this tool call push commits?
 * @param {any} payload @returns {boolean}
 */
export function pushesCommits(payload) {
  const command = bashCommand(payload);
  return command !== null && GIT_PUSH_RE.test(command);
}

/**
 * Does this tool call open a pull request — `gh pr create`, or any MCP
 * `create_pull_request` tool? Out of scope: `gh api` or `curl` to the pulls
 * endpoint, a script whose body runs `gh pr create`, and the web UI. Each is some
 * HTTP client, so matching them means matching arbitrary programs.
 * @param {any} payload @returns {boolean}
 */
export function opensPullRequest(payload) {
  if (typeof payload?.tool_name !== "string") return false;
  if (PR_MCP_TOOL_RE.test(payload.tool_name)) return true;
  const command = bashCommand(payload);
  return command !== null && GH_PR_CREATE_RE.test(command);
}
