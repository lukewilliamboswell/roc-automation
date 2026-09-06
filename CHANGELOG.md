# Release notes

## Header pins and compiler-lane rehearsal

- Select development compiler roots by source path and derive their versions from
  literal header fields, retaining legacy `.roc-version` for unmigrated consumers.
- Verify full immutable base/head blobs contain only the expected pin replacements
  before replacing or merging header update candidates. Public example roots stay
  independent.
- Read release compiler metadata from a header and support an explicit exact-pin,
  exact-branch simulated-stable rehearsal, labeled separately from real stable.

## Maintenance release policy

- Document development on `main`, explicit `roc-<major>.<minor>.x` compiler compatibility,
  reviewed backports, compiler support, and separate LTS commitments.
- Add a read-only `actions/check-release` guard for independent package versions, compiler-pin/branch and
  exact checkout correspondence; publication and artifact validation stay consumer-owned.
- Require an exact final stable compiler for publication on every branch, and
  verify its exact official upstream tag is published with assets. A separate
  default-off, main-only nightly bootstrap opt-in permits transitional releases
  before a usable versioned compiler exists; verify its official nightly release
  and remove the opt-in when adopting the documented stable-only policy.
- Keep the existing nightly updater restricted to the default branch.

## Consumer validation guidance

- Document separate published-example, working-tree, and release-archive checks.
- Record localhost testing, signed URL-only release follow-ups, and keeping
  generated docs in deployment artifacts as consumer integration requirements.
- Link the roc-ansi trial evidence and distinguish controller acceptance from
  published-release compatibility and a live release-follow-up test.

Documentation only; no controller, permission, or consumer pin changes are needed.

## Required checks for bot PRs

- Mirror successful dispatched validation jobs as commit statuses on the exact
  candidate, so GitHub can enforce required checks on Actions-bot PRs.
- Publish pending before validation and refuse missing/skipped/failed job evidence.
- Independently recheck those jobs in the merge phase, without status-write access.
- Show structured GitHub API rejection reasons for actionable failure reporting.

Consumers updating to this revision must add `statuses: write` to their caller's
permission ceiling. Only the validation controller receives that permission.

## Opt-in nightly merging

- Add a default-off `auto_merge` policy for compiler-pin PRs only.
- Recheck bot identity, verified commit shape, upstream release, exact workflow
  run evidence, current base/head, and strict repository rules before merging.
- Keep merge authority in a separate job; use the existing short-lived token
  without approval, bypass, publishing, or deployment privileges.
- Reject stale/failed/missing evidence and leave rejected PRs for diagnosis.

Existing consumers remain review-only until they update their shared SHA and
explicitly enable the policy. See the integration guide for ruleset requirements
and the one-repository rollout procedure.

## Initial shared implementation

- Extract the Roc nightly controller and tests from the consuming repositories.
- Preserve GitHub-signed pin commits, exact-lease branch updates, explicit workflow
  dispatch, exact candidate/run verification, and truthful PR result reporting.
- Provide separate prepare, validate, and report jobs with minimal permissions.
- Add a read-only consumer configuration check and reject missing, duplicate,
  malformed, or escaped workflow paths.
- Keep consumer schedules daily around 13:00 UTC, about four hours after the
  upstream 09:00 UTC nightly schedule. Late publication can wait until tomorrow.

Migration removes copied controller scripts/tests from consumers. Their actual
validation workflows and compiler pins remain project-owned.
