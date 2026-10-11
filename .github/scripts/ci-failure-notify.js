// @ts-check
"use strict";

const LABEL = "ci-failure";

// The conclusions this script reports as a failure. Anything else but
// `success` is a word it does not model, and it refuses rather than filing an
// issue whose body contradicts its title.
const FAILED = new Set([
  "action_required",
  "failure",
  "startup_failure",
  "timed_out",
]);

/**
 * File, update, or close the tracking issue for a post-merge or scheduled
 * workflow run. One open issue per workflow name: a repeat failure adds a
 * comment instead of a new issue, and the workflow's next verified success on
 * the default branch closes it.
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
  if (run.conclusion !== "success" && !FAILED.has(run.conclusion)) {
    throw new Error(
      `Unmodeled workflow_run conclusion "${run.conclusion}" for ${run.name} — ` +
        "the workflow's own `if:` decides which conclusions reach this script.",
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
    // A path-gated workflow concludes `success` through its always() reporter
    // when its decide gate skipped the work job, so the run's own conclusion
    // cannot say the tree was verified — closing on one is a vacuous green.
    // Require every job of the run to have run and passed. `verified_head` in
    // decide-memo-base.py answers the same question for the memo base, by job
    // name, because it knows which workflow it is reading.
    const jobs = await github.paginate(
      github.rest.actions.listJobsForWorkflowRun,
      { ...context.repo, run_id: run.id, per_page: 100 },
    );
    const skipped = jobs.filter((job) => job.conclusion !== "success");
    if (jobs.length === 0 || skipped.length > 0) {
      console.log(
        `Run ${run.id} concluded success without running ` +
          `${skipped.map((job) => job.name).join(", ") || "any job"}; ` +
          `leaving issue #${existing.number} open`,
      );
      return;
    }
    await github.rest.issues.createComment({
      ...context.repo,
      issue_number: existing.number,
      body: `Green again.\n\n${runLine}`,
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

  if (existing) {
    await github.rest.issues.createComment({
      ...context.repo,
      issue_number: existing.number,
      body: `Failed again.\n\n${runLine}`,
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
    "",
    "This issue is filed automatically for post-merge (`push`) and " +
      "`schedule` runs only — failures on those runs have no PR to surface " +
      "them, so without this notification they rot unnoticed. The next " +
      `success of **${run.name}** on \`${run.head_branch}\` closes this ` +
      "issue; further failures while it stays open are added as comments.",
  ].join("\n");

  const created = await github.rest.issues.create({
    ...context.repo,
    title,
    body,
    labels: [LABEL],
  });
  console.log(`Created issue #${created.data.number}`);
};
