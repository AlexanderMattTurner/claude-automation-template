#!/usr/bin/env node
/**
 * UserPromptSubmit: drop CI-failure webhook events for superseded commits.
 *
 * A session subscribed to a PR receives a `<github-webhook-activity>` turn for
 * every failed check run — including runs a newer push already cancelled. Those
 * stale-SHA "failures" are supersession noise (a cancelled shard relayed red by
 * an always() reporter, or an autofix job that amended the head and force-pushed):
 * the red blocks nothing, since branch protection only evaluates the current
 * head, yet each delivery wakes the session for a full-context turn that
 * concludes "ignore it". This hook ends that turn before the model runs: when a
 * CI-failure event's HeadSHA is no longer the head of ANY remote branch, the
 * prompt is blocked with a one-line reason.
 *
 * Posture: fail OPEN. This is an advisory noise filter, not a defense — a
 * mis-dropped real failure would hide signal, so the event passes through on any
 * uncertainty: unparsable payload, git unavailable, ls-remote failure or
 * timeout, or the SHA still being a live head (of any branch: cheap, and a head
 * match is exactly the "still current" case).
 *
 * Dependency-free on purpose: the template ships no node_modules, so the hook
 * must run on a bare `node` from a fresh clone.
 */
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { pathToFileURL } from "node:url";

const pExecFile = promisify(execFile);

/** Conclusions that mark a run as red; success/skipped events are never dropped. */
export const RED_CONCLUSIONS = ["failure", "cancelled", "timed_out"];

/**
 * The HeadSHA of a red-CI webhook event, or null. Matches only the harness's
 * `<github-webhook-activity>` CI shape: the tag, a red `Conclusion:` line, and
 * a full-length `HeadSHA:` line.
 * @param {string} prompt
 * @returns {{ sha: string } | null}
 */
export function parseCiFailureEvent(prompt) {
  if (!prompt.includes("<github-webhook-activity>")) return null;
  const conclusion = /^Conclusion:[ \t]*(?<state>[a-z_]+)[ \t]*$/m.exec(prompt);
  if (!RED_CONCLUSIONS.includes(conclusion?.groups?.state ?? "")) return null;
  const sha = /^HeadSHA:[ \t]*(?<sha>[0-9a-f]{40})[ \t]*$/m.exec(prompt);
  const found = sha?.groups?.sha;
  return found ? { sha: found } : null;
}

/**
 * True when SHA heads any remote branch in `git ls-remote --heads` output.
 * Origin-only by design: a head living on a fork remote (or on a branch deleted
 * after its run) always reads stale and gets dropped — acceptable while only
 * origin branches run CI on the repo.
 * @param {string} sha
 * @param {string} lsRemoteOut
 * @returns {boolean}
 */
export function isCurrentHead(sha, lsRemoteOut) {
  return lsRemoteOut.split("\n").some((line) => line.startsWith(`${sha}\t`));
}

/**
 * `git ls-remote --heads origin` from the project root. Throws on any git
 * failure/timeout; the judge's catch converts that into a pass.
 * @returns {Promise<string>}
 */
export async function remoteHeads() {
  const { stdout } = await pExecFile(
    "git",
    ["ls-remote", "--heads", "origin"],
    {
      cwd: process.env.CLAUDE_PROJECT_DIR || process.cwd(),
      timeout: 8000,
    },
  );
  return stdout;
}

/**
 * Decide the hook response for one raw UserPromptSubmit payload: a block body
 * for a red-CI webhook whose HeadSHA heads no remote branch, or null to pass
 * everything else through untouched.
 * @param {any} payload parsed hook stdin JSON
 * @param {() => Promise<string>} [listHeads] injectable head lister
 * @returns {Promise<{ decision: "block", reason: string } | null>}
 */
export async function judgeDropSupersededCiEvent(
  payload,
  listHeads = remoteHeads,
) {
  if (typeof payload !== "object" || payload === null) return null;
  if (payload.hook_event_name !== "UserPromptSubmit") return null;
  const parsed = parseCiFailureEvent(String(payload.prompt ?? ""));
  if (!parsed) return null;
  let heads;
  try {
    heads = await listHeads();
  } catch {
    return null;
  }
  if (isCurrentHead(parsed.sha, heads)) return null;
  return {
    decision: "block",
    reason:
      `Dropped superseded CI-failure event: ${parsed.sha.slice(0, 12)} is no ` +
      "longer the head of any remote branch, so a newer push already replaced " +
      "this run. Only failures on a PR's current head are actionable.",
  };
}

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8");
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  try {
    const payload = JSON.parse(await readStdin());
    const response = await judgeDropSupersededCiEvent(payload);
    if (response !== null) process.stdout.write(JSON.stringify(response));
  } catch {
    process.exit(0); // Advisory only: never block the agent on a hook fault.
  }
}
