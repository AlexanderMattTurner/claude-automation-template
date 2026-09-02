import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  PROMPTS,
  SEGMENT_MS,
  carryingEvent,
  decide,
  offsetMs,
  promptFor,
  readState,
  runCheck,
  segmentOf,
  statePath,
} from "../.claude/hooks/bullshit-check.mjs";

/** A throwaway state directory, so no test reads another's record. */
function stateDir() {
  const dir = mkdtempSync(join(tmpdir(), "bullshit-check-"));
  process.env.BULLSHIT_CHECK_STATE_DIR = dir;
  return dir;
}

/**
 * An anchor that puts `now` one millisecond before the end of segment 0, where
 * that segment's moment is always behind it — the offset cannot exceed the
 * segment. So a check due NOW needs no search for a session id.
 */
function anchorDueBy(now, segmentMs = SEGMENT_MS) {
  return now - (segmentMs - 1);
}

describe("statePath", () => {
  it("names one file per session under the state directory", () => {
    assert.equal(statePath("abc-123", "/state"), "/state/abc-123.segment");
  });

  it("refuses a session id that is not already a safe filename", () => {
    for (const id of ["../escape", "a/b", "", 7, undefined, null])
      assert.equal(statePath(id, "/state"), null);
  });
});

describe("segmentOf", () => {
  it("counts segments from the anchor, not from the epoch", () => {
    const start = 1_700_000_123_456;
    assert.equal(segmentOf(start, start), 0);
    assert.equal(segmentOf(start + SEGMENT_MS - 1, start), 0);
    assert.equal(segmentOf(start + SEGMENT_MS, start), 1);
    assert.equal(segmentOf(start + SEGMENT_MS, start + 1), 0);
  });
});

describe("offsetMs", () => {
  it("lands inside the segment and spreads over it", () => {
    const sixths = new Set();
    for (let segment = 0; segment < 300; segment += 1) {
      const offset = offsetMs("spread", segment);
      assert.ok(offset >= 0 && offset < SEGMENT_MS, `offset ${offset}`);
      sixths.add(Math.floor((offset / SEGMENT_MS) * 6));
    }
    // Sixths rather than a mean: a draw stuck in one part of the segment
    // averages to the middle and would pass a mean test.
    assert.equal(sixths.size, 6);
  });

  it("answers the same moment for every process in one segment", () => {
    assert.equal(offsetMs("session-a", 42), offsetMs("session-a", 42));
    assert.notEqual(offsetMs("session-a", 42), offsetMs("session-b", 42));
  });
});

describe("promptFor", () => {
  it("opens every question with the prefix the wiring tests match", () => {
    for (const prompt of PROMPTS) assert.match(prompt, /\*\*Bullshit check/);
  });

  it("answers the same question for every process in one segment", () => {
    assert.equal(promptFor("session-a", 7), promptFor("session-a", 7));
  });

  it("reaches every question as the segments pass", () => {
    const seen = new Set(
      Array.from({ length: 60 }, (_, i) => promptFor("session-a", i)),
    );
    assert.equal(seen.size, PROMPTS.length);
  });

  it("draws the question independently of the moment", () => {
    // A shared digest word would make the question a function of the moment:
    // SEGMENT_MS is even, so `offsetMs % PROMPTS.length` would BE the prompt index.
    const tiedToTheMoment = Array.from({ length: 200 }, (_, i) => i).every(
      (i) =>
        PROMPTS.indexOf(promptFor("split", i)) ===
        offsetMs("split", i) % PROMPTS.length,
    );
    assert.equal(tiedToTheMoment, false);
  });
});

describe("carryingEvent", () => {
  it("names the event a question would ride", () => {
    assert.equal(
      carryingEvent({ hook_event_name: "PostToolUse" }),
      "PostToolUse",
    );
    assert.equal(
      carryingEvent({ hook_event_name: "UserPromptSubmit" }),
      "UserPromptSubmit",
    );
  });

  it("refuses every other event, and a payload that names none", () => {
    for (const event of ["PreToolUse", "SessionStart", "Stop", ""])
      assert.equal(carryingEvent({ hook_event_name: event }), null);
    assert.equal(carryingEvent({}), null);
    assert.equal(carryingEvent(undefined), null);
  });
});

