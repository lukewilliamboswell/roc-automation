# Contributing

Use issues for ordinary bug reports and design discussion, and pull requests for
changes. Explain the affected behavior, the change, and its validation. Follow
SECURITY.md for vulnerabilities.

Run `python3 -m unittest discover -s tests -v` before submitting. Add regression
tests for fixes and tests for new behavior, especially stale commit/run evidence,
branch ownership, permissions, and malformed input. Use Python's standard library
unless an additional runtime dependency has an explicit justification.

Keep each token permission on the job that needs it. Never execute candidate
code in the controller jobs. Pin remote actions and workflows to full commit
SHAs. Review dependency changes and address actionable static-analysis findings.

After the initial repository bootstrap, propose changes through PRs. CODEOWNERS
identifies the maintainer; enforcement still depends on repository rules.

For a controller release, commit the action and its tests first. Then update the
reusable workflows to that exact action commit. This gives the nested action an
immutable revision without a self-referential commit hash. Run CI and a consumer
configuration check before selecting the workflow commit for consumer PRs.

Document behavior and migration changes in CHANGELOG.md. Use unique version tags
for published releases, record any assigned vulnerability identifiers for fixes,
and never move an existing release tag. Consumers review full-SHA updates through
Dependabot PRs or a maintainer-created PR. No updater approves or merges itself.
