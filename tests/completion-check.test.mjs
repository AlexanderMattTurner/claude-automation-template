import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  AFFIRMATION,
  ARMING_STOPS_MAX,
  DEFAULT_MAX_PINGS,
  PUSH_WINDOW_MS,
  RESUMPTION,
  isAffirmative,
  question,
  readState,
  readTurn,
  recordPush,
  run,
  statePath,
} from "../.claude/hooks/completion-check.mjs";

/** A throwaway state directory, so no test reads another's record. */
function stateDir() {
  return mkdtempSync(join(tmpdir(), "completion-check-"));
}

/** One JSONL transcript line. */
function line(role, content) {
  return JSON.stringify({ message: { role, content } });
}

const PROMPT = line("user", [{ type: "text", text: "do the thing" }]);
const TOOL_USE = line("assistant", [
  { type: "tool_use", id: "t1", name: "Bash", input: { command: "true" } },
]);
const TOOL_RESULT = line("user", [
  { type: "tool_result", tool_use_id: "t1", content: "ok" },
]);
const reply = (text) => line("assistant", [{ type: "text", text }]);

/** A transcript whose last turn did real work and ended with `text`. */
function workedTurn(text = "Pushed the fix.") {
  return [PROMPT, TOOL_USE, TOOL_RESULT, reply(text)].join("\n");
}

/**
 * A pushed session whose stop is armed: the window is behind it and the stop
 * count is spent, so the next certifiable stop asks.
 */
function armedSession(dir, transcript, id = "s") {
  const path = join(dir, `${id}.json`);
  const transcriptPath = join(dir, `${id}.jsonl`);
  writeFileSync(transcriptPath, transcript);
  writeFileSync(
    path,
    JSON.stringify({
      pushedAt: 1,
      deadlineMs: 2,
      stopsLeft: 1,
      pings: 0,
      done: false,
    }),
  );
  return { path, payload: { session_id: id, transcript_path: transcriptPath } };
}

describe("isAffirmative", () => {
  it("accepts the answer as the last line, however dressed", () => {
    for (const text of [
      "Yes.",
      "**Yes.**",
      "`Yes`",
      "yes",
      "All done.\n\nYes.",
    ])
      assert.equal(isAffirmative(text), true, text);
  });

  it("refuses the word inside a report or before more text", () => {
    for (const text of [
      "Yes. The tests pass, but one is red.",
      "Yes.\nMore.",
      "",
    ])
      assert.equal(isAffirmative(text), false, JSON.stringify(text));
  });
});

describe("question", () => {
  it("names the ping, the affirmation and the resumption marker", () => {
    const text = question(2, 3);
    assert.match(text, /Completion check 2 of 3/);
    assert.ok(text.includes(AFFIRMATION));
    assert.ok(text.includes(RESUMPTION));
  });
});

describe("readTurn", () => {
  it("reads the last turn's reply and whether it used a tool", () => {
    assert.deepEqual(readTurn(workedTurn("Done.")), {
      reply: "Done.",
      usedTools: true,
    });
  });

  it("stops at the prompt that opened the turn", () => {
    const earlier = [PROMPT, TOOL_USE, TOOL_RESULT, reply("old")].join("\n");
    const later = [PROMPT, reply("new")].join("\n");
    assert.deepEqual(readTurn(`${earlier}\n${later}`), {
      reply: "new",
      usedTools: false,
    });
  });

  it("does not count an empty ReadNotifications poll as work", () => {
    const poll = line("assistant", [
      { type: "tool_use", id: "p1", name: "ReadNotifications", input: {} },
    ]);
    const empty = line("user", [
      {
        type: "tool_result",
        tool_use_id: "p1",
        content: "No queued notifications.",
      },
    ]);
    assert.equal(
      readTurn([PROMPT, poll, empty, reply("waiting")].join("\n")).usedTools,
      false,
    );
    const found = line("user", [
      { type: "tool_result", tool_use_id: "p1", content: "1 notification" },
    ]);
    assert.equal(
      readTurn([PROMPT, poll, found, reply("got one")].join("\n")).usedTools,
      true,
    );
  });

  it("skips a line it cannot parse", () => {
    assert.deepEqual(readTurn(`not json\n${workedTurn("ok")}\n{broken`), {
      reply: "ok",
      usedTools: true,
    });
  });
});

describe("state", () => {
  it("refuses a session id that is not a safe filename", () => {
    for (const id of ["../x", "a/b", "", undefined])
      assert.equal(statePath(id, "/state"), null);
    assert.equal(statePath("abc-1", "/state"), "/state/abc-1.json");
  });

  it("reads a fresh state for an absent or damaged record", () => {
    const dir = stateDir();
    const fresh = readState(join(dir, "absent.json"));
    assert.equal(fresh.pushedAt, null);
    assert.equal(fresh.done, false);
    const path = join(dir, "junk.json");
    writeFileSync(path, '{"pushedAt":"soon","stopsLeft":"two","done":"yes"}');
    assert.deepEqual(readState(path), {
      pushedAt: null,
      deadlineMs: null,
      stopsLeft: 0,
      pings: 0,
      done: false,
    });
  });

  it("records the first push only", () => {
    const path = join(stateDir(), "s.json");
    assert.equal(recordPush(path, 1000), true);
    assert.equal(recordPush(path, 2000), false);
    assert.equal(readState(path).pushedAt, 1000);
  });
});

