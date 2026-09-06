# Automation changes

Read docs/security-model.md before changing controller behavior or permissions.
Keep candidate code out of privileged controller jobs. All shared action/workflow
references must use full SHAs. Add meaningful regression tests for security and
state-transition changes. Do not weaken validation or claim badge compliance from
configuration files alone. Use signed commits and PRs after initial bootstrap.
