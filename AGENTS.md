# Automation changes

Read docs/security-model.adoc and docs/contributing.adoc before changing controller behavior or permissions.
Keep candidate code out of privileged controller jobs. All shared action/workflow
references must use full SHAs. Add meaningful regression tests for security and
state-transition changes. Do not weaken validation or claim badge compliance from
configuration files alone. Use signed commits and PRs after initial bootstrap.

Keep the manual scoped to shared tooling, workflow boundaries and evidence;
product-specific security design and project governance remain with maintainers.
Explain concrete risks, principles, controls, verification and residual assumptions
in the relevant chapter; keep docs/risks-and-controls.adoc as a linked index, not
a second specification. Update the rationale when behavior or evidence changes.
Use scoped agents for further investigations, with a concrete proposal and a
GitHub issue as outputs. Review findings before changing the normative model;
record implementation gaps in issues rather than adding gap inventories to the manual.
