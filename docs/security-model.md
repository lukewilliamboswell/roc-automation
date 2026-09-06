# Automation trust model

Consumers pin both reusable workflows to a reviewed full SHA. Each shared workflow
pins the controller action and checkout action to full SHAs. The controller source
runs from `github.action_path`; configuration and git operations use the caller's
`GITHUB_WORKSPACE`. Consumer Python files are never imported by the controller.

The scheduled/manual caller must run on the default branch. Prepare records that
trusted checkout's commit and creates one pin-only commit on `automation/roc-nightly`.
It refuses an existing branch tip with other changed files and uses an exact git
lease. GitHub's commit API signs the pin commit, and the controller verifies that
GitHub reports the signature as verified.

The caller grants a permission ceiling. Actual jobs reduce it:

| Job | Token access |
| --- | --- |
| prepare | contents: write; pull-requests: write |
| validate | contents: read; actions: write |
| report | contents: read; pull-requests: write |
| configuration check | contents: read |

The controller uses `GH_TOKEN` from `github.token`, `DEFAULT_BRANCH`, and GitHub's
standard repository/workspace/run environment. Prepare additionally accepts `FORCE`
for an explicit retry. Its outputs are `changed`, `sha`, and `nightly`. Validate
accepts `CANDIDATE_SHA` and emits JSON `runs` with workflow, ID, URL, and conclusion.
Report accepts that SHA, `NIGHTLY_TAG`, `TEST_RESULT`, and `VALIDATION_RUNS`.

Validation dispatches workflows on the candidate branch with
`nightly_validation: true`. GitHub API version 2026-03-10 returns their exact run
IDs. The controller verifies run commit, branch, and event, waits for all results,
and refuses stale branch heads. Missing, failed, cancelled, skipped, timed-out,
or mismatched evidence never becomes a passing report. Previous success is cleared
before a new candidate is prepared. A timeout links to runs for diagnosis.

A workflow dispatch executes project code under that validation workflow's own
permissions. Each consumer must keep test jobs read-only and guard every release
or deployment job. This is a required review boundary, not something the controller
can infer from a green workflow result. PR review and branch protection remain
maintainer responsibilities; this automation grants no bypass or self-approval.

No credentials are persisted by checkout. Authenticated git pushes receive a
short-lived token in the child process environment, never command-line arguments.
The action does not merge, approve, publish releases, or deploy sites.
