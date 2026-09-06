# Release notes

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
