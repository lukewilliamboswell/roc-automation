# Integration and rollout

New to the workflow? Start with the [package maintainer walkthrough](package-maintainer-guide.md).
This page contains the exact caller configuration and rollout requirements.

A repository keeps its validation workflows and exact compiler versions in the
`roc` field of selected app/package/platform root headers.
Declare workflow filenames in `.github/roc-nightly.json`:

```json
{"workflows": ["ci.yml", "release.yml"]}
```

To test released dependencies with each new nightly, select development roots
and public application headers together, for example:

```json
{"workflows": ["ci.yml", "release.yml"], "compiler_roots": ["package/main.roc", "platform/main.roc", "examples/hello/main.roc"]}
```

`compiler_roots` is a unique list of at most 100 safe relative `.roc` paths; it
contains no versions. Each must be a normal file with one literal header pin, and
selected pins must agree. Selected public examples retain their released package
and platform URLs while their compiler pins advance. If the consumer instead
chooses independent example compilers, leave those roots outside the list.
The updater replaces only selected header string contents, preserving all other
bytes. No formatter runs in privileged jobs. The consumer validates grammar,
compiler availability, and runtime behavior in its read-only candidate workflow.

Consumers omitting `compiler_roots` keep legacy `.roc-version` behavior. Migrate
explicitly and remove duplicate version authority rather than maintaining both.
`actions/nightly/compiler_pins.py` provides `read_pin(path)` and
`replace_pin(source, pin)` for local tooling; vendor a reviewed revision with
provenance if direct action use is not appropriate. Replacement requires an
existing literal header pin and never inserts or formats one.

Each listed workflow must support a boolean dispatch input named
`nightly_validation`. Its true path must run the intended tests without publishing
releases, uploading packages to registries, or deploying sites.

Before selecting workflows, apply the [consumer validation contract](consumer-validation.md).
Require checks of committed published example URLs as well as working-tree source
and the proposed release archive. A local bundle test alone does not establish
that a compiler update works with the release users download.
Prefer separately named published-release and current-source workflows or jobs so
failures identify which compatibility promise broke. Configure the nightly
controller to dispatch both. Compiler-only and combined compiler/source candidates
must pass both; package-only pull requests primarily exercise current source.

Use this caller, replacing `REVIEWED_FULL_SHA` with an actual 40-character commit
SHA containing the reusable workflow:

```yaml
name: Update Roc nightly
on:
  schedule:
    - cron: "13 13 * * *"
  workflow_dispatch:
permissions: {}
concurrency:
  group: roc-nightly-update
  cancel-in-progress: false
jobs:
  update:
    permissions:
      contents: write
      pull-requests: write
      actions: write
      statuses: write
    uses: lukewilliamboswell/roc-automation/.github/workflows/update-roc-nightly.yml@REVIEWED_FULL_SHA
```

Those caller permissions are the maximum available to the called workflow.
[GitHub permits the called workflow to reduce them](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations).
The actual jobs grant only the scopes listed in the security model. No secrets
inheritance or PAT is needed. Keep concurrency in the caller only.

Run a cheap consumer check on PRs and manual dispatches:

```yaml
name: Nightly configuration
on:
  pull_request:
  workflow_dispatch:
permissions:
  contents: read
jobs:
  configuration:
    uses: lukewilliamboswell/roc-automation/.github/workflows/check-config.yml@REVIEWED_FULL_SHA
```

This checks the local configuration and pin using the shared action. It does not
replace the project's tests or repeat the shared controller's test suite.

The daily schedule is around 13:00 UTC, about four hours after the upstream 09:00
UTC build schedule. Stagger consumer minutes. The latest published nightly is used;
a delayed release can wait until the next day. Unchanged candidates with an open
PR are skipped; manual dispatch retries an unchanged candidate.

The updater remains default-branch-only when a consumer adopts
[maintenance branches](maintenance-releases.md). It does not update compiler compatibility
branch pins; versioned compiler patch updates use reviewed PRs. Review cross-version published compatibility separately from
release fixtures tested with their documented compiler.

## Repository settings and acceptance