describe("readState", () => {
  it("reads back a recorded anchor and segment", () => {
    const path = join(stateDir(), "s.segment");
    writeFileSync(path, "1700000000000 17\n");
    assert.deepEqual(readState(path), { start: 1_700_000_000_000, last: 17 });
  });

  it("answers null for an absent or unusable record", () => {
    const dir = stateDir();
    assert.equal(readState(join(dir, "absent.segment")), null);
    for (const text of [
      "",
      "17",
      "1700000000000 17 33",
      "1700000000000  17",
      "not-a-number 17",
      "1.5 17",
      "0 17",
      "1700000000000 -2",
    ]) {
      const path = join(dir, "junk.segment");
      writeFileSync(path, text);
      assert.equal(readState(path), null, `read back ${JSON.stringify(text)}`);
    }
  });
});

describe("decide", () => {
  const session = "session-a";
  const start = 1_700_000_123_456;
  const segment = 3;
  const due = start + segment * SEGMENT_MS + offsetMs(session, segment);

  it("anchors a session that has no record yet, and asks nothing", () => {
    assert.deepEqual(decide({ sessionId: session, now: start, state: null }), {
      fire: false,
      record: { start, last: -1 },
    });
  });

  it("re-anchors when the clock moved back behind the anchor", () => {
    assert.deepEqual(
      decide({ sessionId: session, now: start - 1, state: { start, last: 0 } }),
      { fire: false, record: { start: start - 1, last: -1 } },
    );
  });

  it("fires on the first call at or after the moment, once", () => {
    const state = { start, last: 2 };
    assert.deepEqual(decide({ sessionId: session, now: due - 1, state }), {
      fire: false,
      record: null,
    });
    assert.deepEqual(decide({ sessionId: session, now: due, state }), {
      fire: true,
      record: { start, last: segment },
    });
    assert.deepEqual(
      decide({
        sessionId: session,
        now: due + 60_000,
        state: { start, last: segment },
      }),
      { fire: false, record: null },
    );
  });

  it("never rewinds to a segment it has already spent", () => {
    assert.deepEqual(
      decide({ sessionId: session, now: due, state: { start, last: 5 } }),
      { fire: false, record: null },
    );
  });

  it("asks at once when the segment before this one went unspent", () => {
    // Spent segment 1, idle through segment 2, back BEFORE segment 3's moment.
    const first = decide({
      sessionId: session,
      now: due - 1,
      state: { start, last: 1 },
    });
    assert.deepEqual(first, { fire: true, record: { start, last: segment } });
    // One question for the segments it skipped, never a backlog.
    assert.deepEqual(
      decide({ sessionId: session, now: due - 1, state: first.record }),
      { fire: false, record: null },
    );
  });

  it("carries an overdue check only when told to", () => {
    // A prompt waits for the moment: a session woken by prompts is overdue at
    // every wake and would be asked at turn start, before any work exists.
    const state = { start, last: 1 };
    assert.equal(
      decide({ sessionId: session, now: due - 1, state, carryOverdue: false })
        .fire,
      false,
    );
    assert.deepEqual(
      decide({ sessionId: session, now: due, state, carryOverdue: false }),
      { fire: true, record: { start, last: segment } },
    );
  });
});

