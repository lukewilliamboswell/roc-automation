# Release notes

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