1. Use read-only default workflow permissions. Enable Actions PR creation in
   Settings → Actions → General. The UI option also mentions approval; this
   controller uses creation only and never approves PRs.
2. Allow the pinned shared workflows and their nested pinned actions in any
   repository action allowlist. Entries for one repository use
   `OWNER/REPOSITORY@TAG-OR-SHA`; prefer the exact reviewed SHA. An entry without
   `@...` does not match an action reference. Confirm this before retiring the
   local controller.
3. Keep protected-branch rules and require the actual project validation checks
   on the candidate commit. Do not require the scheduled updater's default-branch
   job. Use up-to-date branch requirements or a merge queue for integration checks.
4. After merge, run the updater once using GITHUB_TOKEN. Inspect its verified
   signed commit, candidate validation runs, and final PR body. Exercise a no-op
   and confirm failure reporting before relying on unattended results.
5. Add or retain a weekly `github-actions` Dependabot entry. Review shared SHA-pin
   updates like other dependency updates. Never replace full pins with `main`.
6. Verify the consumer's release follow-up updates and tests published URLs,
   keeps generated docs in deployment artifacts, preserves the site layout, and
   creates commits accepted by the branch
   signature rules. Record this separately from the nightly merge trial; see the
   [release follow-up contract](consumer-validation.md).

The branch `automation/roc-nightly` is reserved for pin-only bot commits. Put manual
compatibility fixes on separate branches. No branch-protection bypass is required.

## Live rollout and troubleshooting

Resolve immutable references instead of completing an abbreviated SHA by hand.
Query the reviewed revision and copy the returned 40-character SHA:

```sh
gh api repos/OWNER/roc-automation/commits/REVIEWED_REF --jq .sha
```

Use that exact value in both consumer callers and verify it again after editing.
An invalid reusable-workflow SHA produces a `startup_failure` with zero jobs and
no job logs; it never reaches controller code.

Before the first opted-in dispatch, inspect the live repository settings rather
than relying only on the web form:

```sh
gh api repos/OWNER/REPOSITORY/actions/permissions/selected-actions
gh api repos/OWNER/REPOSITORY/rules/branches/DEFAULT_BRANCH
```

The effective rules must include an active pull-request rule and required status
checks with `strict_required_status_checks_policy: true`. Required check contexts
must name real aggregate jobs produced by the configured validation workflows and
must allow zero human approvals if unattended merging is intended. Give the bot no
bypass. Confirm the saved values through this API before testing the updater.

Keep required aggregate checks stable for every pull request. Do not put an entire
required workflow behind a path filter: an unrelated PR can then wait forever for
a check that GitHub never creates. If expensive work is conditional, always run a
small aggregate job that truthfully reports the lane's result.

Failure shape helps locate the problem:

| Symptom | Likely boundary | Check |
| --- | --- | --- |
| `startup_failure`, zero jobs, no logs | Workflow reference or Actions policy | Full SHA and `OWNER/REPOSITORY@REF` allowlist entries |
| Prepare succeeds; validate fails before dispatch | Repository preflight | Active strict ruleset, pull-request rule, required contexts and integrations |
| A dispatched workflow fails | Consumer compatibility | The linked exact candidate run and its jobs |
| Validation/report succeed; merge fails | Live state or merge policy | Base/head movement, reviews, signatures and current rules |

After configuration merges, manually dispatch the updater; manual dispatch retries
an unchanged candidate, so a new nightly is unnecessary. Record the candidate's
verified signed commit, every dispatched run, the bot-authored merge, and the
effective rules. Then exercise a subsequent no-op and retain a real or controlled
failure as evidence that unsuccessful candidates remain open. A merge performed
with `GITHUB_TOKEN` does not normally trigger `push` workflows; dispatch separately
authorized follow-up automation explicitly.

## Required checks on manually merged bot PRs

A successful dispatch proves the selected workflows ran; it does not by itself
prove that branch protection permits merging. With `auto_merge: false`, this
controller reports verified run links on the PR but does not mirror results into
required commit-status contexts. That mirroring currently belongs to the opt-in
merge path. Do not enable automatic merging merely to obtain status reporting.