describe("run", () => {
  it("allows every stop of a session that never pushed", () => {
    const dir = stateDir();
    const transcript = join(dir, "t.jsonl");
    writeFileSync(transcript, workedTurn());
    assert.equal(
      run({ session_id: "s", transcript_path: transcript }, { stateDir: dir }),
      null,
    );
  });

  it("draws the arming moment once and keeps it", () => {
    const dir = stateDir();
    const path = join(dir, "s.json");
    const transcript = join(dir, "s.jsonl");
    writeFileSync(transcript, workedTurn());
    recordPush(path, 1_000_000);
    const options = { stateDir: dir, random: () => 0.5, now: () => 1_000_000 };
    assert.equal(
      run({ session_id: "s", transcript_path: transcript }, options),
      null,
    );
    const saved = readState(path);
    assert.equal(
      saved.deadlineMs,
      1_000_000 + Math.floor(0.5 * PUSH_WINDOW_MS),
    );
    // stopsLeft drew 1 + floor(0.5 * 3) = 2, and this certifiable stop spent one.
    assert.equal(saved.stopsLeft, 1 + Math.floor(0.5 * ARMING_STOPS_MAX) - 1);
    // A different random on the next stop changes nothing: the draw is stored.
    run(
      { session_id: "s", transcript_path: transcript },
      { ...options, random: () => 0.9 },
    );
    assert.equal(readState(path).deadlineMs, saved.deadlineMs);
  });

  it("asks once the window has passed, and again until answered or spent", () => {
    const dir = stateDir();
    const { path, payload } = armedSession(dir, workedTurn("Pushed."));
    const options = { stateDir: dir, now: () => 10 };
    const first = run(payload, options);
    assert.equal(first?.decision, "block");
    assert.match(first?.reason ?? "", /Completion check 1 of 3/);
    assert.equal(readState(path).pings, 1);
    // Not the affirmation: asked again.
    writeFileSync(payload.transcript_path, workedTurn("Still going."));
    assert.match(run(payload, options)?.reason ?? "", /check 2 of 3/);
    // The affirmation as the last line ends the check.
    writeFileSync(payload.transcript_path, workedTurn("All done.\n\nYes."));
    assert.equal(run(payload, options), null);
    assert.equal(readState(path).done, true);
    assert.equal(run(payload, options), null);
  });

  it("does not let an unasked 'Yes.' spend the one shot", () => {
    const dir = stateDir();
    const { path, payload } = armedSession(dir, workedTurn("Yes."));
    const response = run(payload, { stateDir: dir, now: () => 10 });
    assert.equal(response?.decision, "block");
    assert.equal(readState(path).done, false);
  });

  it("allows the stop after the ping budget is spent", () => {
    const dir = stateDir();
    const { path, payload } = armedSession(dir, workedTurn("nope"));
    const options = { stateDir: dir, now: () => 10, maxPings: 2 };
    assert.equal(run(payload, options)?.decision, "block");
    assert.equal(run(payload, options)?.decision, "block");
    assert.equal(run(payload, options), null);
    assert.equal(readState(path).done, true);
    assert.equal(DEFAULT_MAX_PINGS, 3);
  });

  it("allows a stop with nothing to certify without spending the shot", () => {
    const dir = stateDir();
    const idle = [PROMPT, reply("nothing to do")].join("\n");
    const { path, payload } = armedSession(dir, idle);
    assert.equal(run(payload, { stateDir: dir, now: () => 10 }), null);
    assert.equal(readState(path).pings, 0);
    assert.equal(readState(path).done, false);
  });

  it("waits out the stop count inside the window", () => {
    const dir = stateDir();
    const { path, payload } = armedSession(dir, workedTurn());
    writeFileSync(
      path,
      JSON.stringify({
        pushedAt: 1,
        deadlineMs: 100,
        stopsLeft: 2,
        pings: 0,
        done: false,
      }),
    );
    assert.equal(run(payload, { stateDir: dir, now: () => 50 }), null);
    assert.equal(readState(path).stopsLeft, 1);
    assert.equal(
      run(payload, { stateDir: dir, now: () => 50 })?.decision,
      "block",
    );
  });

  it("is disabled by COMPLETION_CHECK=0 and by an unusable payload", () => {
    const dir = stateDir();
    const { payload } = armedSession(dir, workedTurn());
    process.env.COMPLETION_CHECK = "0";
    try {
      assert.equal(run(payload, { stateDir: dir, now: () => 10 }), null);
    } finally {
      delete process.env.COMPLETION_CHECK;
    }
    assert.equal(
      run({ session_id: "../x", transcript_path: "t" }, { stateDir: dir }),
      null,
    );
    assert.equal(run({ session_id: "s" }, { stateDir: dir }), null);
  });

  it("records the state it acted on", () => {
    const dir = stateDir();
    const { path, payload } = armedSession(dir, workedTurn());
    run(payload, { stateDir: dir, now: () => 10 });
    assert.equal(JSON.parse(readFileSync(path, "utf8")).pings, 1);
  });
});
