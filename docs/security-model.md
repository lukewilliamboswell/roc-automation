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
| validate | contents: read; actions: write; statuses: write |
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
entirely otherwise. The merge job runs after successful validation and reporting.
It performs no consumer checkout: it reads configuration through the API at the
original default-branch event SHA and executes only the pinned shared controller.
It has contents write, pull-requests read, and actions read permissions. Test jobs
never receive this merge token.

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

All controller jobs explicitly restrict execution to schedule/manual events
on the default branch (and reject tags). Every non-check controller invocation
also validates that event/ref boundary before performing any API or git operation.
Prepare, validate, and report use checkout's original event commit under these
explicit event/ref guards and verify it equals the event SHA before doing work. The merge job reads that
immutable SHA through the API and checks the live default branch before merging.
If the branch moves, retry the updater on its current commit.


Opted-in validation publishes pending commit statuses for the active ruleset's
required Actions contexts, then publishes success only after the exact dispatched
runs and all corresponding jobs have succeeded. Missing, skipped, or failed jobs
cannot produce success. This is needed because GitHub can leave dispatched check
runs unattached to an Actions-bot PR. Statuses use the same check names and Actions
identity; they do not replace or bypass the required checks. The merge job has no
status-write permission and independently rechecks the run and job evidence.

## Read-only release policy

`actions/check-release` is separate from the nightly controller. It requires an
explicit dispatch and exact event checkout, reads the compiler pin from that
checkout, and defaults to a final compiler version for publication. It uses
`github.token` to read `roc-lang/roc` release metadata for the exact pin spelling;
missing releases, drafts, prereleases and releases without assets are rejected.
Compiler compatibility branches must match the compiler line. Optional default-
branch permission alone does not permit nightly publication. A separate default-off
`allow-nightly-bootstrap` input permits an exact nightly only on the actual default
branch; metadata then comes from `roc-lang/nightlies` with the same published
release and asset checks. Compatibility branches cannot use this exception. Remove
the bootstrap opt-in when the consumer adopts the intended stable-only policy.

This action has no write operations, consumer Python imports, approval, or bypass.
The official release metadata is upstream evidence, not verification of compiler
binary integrity. Consumers retain compiler installation, actual version checks,
tests, artifact identity, tag uniqueness, and publication permissions.

## Header compiler roots

Consumers may set `compiler_roots` in trusted `.github/roc-nightly.json` to select
exact source-root paths instead of legacy `.roc-version`. This is an edit-authority
list, not a version registry; versions come from literal `roc` fields in the
selected app/package/platform headers. Public-example roots belong in the list
when testing released dependencies with each new nightly; their dependency URLs
remain immutable. Independent-compiler example roots stay outside the list.
Paths reject escapes, globs, duplicates and non-Roc files;
local source reads reject symlinks and paths outside the checkout.

Prepare preserves bytes outside each selected pin literal. Before replacing an
existing candidate, and independently before optional merging, the controller
reads immutable parent/candidate blobs via the API and compares the full contents
against the exact expected literal replacements. The complete changed-file set
must match those roots, with every file modified in place. Extra files or a body
change cannot pass as a compiler update. Merge uses trusted event configuration
and does not check out or execute candidate source. Tests cover body mutations,
extra files, selected-root limits and preservation of unselected examples.

The read-only release guard accepts `compiler-root` to read the source header;
empty retains legacy `.roc-version`. An optional paired `simulated-stable-pin`
and `simulated-stable-line` permits a clearly labeled simulation only on the exact
`roc-<line>.x` branch and only for that exact nightly. It still verifies the
published nightly asset release and emits `compiler-channel: simulated-stable`.
It never reports stable upstream availability. Remove the mapping after rehearsal.
