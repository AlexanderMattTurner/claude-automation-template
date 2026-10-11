// @ts-check
"use strict";

const LABEL = "ci-failure";

// The run conclusions this script reports as a failure. Anything but these and
// `success` is a word it does not model.
const FAILED_RUN = new Set([
  "action_required",
  "failure",
  "startup_failure",
  "timed_out",
]);

// The job conclusions that make a job one the issue is about. `skipped` is not
// among them: a workflow can skip a job by design on every run this notifier
// sees, and demanding that job pass would keep the issue open forever.
const FAILED_JOB = new Set([
  "action_required",
  "cancelled",
  "failure",
  "timed_out",
]);

// Carries the failing job names from the failure that files the issue to the
// success that closes it. A repeat failure writes its own line, and the last
// line in the thread is the live set.
const JOBS_LINE = "**Jobs that failed:**";

/** Every job of a run, latest attempt, as `{name, conclusion}` records. */
async function runJobs(github, context, runId) {
  return github.paginate(github.rest.actions.listJobsForWorkflowRun, {
    ...context.repo,
    run_id: runId,
    per_page: 100,
  });
}

/** The job names a `JOBS_LINE` carries; empty when the text has no such line. */
function parseJobsLine(text) {
  const line = (text || "")
    .split("\n")
    .find((candidate) => candidate.startsWith(JOBS_LINE));
  return line
    ? [...line.matchAll(/`(?<job>[^`]+)`/g)].map((match) => match.groups.job)
    : [];
}

/**
 * The failing job names last recorded on the issue, or null when none is.
 *
 * An issue filed before this script recorded them, or by a run that reported
 * no failing job, yields null — there is then nothing a green run could prove,
 * so the issue waits for a human.
 */
async function recordedJobs(github, context, issue) {
  const comments = await github.paginate(github.rest.issues.listComments, {
    ...context.repo,
    issue_number: issue.number,
    per_page: 100,
  });
  let recorded = null;
  for (const text of [issue.body, ...comments.map((comment) => comment.body)]) {
    const found = parseJobsLine(text);
    if (found.length > 0) {
      recorded = found;
    }
  }
  return recorded;
}

/**
 * File, update, or close the tracking issue for a post-merge or scheduled
 * workflow run. One open issue per workflow name: a repeat failure adds a
 * comment instead of a new issue, and a later run that passes every job the
 * issue records closes it.
 *
 * Called by the ci-failure-notify workflow via actions/github-script.
 *
 * @param {object} params
 * @param {object} params.github  - Authenticated Octokit client
 * @param {object} params.context - GitHub Actions webhook event context
 */
module.exports = async ({ github, context }) => {
  const run = context.payload.workflow_run;
  if (!run) {
    throw new Error(
      "No workflow_run payload on this event — expected a workflow_run event.",
    );
  }
  if (run.conclusion !== "success" && !FAILED_RUN.has(run.conclusion)) {
    throw new Error(
      `Unmodeled workflow_run conclusion "${run.conclusion}" for ${run.name}.`,
    );
  }
  const title = `CI failure: ${run.name}`;

  const runLine = [
    `**Run:** ${run.html_url}`,
    `**Conclusion:** ${run.conclusion}`,
    `**Head SHA:** ${run.head_sha}`,
    `**Event:** ${run.event}`,
  ].join("\n");

  const openIssues = await github.paginate(github.rest.issues.listForRepo, {
    ...context.repo,
    state: "open",
    labels: LABEL,
    per_page: 100,
  });
  // listForRepo returns pull requests too; only real issues are dedup targets.
  const existing = openIssues.find(
    (issue) => !issue.pull_request && issue.title === title,
  );

  if (run.conclusion === "success") {
    if (!existing) {
      console.log(`No open ${LABEL} issue for ${run.name}`);
      return;
    }
    // A run that started before the issue's last notice tested an older tree,
    // so its green says nothing about the failure that notice describes. The
    // next green run closes the issue instead.
    if (new Date(run.run_started_at) < new Date(existing.updated_at)) {
      console.log(
        `Run ${run.id} started before issue #${existing.number} was last updated; leaving it open`,
      );
      return;
    }
    const recorded = await recordedJobs(github, context, existing);
    if (!recorded) {
      console.log(
        `Issue #${existing.number} records no failing job; leaving it open`,
      );
      return;
    }
    // The run's own conclusion cannot stand in for this: a path-gated workflow
    // concludes `success` through its always() reporter when the decide gate
    // skipped the work job. Closing on that is a vacuous green, so each job the
    // issue is about has to pass in this run. `verified_head` in
    // decide-memo-base.py answers the same question for the memo base.
    const passed = new Set(
      (await runJobs(github, context, run.id))
        .filter((job) => job.conclusion === "success")
        .map((job) => job.name),
    );
    const outstanding = recorded.filter((name) => !passed.has(name));
    if (outstanding.length > 0) {
      console.log(
        `Run ${run.id} did not pass ${outstanding.join(", ")}; leaving issue #${existing.number} open`,
      );
      return;
    }
    await github.rest.issues.createComment({
      ...context.repo,
      issue_number: existing.number,
      body: `Green again — ${recorded.join(", ")} passed.\n\n${runLine}`,
    });
    await github.rest.issues.update({
      ...context.repo,
      issue_number: existing.number,
      state: "closed",
      state_reason: "completed",
    });
    console.log(`Closed issue #${existing.number}`);
    return;
  }

  const failing = (await runJobs(github, context, run.id))
    .filter((job) => FAILED_JOB.has(job.conclusion))
    .map((job) => job.name);
  const jobsLine = `${JOBS_LINE} ${
    failing.map((name) => `\`${name}\``).join(", ") || "none reported"
  }`;

  if (existing) {
    await github.rest.issues.createComment({
      ...context.repo,
      issue_number: existing.number,
      body: `Failed again.\n\n${runLine}\n${jobsLine}`,
    });
    console.log(`Commented on existing issue #${existing.number}`);
    return;
  }

  // issues.create silently drops labels the caller cannot create, so ensure
  // the label exists first; a 422 "already_exists" means another run (or a
  // human) beat us to it.
  try {
    await github.rest.issues.createLabel({
      ...context.repo,
      name: LABEL,
      color: "b60205",
      description: "A post-merge or scheduled workflow run failed",
    });
  } catch (error) {
    const alreadyExists =
      error.status === 422 &&
      (error.response?.data?.errors || []).some(
        (e) => e.code === "already_exists",
      );
    if (!alreadyExists) {
      throw error;
    }
  }

  const body = [
    `The **${run.name}** workflow failed on \`${run.head_branch}\`.`,
    "",
    runLine,
    jobsLine,
    "",
    "This issue is filed automatically for post-merge (`push`) and " +
      "`schedule` runs only — failures on those runs have no PR to surface " +
      "them, so without this notification they rot unnoticed. A later run of " +
      `**${run.name}** on \`${run.head_branch}\` that passes every job above ` +
      "closes this issue; further failures while it stays open are added as " +
      "comments.",
  ].join("\n");

  const created = await github.rest.issues.create({
    ...context.repo,
    title,
    body,
    labels: [LABEL],
  });
  console.log(`Created issue #${created.data.number}`);
};
