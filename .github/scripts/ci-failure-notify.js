// @ts-check
"use strict";

const LABEL = "ci-failure";

/**
 * File, update, or close the tracking issue for a post-merge or scheduled
 * workflow run. One open issue per workflow name: a repeat failure adds a
 * comment instead of a new issue, and the workflow's next success on the
 * default branch closes it. The success leg is what makes the open issue mean
 * "still broken" — without it the issue reports a failure that is over.
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

  // Only a green run clears the failure. The workflow passes `cancelled` and
  // `skipped` through to neither leg, because a run that posted no verdict
  // says nothing about whether the failure is fixed.
  if (run.conclusion === "success") {
    if (!existing) {
      console.log(`No open ${LABEL} issue for ${run.name}`);
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