describe("runCheck", () => {
  const payload = (fields = {}) => ({
    hook_event_name: "PostToolUse",
    session_id: "session-run",
    ...fields,
  });

  /** Writes the state file that leaves `now` past a due moment. */
  function dueState(dir, now, session = "session-run") {
    const start = anchorDueBy(now);
    writeFileSync(join(dir, `${session}.segment`), `${start} -1`);
    return start;
  }

  it("asks once per segment and records what it spent", () => {
    const dir = stateDir();
    const now = Date.now();
    const start = dueState(dir, now);
    assert.equal(runCheck(payload(), now), promptFor("session-run", 0));
    assert.equal(
      readFileSync(join(dir, "session-run.segment"), "utf8"),
      `${start} 0`,
    );
    // The record is renamed into place, so no temp file survives the write.
    assert.ok(readdirSync(dir).every((entry) => !entry.endsWith(".tmp")));
    assert.equal(runCheck(payload(), now + 1), null);
  });

  it("asks once when two events race on the same segment", () => {
    const dir = stateDir();
    const now = Date.now();
    const start = dueState(dir, now);
    assert.ok(runCheck(payload(), now));
    // The second process read the record before the first one wrote, so it
    // sees the same pre-fire state and decides to fire too.
    writeFileSync(join(dir, "session-run.segment"), `${start} -1`);
    assert.equal(runCheck(payload(), now), null);
  });

  it("asks on the user's prompt as well as on a tool call, once", () => {
    const dir = stateDir();
    const now = Date.now();
    dueState(dir, now);
    assert.ok(runCheck(payload({ hook_event_name: "UserPromptSubmit" }), now));
    assert.equal(runCheck(payload(), now), null);
  });

  it("leaves an overdue check to the next tool call, not the prompt", () => {
    // Segment 0 spent, segment 1 skipped, now early in segment 2 before its
    // moment.
    const dir = stateDir();
    const start = Date.now() - 2 * SEGMENT_MS;
    writeFileSync(join(dir, "session-run.segment"), `${start} 0`);
    const early = start + 2 * SEGMENT_MS + offsetMs("session-run", 2) - 1;
    assert.equal(
      runCheck(payload({ hook_event_name: "UserPromptSubmit" }), early),
      null,
    );
    assert.ok(runCheck(payload(), early));
  });

  it("asks again after a re-anchor despite a stale claim file", () => {
    const dir = stateDir();
    const now = Date.now();
    const start = dueState(dir, now);
    assert.ok(runCheck(payload(), now));
    assert.ok(existsSync(join(dir, "session-run.segment.0")));
    const rewound = start - 1000;
    assert.equal(runCheck(payload(), rewound), null);
    assert.equal(existsSync(join(dir, "session-run.segment.0")), false);
    assert.ok(runCheck(payload(), rewound + offsetMs("session-run", 0)));
  });

  it("anchors a session on its first event and asks nothing yet", () => {
    const dir = stateDir();
    const now = Date.now();
    assert.equal(runCheck(payload(), now), null);
    assert.equal(
      readFileSync(join(dir, "session-run.segment"), "utf8"),
      `${now} -1`,
    );
    assert.ok(runCheck(payload(), now + offsetMs("session-run", 0)));
  });

  it("leaves the record alone for a subagent's tool call", () => {
    const dir = stateDir();
    const now = Date.now();
    const start = dueState(dir, now);
    assert.equal(runCheck(payload({ agent_id: "agent_01ABC" }), now), null);
    assert.equal(
      readFileSync(join(dir, "session-run.segment"), "utf8"),
      `${start} -1`,
    );
    assert.ok(runCheck(payload(), now));
  });

  it("stays silent, record untouched, for a payload it cannot place", () => {
    const dir = stateDir();
    const now = Date.now();
    const start = dueState(dir, now);
    assert.equal(runCheck(payload({ session_id: "../escape" }), now), null);
    assert.equal(runCheck(undefined, now), null);
    assert.equal(
      runCheck(payload({ hook_event_name: "PreToolUse" }), now),
      null,
    );
    assert.equal(runCheck({ session_id: "session-run" }, now), null);
    assert.equal(
      readFileSync(join(dir, "session-run.segment"), "utf8"),
      `${start} -1`,
    );
  });

  it("drops the question when a directory squats on the record path", () => {
    // The record is renamed into place, and a rename over a directory fails: the
    // question is dropped, and the temp file the rename would have moved is gone.
    const dir = stateDir();
    mkdirSync(join(dir, "session-run.segment"));
    assert.equal(runCheck(payload(), Date.now()), null);
    assert.ok(readdirSync(dir).every((entry) => !entry.endsWith(".tmp")));
  });

  it("drops the question when the record cannot be written", () => {
    // A regular file where the state directory should be: repeating the
    // question on every later event is worse than missing this segment's.
    const blocked = join(stateDir(), "blocked");
    writeFileSync(blocked, "not a directory");
    process.env.BULLSHIT_CHECK_STATE_DIR = blocked;
    assert.equal(runCheck(payload(), Date.now()), null);
  });
});
