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
By default the action does not merge. It never approves PRs, publishes releases,
or deploys sites. The optional merge policy is described below.

## Optional merge authority

Consumers may explicitly set the boolean `auto_merge` in their trusted config.
The default is false, and prepare emits the opt-in so the merge job is skipped
entirely otherwise. The merge job runs after successful validation and reporting,
checks out the original trusted default-branch SHA with no persisted credentials,
and never runs candidate code. It has contents write, pull-requests read, and
actions read permissions. Test jobs never receive this merge token.

Immediately before merging, the controller independently rechecks the PR, signed
bot commit, published upstream tag, configured workflow paths and live run results,
branch heads, and active pull-request/strict status-check rulesets. It passes the
expected head SHA to GitHub's normal squash-merge endpoint. No bot approval or
protection bypass is used. Repository rules must have no bot bypass; administrators
remain responsible for protecting those rules and the trusted workflow/config.

Token scopes cannot restrict contents write to one file or one operation. The
pin-only restriction is controller policy, backed by required PRs and checks; it
is not a native GitHub file-scoped merge permission. All consumers share the
GitHub Actions bot identity, so the identity check alone is not proof that a
particular workflow created a commit. The shape, trusted base, signature, upstream
release, and independently checked validation are also required.

Both prepare and merge explicitly restrict their jobs to schedule/manual events
on the default branch (and reject tags). Every non-check controller invocation
also validates that event/ref boundary before performing any API or git operation.
A caller triggered by a pull request cannot rely on dependency-job conditions to
reach a privileged merge checkout.
