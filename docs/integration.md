# Integration and rollout

A repository keeps its existing validation workflows and exact `.roc-version`.
Declare workflow filenames in `.github/roc-nightly.json`:

```json
{"workflows": ["ci.yml", "release.yml"]}
```

Each listed workflow must support a boolean dispatch input named
`nightly_validation`. Its true path must run the intended tests without publishing
releases, uploading packages to registries, or deploying sites.

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

The branch `automation/roc-nightly` is reserved for pin-only bot commits. Put manual
compatibility fixes on separate branches. No branch-protection bypass is required.

## Migrate copied controllers

Replace the updater and controller-test workflow with the callers above. Remove
`scripts/nightly_update.py` and its copied tests. Retain project validation workflows,
`.roc-version`, `.github/roc-nightly.json`, and project-specific rollout notes.
For already-merged installations, use a follow-up PR from the latest default branch.

Consumer repository settings are separate from file changes. Record settings that
still need action; do not present a caller PR as a completed live-bot acceptance test.
