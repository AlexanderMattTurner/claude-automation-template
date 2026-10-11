// Behavior tests for the CI-failure tracking-issue filer: does a failed
// post-merge/scheduled workflow_run produce (or update) the right tracking
// issue, and does a later run that passes every job the issue still waits on
// close it? Drives the real module (no re-implementation) and asserts the
// observable calls made against a fake Octokit-shaped `github` client — the
// real dependency is the GitHub API, which no test can reach.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const notify = require("./ci-failure-notify.js");

const REPO = { owner: "owner", repo: "downstream-repo" };
const RUN_ID = 12345;
const MARKER = "<!-- ci-failure-notify:";
const RUN_FACTS = {
  id: RUN_ID,
  url: "https://github.com/owner/downstream-repo/actions/runs/1",
  conclusion: "failure",
  sha: "deadbeef",
  event: "push",
};

/** An open tracking issue, as `issues.listForRepo` returns one. */
function openIssue({ number = 42, title = "CI failure: CI", body = "" } = {}) {
  return { number, title, body, pull_request: undefined };
}

/** An issue body carrying the record the script reads its waiting list from. */
function recordedBody(jobs, run = RUN_FACTS) {
  const record = { jobs, run };
  return [
    "The **CI** workflow failed on `main`.",
    "",
    `${MARKER}${JSON.stringify(record)} -->`,
    "",
  ].join("\n");
}

/** The record a body carries, read back the way the script reads it. */
function recordIn(body) {
  const line = body.split("\n").find((text) => text.startsWith(MARKER));
  assert.ok(line, `no record in body:\n${body}`);
  return JSON.parse(line.slice(MARKER.length, line.lastIndexOf("-->")));
}

/** Build a fake Octokit-shaped `github` client that records every call. */
function fakeGithub({
  createLabelError,
  existingIssues = [],
  runJobs = [],
} = {}) {
  const calls = {
    createLabel: [],
    createComment: [],
    create: [],
    update: [],
    paginate: [],
  };
  const github = {
    calls,
    rest: {
      issues: {
        async createLabel(params) {
          calls.createLabel.push(params);
          if (createLabelError) {
            throw createLabelError;
          }
        },
        async createComment(params) {
          calls.createComment.push(params);
        },
        async create(params) {
          calls.create.push(params);
          return { data: { number: 999 } };
        },
        async update(params) {
          calls.update.push(params);
        },
        // Referenced only as a paginate() argument; the fake paginate below
        // dispatches on which lister it was handed.
        listForRepo() {},
      },
      actions: { listJobsForWorkflowRun() {} },
    },
    async paginate(lister, params) {
      calls.paginate.push(params);
      return lister === github.rest.actions.listJobsForWorkflowRun
        ? runJobs
        : existingIssues;
    },
  };
  return github;
}

function workflowRunContext(overrides = {}) {
  return {
    repo: REPO,
    payload: {
      workflow_run: {
        id: RUN_ID,
        name: "CI",
        html_url: RUN_FACTS.url,
        conclusion: "failure",
        head_sha: "deadbeef",
        event: "push",
        head_branch: "main",
        ...overrides,
      },
    },
  };
}

