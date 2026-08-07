/**
 * Shared pipeline alerting for the scheduled workflows.
 *
 * Replaces three copies of a dedupe that keyed on "any open issue labelled
 * pipeline-alert". Under that rule issue #8 — opened 2026-07-21 for a
 * stats.wnba.com failure that no longer exists — absorbed every later alert as
 * a comment. Seventeen of them, across two different root causes, under a title
 * that stayed permanently wrong. Nothing in any workflow ever closed it, so the
 * channel was open by construction and no new notification could ever fire.
 *
 * What changed:
 *   - dedupe on a per-failure-class KEY carried in an HTML marker, so an
 *     extraction failure and an audit failure are different threads;
 *   - a succeeding run CLOSES its own thread, so the next failure is new again;
 *   - the title is rewritten when the failure class recurs, so it never lies;
 *   - pull requests are filtered out (listForRepo returns them too);
 *   - results are paginated rather than trusting the first page.
 *
 * Usage from a workflow:
 *   - uses: actions/github-script@v7
 *     with:
 *       script: |
 *         const alert = require('./.github/scripts/pipeline-alert.js')
 *         await alert({github, context}, {key: 'extract', title: '...', body: '...'})
 */

const LABEL = 'pipeline-alert';

function marker(key) {
  return `<!-- alert-key: ${key} -->`;
}

function runUrl(context) {
  const { owner, repo } = context.repo;
  return `https://github.com/${owner}/${repo}/actions/runs/${context.runId}`;
}

async function openAlerts({ github, context }) {
  const { owner, repo } = context.repo;
  const all = await github.paginate(github.rest.issues.listForRepo, {
    owner, repo, state: 'open', labels: LABEL, per_page: 100,
  });
  // listForRepo returns pull requests as issues; a PR carrying the label would
  // otherwise swallow every alert exactly as #8 did.
  return all.filter((issue) => !issue.pull_request);
}

/**
 * @param {{key: string, title?: string, body?: string, resolved?: boolean}} opts
 */
module.exports = async function alert({ github, context }, opts) {
  const { key, title, body, resolved = false } = opts;
  if (!key) throw new Error('pipeline-alert: a failure-class key is required');

  const { owner, repo } = context.repo;
  const tag = marker(key);
  const issues = await openAlerts({ github, context });
  const mine = issues.filter((i) => (i.body || '').includes(tag));

  if (resolved) {
    // Close this failure class's own thread, plus any legacy alert that
    // predates keying — a green run means nothing is outstanding, and leaving
    // an unkeyed issue open re-arms the original trap.
    const legacy = issues.filter((i) => !/<!-- alert-key: /.test(i.body || ''));
    for (const issue of [...mine, ...legacy]) {
      await github.rest.issues.createComment({
        owner, repo, issue_number: issue.number,
        body: `Resolved: a subsequent \`${key}\` run succeeded. ${runUrl(context)}`,
      });
      await github.rest.issues.update({
        owner, repo, issue_number: issue.number, state: 'closed',
      });
    }
    return { closed: mine.length + legacy.length };
  }

  const fullBody = `${tag}\n\n${body}\n\nRun: ${runUrl(context)}`;
  if (mine.length > 0) {
    const issue = mine[0];
    await github.rest.issues.createComment({
      owner, repo, issue_number: issue.number, body: fullBody,
    });
    if (title && issue.title !== title) {
      // The failure class recurred with different detail; the title must
      // describe the CURRENT failure, not the first one ever seen.
      await github.rest.issues.update({
        owner, repo, issue_number: issue.number, title,
      });
    }
    return { commented: issue.number };
  }

  const created = await github.rest.issues.create({
    owner, repo, title, body: fullBody, labels: [LABEL],
  });
  return { created: created.data.number };
};

module.exports.LABEL = LABEL;
module.exports.marker = marker;
