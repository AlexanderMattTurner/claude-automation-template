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

// The job conclusions that put a job on the issue's waiting list. `skipped` is
// not among them: a workflow can skip a job by design on every run this
// notifier sees, and demanding that job pass would keep the issue open forever.
const FAILED_JOB = new Set([
  "action_required",
  "cancelled",
  "failure",
  "timed_out",
]);

// Opens the record this script writes into the issue body and reads back. The
// body's visible text is rendered from that record, so the list a human reads
// is the list the close decision uses. The record lives in the body alone: a
// body is one current statement, where a record spread over a comment thread
// lets the newest paste decide.
const MARKER = "<!-- ci-failure-notify:";
const MARKER_END = "-->";

/** Every job of a run's latest attempt, as `{name, conclusion}` records. */
async function runJobs(github, context, runId) {
  return github.paginate(github.rest.actions.listJobsForWorkflowRun, {
    ...context.repo,
    run_id: runId,
    per_page: 100,
  });
}

/** The record `body` carries, or null when this script has written none. */
function readRecord(body) {
  const line = (body || "")
    .split("\n")
    .find((candidate) => candidate.startsWith(MARKER));
  if (!line) {
    return null;
  }
  return JSON.parse(line.slice(MARKER.length, line.lastIndexOf(MARKER_END)));
}

/** A timestamp in milliseconds. Throws, because NaN loses every comparison. */
function parseStamp(value, label) {
  const stamp = Date.parse(value);
  if (Number.isNaN(stamp)) {
    throw new Error(`Unreadable ${label} "${value}".`);
  }
  return stamp;
}

/**
 * The job names still waiting for a pass: those already recorded, plus the ones
 * this run failed, less the ones it passed.
 *
 * A job leaves the list only when a run is OBSERVED to pass it. Taking each
 * run's failures as the whole list instead would drop a job the run skipped,
 * and the next green run would then close the issue over an untested failure.
 */
function mergeJobs(recorded, jobs) {
  const passed = new Set(
    jobs.filter((job) => job.conclusion === "success").map((job) => job.name),
  );
  const failed = jobs
    .filter((job) => FAILED_JOB.has(job.conclusion))
    .map((job) => job.name);
  return [...new Set([...recorded, ...failed])].filter(
    (name) => !passed.has(name),
  );
}

/** The waiting list as the issue shows it. */
function waitingLine(jobs) {
  const names = jobs.map((name) => `\`${name}\``).join(", ");
  return `**Jobs still waiting for a pass:** ${
    names || "none — the run failed before any job started"
  }`;
}

/** The whole issue body, rendered from the record so the two cannot disagree. */
function renderBody({ run, record, absent }) {
  const stalemate =
    absent.length > 0
      ? [
          "",
          `**No run of this workflow reports ${absent
            .map((name) => `\`${name}\``)
            .join(", ")} any more.** A renamed or deleted job cannot pass, ` +
            "so no run can close this issue — close it by hand.",
        ]
      : [];
  return [
    `The **${run.name}** workflow failed on \`${run.head_branch}\`.`,
    "",
    `${MARKER}${JSON.stringify(record)} ${MARKER_END}`,
    waitingLine(record.jobs),
    "",
    `**Latest failing run:** ${record.run.url}`,
    `**Conclusion:** ${record.run.conclusion}`,
    `**Head SHA:** ${record.run.sha}`,
    `**Event:** ${record.run.event}`,
    ...stalemate,
    "",
    "This issue is filed automatically for post-merge (`push`) and " +
      "`schedule` runs only. A failure on one of those has no PR to surface " +
      "it, so without this notice it rots unnoticed. A later run of " +
      `**${run.name}** on \`${run.head_branch}\` closes this issue when it ` +
      "starts after the newest failure and passes every job listed above. A " +
      "repeat failure adds a comment and updates that list.",
  ].join("\n");
}

/**
 * File, update, or close the tracking issue for a post-merge or scheduled
 * workflow run. One open issue per workflow name: a repeat failure comments and
 * updates the issue instead of filing a second one, and a later run that passes
 * every job the issue still waits on closes it.
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
  const startedAt = parseStamp(run.run_started_at, "run_started_at");
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
  const record = existing ? readRecord(existing.body) : null;

  // A run that started before the newest failure the issue records tested an
  // older tree, so neither its passes nor its failures speak to that failure.
  if (record && startedAt < parseStamp(record.since, "recorded failure time")) {
    console.log(
      `Run ${run.id} started before the newest failure on issue #${existing.number}; leaving it as it is`,
    );
    return;
  }

  if (run.conclusion === "success") {
    if (!existing) {
      console.log(`No open ${LABEL} issue for ${run.name}`);
      return;
    }
    if (!record) {
      console.log(
        `Issue #${existing.number} records no failing job; leaving it open`,
      );
      return;
    }
    // The run's own conclusion cannot stand in for this: a path-gated workflow
    // concludes `success` through its always() reporter when the decide gate
    // skipped the work job. Closing on that is a vacuous green, so each job the
    // issue waits on has to pass in this run. `verified_head` in
    // decide-memo-base.py answers the same question for the memo base.
    const jobs = await runJobs(github, context, run.id);
    const waiting = mergeJobs(record.jobs, jobs);

    if (waiting.length === 0) {
      const cleared =
        record.jobs.length > 0
          ? `${record.jobs.join(", ")} passed`
          : "the workflow started and passed";
      await github.rest.issues.createComment({
        ...context.repo,
        issue_number: existing.number,
        body: `Green again — ${cleared}.\n\n${runLine}`,
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

    // A job this run proved drops off the list even when others remain, so two
    // path-gated jobs that failed together need not pass together.
    const present = new Set(jobs.map((job) => job.name));
    const body = renderBody({
      run,
      record: { ...record, jobs: waiting },
      absent: waiting.filter((name) => !present.has(name)),
    });
    console.log(
      `Run ${run.id} left ${waiting.join(", ")} unproven on issue #${existing.number}`,
    );
    if (body !== existing.body) {
      await github.rest.issues.update({
        ...context.repo,
        issue_number: existing.number,
        body,
      });
    }
    return;
  }

  const jobs = await runJobs(github, context, run.id);
  const next = {
    jobs: mergeJobs(record ? record.jobs : [], jobs),
    since: run.run_started_at,
    run: {
      url: run.html_url,
      conclusion: run.conclusion,
      sha: run.head_sha,
      event: run.event,
    },
  };
  const body = renderBody({ run, record: next, absent: [] });

  if (existing) {
    await github.rest.issues.update({
      ...context.repo,
      issue_number: existing.number,
      body,
    });
    await github.rest.issues.createComment({
      ...context.repo,
      issue_number: existing.number,
      body: `Failed again.\n\n${runLine}\n${waitingLine(next.jobs)}`,
    });
    console.log(`Updated existing issue #${existing.number}`);
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

  const created = await github.rest.issues.create({
    ...context.repo,
    title,
    body,
    labels: [LABEL],
  });
  console.log(`Created issue #${created.data.number}`);
};
