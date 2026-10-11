// Behavior tests for the CI-failure tracking-issue filer: does a failed
// post-merge/scheduled workflow_run produce (or update) the right tracking
// issue, and does a later run that passes the jobs it recorded close it?
// Drives the real module (no re-implementation) and asserts the observable
// calls made against a fake Octokit-shaped `github` client — the real
// dependency is the GitHub API, which no test can reach.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const notify = require("./ci-failure-notify.js");

const REPO = { owner: "owner", repo: "downstream-repo" };
const FILED_AT = "2026-01-01T00:00:00Z";
const LATER = "2026-01-02T00:00:00Z";

/** An open tracking issue, as `issues.listForRepo` returns one. */
function openIssue({ number = 42, title = "CI failure: CI", body = "" } = {}) {
  return { number, title, body, updated_at: FILED_AT, pull_request: undefined };
}

/** Build a fake Octokit-shaped `github` client that records every call. */
function fakeGithub({
  createLabelError,
  existingIssues = [],
  issueComments = [],
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
        // These two are referenced only as paginate() arguments; the fake
        // paginate below dispatches on which lister it was handed.
        listForRepo() {},
        listComments() {},
      },
      actions: { listJobsForWorkflowRun() {} },
    },
    async paginate(lister, params) {
      calls.paginate.push(params);
      if (lister === github.rest.actions.listJobsForWorkflowRun) {
        return runJobs;
      }
      return lister === github.rest.issues.listComments
        ? issueComments
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
        id: 12345,
        name: "CI",
        html_url: "https://github.com/owner/downstream-repo/actions/runs/1",
        conclusion: "failure",
        head_sha: "deadbeef",
        event: "push",
        head_branch: "main",
        run_started_at: LATER,
        ...overrides,
      },
    },
  };
}

describe("ci-failure-notify", () => {
  it("creates a new labeled issue naming the jobs that failed", async () => {
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
    assert.match(
      created.body,
      /https:\/\/github\.com\/owner\/downstream-repo\/actions\/runs\/1/,
    );
    assert.match(created.body, /failure/);
    assert.match(created.body, /deadbeef/);
    assert.match(created.body, /push/);
    assert.match(created.body, /main/);
    // Only the job that failed is recorded: a skipped job may be skipped by
    // design on every run, and demanding it pass would never close the issue.
    assert.match(created.body, /\*\*Jobs that failed:\*\* `lint`$/m);
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

  it("comments on an existing open matching issue instead of creating one", async () => {
    const github = fakeGithub({
      existingIssues: [openIssue()],
      runJobs: [{ name: "lint", conclusion: "failure" }],
    });
    const context = workflowRunContext();

    await notify({ github, context });

    assert.equal(github.calls.create.length, 0);
    assert.equal(github.calls.update.length, 0);
    assert.equal(github.calls.createComment.length, 1);

    const comment = github.calls.createComment[0];
    assert.equal(comment.issue_number, 42);
    assert.equal(comment.owner, REPO.owner);
    assert.equal(comment.repo, REPO.repo);
    assert.match(comment.body, /Failed again/);
    assert.match(comment.body, /deadbeef/);
    assert.match(comment.body, /\*\*Jobs that failed:\*\* `lint`$/m);

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

  it("closes the issue when every recorded job passes, past a by-design skip", async () => {
    const github = fakeGithub({
      existingIssues: [openIssue({ body: "**Jobs that failed:** `label`\n" })],
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

    assert.deepEqual(github.calls.paginate[2], {
      ...REPO,
      run_id: 12345,
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

  it("leaves the issue open when a recorded job was skipped in the green run", async () => {
    const github = fakeGithub({
      existingIssues: [
        openIssue({
          body: "**Jobs that failed:** `hook-lifecycle`, `hook-lifecycle-passed`\n",
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

    assert.equal(github.calls.update.length, 0);
    assert.equal(github.calls.createComment.length, 0);
    assert.equal(github.calls.create.length, 0);
  });

  it("takes the newest recorded job list, not the issue body's", async () => {
    const github = fakeGithub({
      existingIssues: [
        openIssue({ body: "**Jobs that failed:** `old-job`\n" }),
      ],
      issueComments: [
        { body: "Failed again.\n**Jobs that failed:** `new-job`\n" },
      ],
      runJobs: [{ name: "new-job", conclusion: "success" }],
    });
    const context = workflowRunContext({ conclusion: "success" });

    await notify({ github, context });

    assert.equal(github.calls.update.length, 1);
    assert.match(github.calls.createComment[0].body, /new-job passed/);
  });

  it("leaves the issue open when it records no failing job", async () => {
    const github = fakeGithub({
      existingIssues: [
        openIssue({ body: "**Jobs that failed:** none reported\n" }),
      ],
      runJobs: [{ name: "lint", conclusion: "success" }],
    });
    const context = workflowRunContext({ conclusion: "success" });

    await notify({ github, context });

    assert.equal(github.calls.update.length, 0);
    assert.equal(github.calls.createComment.length, 0);
  });

  it("leaves the issue open when the green run predates its last notice", async () => {
    const github = fakeGithub({
      existingIssues: [openIssue({ body: "**Jobs that failed:** `lint`\n" })],
      runJobs: [{ name: "lint", conclusion: "success" }],
    });
    const context = workflowRunContext({
      conclusion: "success",
      run_started_at: "2025-12-31T00:00:00Z",
    });

    await notify({ github, context });

    assert.equal(github.calls.update.length, 0);
    assert.equal(github.calls.createComment.length, 0);
    // The refusal costs no job or comment call: only the issue lookup ran.
    assert.equal(github.calls.paginate.length, 1);
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
    // No issue to clear, so neither the comments nor the jobs are fetched.
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
