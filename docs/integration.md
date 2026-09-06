# Integration and rollout

A repository keeps its existing validation workflows and exact `.roc-version`.
Declare workflow filenames in `.github/roc-nightly.json`:

```json
{"workflows": ["ci.yml", "release.yml"]}
```

Each listed workflow must support a boolean dispatch input named
`nightly_validation`. Its true path must run the intended tests without publishing
releases, uploading packages to registries, or deploying sites.

Before selecting workflows, apply the [consumer validation contract](consumer-validation.md).
Require checks of committed published example URLs as well as working-tree source
and the proposed release archive. A local bundle test alone does not establish
that a compiler update works with the release users download.

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
[maintenance branches](maintenance-releases.md). It does not update maintenance
compiler pins. Review cross-version published compatibility separately from
release fixtures tested with their documented compiler.

## Repository settings and acceptance

1. Use read-only default workflow permissions. Enable Actions PR creation in
   Settings → Actions → General. The UI option also mentions approval; this
   controller uses creation only and never approves PRs.
2. Allow the pinned shared workflows and their nested pinned actions in any
   repository action allowlist. Confirm this before retiring the local controller.
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
   [release follow-up contract and trial evidence](consumer-validation.md).

The branch `automation/roc-nightly` is reserved for pin-only bot commits. Put manual
compatibility fixes on separate branches. No branch-protection bypass is required.

## Migrate copied controllers

Replace the updater and controller-test workflow with the callers above. Remove
`scripts/nightly_update.py` and its copied tests. Retain project validation workflows,
`.roc-version`, `.github/roc-nightly.json`, and project-specific rollout notes.
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
current base, single verified bot commit modifying only the pin, published upstream
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