Validate a real protected-branch merge before declaring setup complete. A release
follow-up reporter must provide the check names and sources your rules require,
on the commit GitHub evaluates. One aggregate result cannot satisfy unrelated
per-job requirements. If a required name identifies both a check run and a commit
status, GitHub requires both to pass. See [required-check troubleshooting](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks).

Manual approval to start the bot PR's ordinary workflows may still be necessary;
that starts validation and is separate from approving the code. Alternatives
include a distinct aggregate check implemented for every PR path, or a GitHub App
that triggers ordinary PR workflows. These require reviewed integration and live
protected-merge verification; this controller does not install either approach.
Choose that policy explicitly instead of assuming dispatch removes every manual
step. See [GitHub's workflow trigger rules](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow).

## Migrate copied controllers

Replace the updater and controller-test workflow with the callers above. Remove
`scripts/nightly_update.py` and its copied tests. Retain project validation workflows,
compiler header pins (or legacy `.roc-version`), `.github/roc-nightly.json`, and
project-specific rollout notes.
For already-merged installations, use a follow-up PR from the latest default branch.

Consumer repository settings are separate from file changes. Record settings that
still need action; do not present a caller PR as a completed live-bot acceptance test.

## Opt-in automatic merging

Automatic merging defaults to disabled. To opt in on the trusted default branch:

```json
{"workflows": ["ci.yml", "release.yml"], "auto_merge": true}
```

Install an active default-branch ruleset requiring pull requests and strict
(up-to-date) required status checks. Select the real test/build/bundle checks from
successful candidate runs, restricted to the GitHub Actions integration. Retain
signature, deletion, and force-push protections. Give the Actions bot no bypass.
The controller requires rulesets specifically; legacy branch protection alone is
not accepted by its preflight. Existing required human reviews still block merging.
For unattended pin updates, the applicable policy must permit merging without a
human approval; this is a deliberate review-policy decision, not a bot approval.

The separate merge job performs no consumer checkout and reads policy at the
original trusted event SHA through the API. It uses the existing GITHUB_TOKEN, with
contents write and Actions/PR read access. No App registration, PAT, stored key,
repository-wide auto-merge setting, or approval permission is needed. It requests
an immediate squash merge only after live validation; it does not queue a merge
that could later accept an unvalidated replacement commit.

The controller checks the bot PR identity, same-repository reserved branch,
current base, single verified bot commit modifying only selected compiler pin literals, published upstream
release, and fresh API results for every dispatched validation run. The merge API
receives the expected head SHA and enforces repository rules. Strict status checks
close the race if the default branch moves after the controller checks it.

A rejected merge fails the updater and leaves the PR for diagnosis. Retry the
updater manually after resolving the cause; it rebuilds/revalidates against the
current default branch. Set `auto_merge` to false (or remove it) to disable merging;
disable the caller workflow in Actions for an immediate emergency stop. A default
branch change invalidates an in-flight candidate. Do not grant a bypass to force
an update through.

Trial on one repository before adding other opt-ins. Record the successful bot
merge, exact validation runs, enforced rules, and a subsequent no-op. Nightly
validation must never publish a release. A GITHUB_TOKEN merge does not normally
trigger push workflows: explicitly dispatch any separately authorized follow-up
work rather than assuming publication or deployment will run.

This policy is intended for mechanical compiler-pin updates only. Changes to
source code, workflow configuration, and the shared automation require maintainer
review. Passing tests demonstrate covered compatibility, not compiler provenance
or freedom from malicious upstream changes. Pin upgrades still trust Roc's nightly
release channel.


The caller permission ceiling includes `statuses: write`; only the validation
controller job receives it. For opted-in consumers, this job mirrors the configured
validation jobs into the active ruleset's required commit-status contexts. It sets
pending before validation and success only after the real runs/jobs succeed.
Choose required contexts that correspond to actual jobs in the selected dispatch
workflows, with GitHub Actions as their source. Other integrations or checks that
never run in nightly validation are rejected. This avoids manual approval of the
redundant bot-triggered PR workflows while retaining strict required checks.