describe("ci-failure-notify", () => {
  it("creates a new labeled issue recording the jobs that failed", async () => {
    const github = fakeGithub({
      existingIssues: [],
      runJobs: [
        { name: "decide", conclusion: "success" },
        { name: "lint", conclusion: "failure" },
        { name: "docs", conclusion: "skipped" },
      ],
    });
    const context = workflowRunContext();

    await notify({ github, context });

    assert.equal(github.calls.createLabel.length, 1);
    assert.equal(github.calls.createComment.length, 0);
    assert.equal(github.calls.create.length, 1);

    const created = github.calls.create[0];
    assert.equal(created.owner, REPO.owner);
    assert.equal(created.repo, REPO.repo);
    assert.equal(created.title, "CI failure: CI");
    assert.deepEqual(created.labels, ["ci-failure"]);
    assert.match(created.body, /deadbeef/);
    assert.match(created.body, /main/);
    // Only the job that failed is recorded: a skipped job may be skipped by
    // design on every run, and demanding it pass would never close the issue.
    assert.deepEqual(recordIn(created.body), {
      jobs: ["lint"],
      run: RUN_FACTS,
    });
    assert.match(
      created.body,
      /\*\*Jobs still waiting for a pass:\*\* `lint`$/m,
    );
  });

  it("swallows an 'already_exists' 422 from createLabel and proceeds", async () => {
    const alreadyExistsError = Object.assign(new Error("Validation Failed"), {
      status: 422,
      response: { data: { errors: [{ code: "already_exists" }] } },
    });
    const github = fakeGithub({
      createLabelError: alreadyExistsError,
      existingIssues: [],
    });
    const context = workflowRunContext();

    await notify({ github, context });

    assert.equal(github.calls.createLabel.length, 1);
    assert.equal(github.calls.create.length, 1);
  });

  it("rethrows a 422 from createLabel that is not 'already_exists'", async () => {
    const otherValidationError = Object.assign(new Error("Validation Failed"), {
      status: 422,
      response: { data: { errors: [{ code: "invalid" }] } },
    });
    const github = fakeGithub({ createLabelError: otherValidationError });
    const context = workflowRunContext();

    await assert.rejects(
      () => notify({ github, context }),
      otherValidationError,
    );
    assert.equal(github.calls.create.length, 0);
    assert.equal(github.calls.createComment.length, 0);
  });

  it("rethrows a non-422 error from createLabel", async () => {
    const serverError = Object.assign(new Error("Internal Server Error"), {
      status: 500,
    });
    const github = fakeGithub({ createLabelError: serverError });
    const context = workflowRunContext();

    await assert.rejects(() => notify({ github, context }), serverError);
    assert.equal(github.calls.create.length, 0);
    assert.equal(github.calls.createComment.length, 0);
  });

  it("comments on an existing issue and adds the new failures to its record", async () => {
    const github = fakeGithub({
      existingIssues: [openIssue({ body: recordedBody(["lint"]) })],
      runJobs: [
        { name: "lint", conclusion: "skipped" },
        { name: "format", conclusion: "failure" },
      ],
    });
    const context = workflowRunContext();

    await notify({ github, context });

    assert.equal(github.calls.create.length, 0);
    assert.equal(github.calls.update.length, 1);
    assert.deepEqual(recordIn(github.calls.update[0].body).jobs, [
      "lint",
      "format",
    ]);
    assert.equal(github.calls.update[0].issue_number, 42);

    assert.equal(github.calls.createComment.length, 1);
    const comment = github.calls.createComment[0];
    assert.equal(comment.issue_number, 42);
    assert.match(comment.body, /Failed again/);
    assert.match(comment.body, /deadbeef/);
    assert.match(
      comment.body,
      /\*\*Jobs still waiting for a pass:\*\* `lint`, `format`$/m,
    );

    // The label filter and the open-state filter are what make this issue the
    // bot's own tracker rather than any same-titled issue in the repo.
    assert.deepEqual(github.calls.paginate[0], {
      ...REPO,
      state: "open",
      labels: "ci-failure",
      per_page: 100,
    });
  });

  it("does not treat a same-titled pull request as a matching issue", async () => {
    const github = fakeGithub({
      existingIssues: [
        {
          number: 7,
          title: "CI failure: CI",
          pull_request: { url: "https://api.github.com/pulls/7" },
        },
      ],
    });
    const context = workflowRunContext();

    await notify({ github, context });

    assert.equal(github.calls.createComment.length, 0);
    assert.equal(github.calls.create.length, 1);
    assert.equal(github.calls.create[0].title, "CI failure: CI");
  });

  it("closes the issue when every job it waits on passes, past a by-design skip", async () => {
    const github = fakeGithub({
      // Recorded by an earlier run than this one, which is the ordinary case:
      // a later run of the same workflow clears the issue.
      existingIssues: [
        openIssue({
          body: recordedBody(["label"], { ...RUN_FACTS, id: RUN_ID - 1 }),
        }),
      ],
      // The shape of a healthy `PR meta (privileged)` or `Sync from Template`
      // run: the job that failed passes, and a second job is skipped on every
      // run this notifier sees.
      runJobs: [
        { name: "label", conclusion: "success" },
        { name: "cancel", conclusion: "skipped" },
      ],
    });
    const context = workflowRunContext({ conclusion: "success" });

    await notify({ github, context });

    assert.deepEqual(github.calls.paginate[1], {
      ...REPO,
      run_id: RUN_ID,
      per_page: 100,
    });
    assert.equal(github.calls.create.length, 0);
    assert.equal(github.calls.update.length, 1);

    const update = github.calls.update[0];
    assert.equal(update.issue_number, 42);
    assert.equal(update.owner, REPO.owner);
    assert.equal(update.repo, REPO.repo);
    assert.equal(update.state, "closed");
    assert.equal(update.state_reason, "completed");

    assert.equal(github.calls.createComment.length, 1);
    const comment = github.calls.createComment[0];
    assert.equal(comment.issue_number, 42);
    assert.match(comment.body, /Green again — label passed/);
  });

  it("leaves the issue open when a job it waits on was skipped in the green run", async () => {
    const github = fakeGithub({
      existingIssues: [
        openIssue({
          body: recordedBody(["hook-lifecycle", "hook-lifecycle-passed"]),
        }),
      ],
      // The shape a path-gated workflow produces on an unrelated push: the
      // decide gate and the always() reporter pass, the work job never runs,
      // and the run still concludes `success`.
      runJobs: [
        { name: "decide", conclusion: "success" },
        { name: "hook-lifecycle", conclusion: "skipped" },
        { name: "hook-lifecycle-passed", conclusion: "success" },
      ],
    });
    const context = workflowRunContext({ conclusion: "success" });

    await notify({ github, context });

    assert.equal(github.calls.create.length, 0);
    assert.equal(github.calls.createComment.length, 0);
    assert.equal(github.calls.update.length, 1);
    assert.equal(github.calls.update[0].state, undefined);
    // `hook-lifecycle-passed` passed, so only the untested job is left.
    assert.deepEqual(recordIn(github.calls.update[0].body).jobs, [
      "hook-lifecycle",
    ]);
  });

  it("keeps a recorded job that a later failure skipped", async () => {
    // Two runs, driven end to end: a second failure whose gate stopped at
    // `decide` must not drop `hook-lifecycle`, which has never passed. Taking
    // each run's own failures as the whole list would drop it, and the green
    // run below would then close the issue over an untested breakage.
    const first = fakeGithub({
      existingIssues: [
        openIssue({
          body: recordedBody(["hook-lifecycle", "hook-lifecycle-passed"]),
        }),
      ],
      runJobs: [
        { name: "decide", conclusion: "failure" },
        { name: "hook-lifecycle", conclusion: "skipped" },
        { name: "hook-lifecycle-passed", conclusion: "failure" },
      ],
    });

    await notify({ github: first, context: workflowRunContext() });

    const updated = first.calls.update[0].body;
    assert.deepEqual(recordIn(updated).jobs, [
      "hook-lifecycle",
      "hook-lifecycle-passed",
      "decide",
    ]);

    const second = fakeGithub({
      existingIssues: [openIssue({ body: updated })],
      runJobs: [
        { name: "decide", conclusion: "success" },
        { name: "hook-lifecycle", conclusion: "skipped" },
        { name: "hook-lifecycle-passed", conclusion: "success" },
      ],
    });

    await notify({
      github: second,
      context: workflowRunContext({ conclusion: "success" }),
    });

    assert.equal(second.calls.createComment.length, 0);
    assert.deepEqual(recordIn(second.calls.update[0].body).jobs, [
      "hook-lifecycle",
    ]);
    assert.equal(second.calls.update[0].state, undefined);
  });

  it("names a job no run of the workflow reports any more", async () => {
    const github = fakeGithub({
      existingIssues: [openIssue({ body: recordedBody(["test"]) })],
      // The fix for the failure renamed the job, so nothing named `test` can
      // ever pass and no run can close the issue.
      runJobs: [{ name: "node-tests", conclusion: "success" }],
    });
    const context = workflowRunContext({ conclusion: "success" });

    await notify({ github, context });

    assert.equal(github.calls.createComment.length, 0);
    assert.equal(github.calls.update.length, 1);
    assert.match(
      github.calls.update[0].body,
      /No run of this workflow reports `test` any more/,
    );
    assert.equal(github.calls.update[0].state, undefined);
  });

  it("writes no update when the green run changes nothing", async () => {
    const github = fakeGithub({
      existingIssues: [openIssue({ body: recordedBody(["lint"]) })],
      runJobs: [{ name: "lint", conclusion: "skipped" }],
    });
    const context = workflowRunContext({ conclusion: "success" });

    await notify({ github, context });

    assert.equal(github.calls.createComment.length, 0);
    // The body it would write differs from `recordedBody`'s prose, so assert
    // the rewrite is idempotent by feeding that body back in.
    const rewritten = github.calls.update[0].body;
    const again = fakeGithub({
      existingIssues: [openIssue({ body: rewritten })],
      runJobs: [{ name: "lint", conclusion: "skipped" }],
    });

    await notify({ github: again, context });

    assert.equal(again.calls.update.length, 0);
    assert.equal(again.calls.createComment.length, 0);
  });

  it("leaves an issue that records no failing job open", async () => {
    const github = fakeGithub({
      existingIssues: [openIssue({ body: "Filed by hand, with no record.\n" })],
      runJobs: [{ name: "lint", conclusion: "success" }],
    });
    const context = workflowRunContext({ conclusion: "success" });

    await notify({ github, context });

    assert.equal(github.calls.update.length, 0);
    assert.equal(github.calls.createComment.length, 0);
    // The refusal costs no job call: only the issue lookup ran.
    assert.equal(github.calls.paginate.length, 1);
  });

  it("writes a record onto an issue that has none when the workflow fails again", async () => {
    const github = fakeGithub({
      existingIssues: [openIssue({ body: "Filed by hand, with no record.\n" })],
      runJobs: [{ name: "lint", conclusion: "failure" }],
    });
    const context = workflowRunContext();

    await notify({ github, context });

    assert.equal(github.calls.update.length, 1);
    assert.deepEqual(recordIn(github.calls.update[0].body).jobs, ["lint"]);
    assert.equal(github.calls.createComment.length, 1);
  });

  it("leaves the issue open when the green run predates the newest failure", async () => {
    const github = fakeGithub({
      existingIssues: [
        openIssue({
          body: recordedBody(["lint"], { ...RUN_FACTS, id: RUN_ID + 1 }),
        }),
      ],
      runJobs: [{ name: "lint", conclusion: "success" }],
    });
    const context = workflowRunContext({ conclusion: "success" });

    await notify({ github, context });

    assert.equal(github.calls.update.length, 0);
    assert.equal(github.calls.createComment.length, 0);
    // The refusal costs no job call: only the issue lookup ran.
    assert.equal(github.calls.paginate.length, 1);
  });

  it("closes the issue when the recorded run itself is re-run and passes", async () => {
    // "Re-run failed jobs" keeps the run id, so the guard above must let the
    // recorded run's own later attempt through.
    const github = fakeGithub({
      existingIssues: [openIssue({ body: recordedBody(["lint"]) })],
      runJobs: [{ name: "lint", conclusion: "success" }],
    });
    const context = workflowRunContext({ conclusion: "success" });

    await notify({ github, context });

    assert.equal(github.calls.update[0].state, "closed");
    assert.match(
      github.calls.createComment[0].body,
      /Green again — lint passed/,
    );
  });

  it("refuses a recorded run id it cannot read", async () => {
    const github = fakeGithub({
      existingIssues: [
        openIssue({
          body: recordedBody(["lint"], { ...RUN_FACTS, id: "latest" }),
        }),
      ],
      runJobs: [{ name: "lint", conclusion: "success" }],
    });
    const context = workflowRunContext({ conclusion: "success" });

    await assert.rejects(
      () => notify({ github, context }),
      /Unreadable recorded run id "latest"/,
    );
    assert.equal(github.calls.update.length, 0);
  });

  // Every member of the script's failing-run set must file an issue. Exercising
  // `failure` alone would let a dropped member through: the script would then
  // throw inside github-script, and no workflow watches the notifier itself.
  for (const conclusion of [
    "action_required",
    "failure",
    "startup_failure",
    "timed_out",
  ]) {
    it(`files an issue for a ${conclusion} run`, async () => {
      const github = fakeGithub({
        existingIssues: [],
        runJobs: [{ name: "lint", conclusion: "failure" }],
      });
      const context = workflowRunContext({ conclusion });

      await notify({ github, context });

      assert.equal(github.calls.create.length, 1);
      assert.match(
        github.calls.create[0].body,
        new RegExp(`\\*\\*Conclusion:\\*\\* ${conclusion}$`, "m"),
      );
    });
  }

  it("closes a startup failure once the workflow runs and passes", async () => {
    // A run that fails before any job starts reports no failing job, so the
    // claim is only "this run could not start". A later run that started and
    // concluded success falsifies exactly that.
    const filing = fakeGithub({ existingIssues: [], runJobs: [] });

    await notify({
      github: filing,
      context: workflowRunContext({ conclusion: "startup_failure" }),
    });

    const filed = filing.calls.create[0].body;
    assert.deepEqual(recordIn(filed).jobs, []);
    assert.match(filed, /failed before any job started/);

    const green = fakeGithub({
      existingIssues: [openIssue({ body: filed })],
      runJobs: [{ name: "lint", conclusion: "success" }],
    });

    await notify({
      github: green,
      context: workflowRunContext({ conclusion: "success" }),
    });

    assert.equal(green.calls.update[0].state, "closed");
    assert.match(
      green.calls.createComment[0].body,
      /Green again — the workflow started and passed/,
    );
  });

  it("leaves another workflow's issue open when this one goes green", async () => {
    const github = fakeGithub({
      existingIssues: [
        openIssue({ number: 42, title: "CI failure: Lint", body: "" }),
      ],
      runJobs: [{ name: "lint", conclusion: "success" }],
    });
    const context = workflowRunContext({ conclusion: "success" });

    await notify({ github, context });

    assert.equal(github.calls.update.length, 0);
    assert.equal(github.calls.createComment.length, 0);
    assert.equal(github.calls.create.length, 0);
    assert.equal(github.calls.createLabel.length, 0);
  });

  it("files nothing when a green run finds no open issue", async () => {
    const github = fakeGithub({ existingIssues: [] });
    const context = workflowRunContext({ conclusion: "success" });

    await notify({ github, context });

    assert.equal(github.calls.create.length, 0);
    assert.equal(github.calls.update.length, 0);
    assert.equal(github.calls.createComment.length, 0);
    // No issue to clear, so the run's jobs are never fetched.
    assert.equal(github.calls.paginate.length, 1);
  });

  it("refuses a conclusion it does not model", async () => {
    const github = fakeGithub({ existingIssues: [] });
    const context = workflowRunContext({ conclusion: "neutral" });

    await assert.rejects(() => notify({ github, context }), /neutral/);

    assert.equal(github.calls.create.length, 0);
    assert.equal(github.calls.createComment.length, 0);
    assert.equal(github.calls.createLabel.length, 0);
  });

  it("throws and calls nothing when workflow_run is missing", async () => {
    const github = fakeGithub();
    const context = { repo: REPO, payload: {} };

    await assert.rejects(() => notify({ github, context }), /workflow_run/);

    assert.equal(github.calls.createLabel.length, 0);
    assert.equal(github.calls.createComment.length, 0);
    assert.equal(github.calls.create.length, 0);
  });
});
