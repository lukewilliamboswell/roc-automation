# OpenSSF Best Practices rollout

The [passing criteria](https://www.bestpractices.dev/en/criteria/0) cover project
practices as well as tooling. This repository supplies shared automation and
reviewable evidence; using it does not establish another project's badge status.

| Area | Shared foundation | Per-project evidence to review |
| --- | --- | --- |
| Change control | SHA-pinned automation, signed compiler updates, PR evidence | Public history, effective review rules, maintained releases |
| Testing | Controller regression suite, exact candidate validation | Meaningful project tests, documented invocation, tests for new behavior |
| Analysis | Python/Actions CodeQL here | Appropriate analysis of the project's own languages; findings resolved |
| Dependencies | Dependabot PRs for Actions | Dependency inventory, reviewed upgrades, timely vulnerability fixes |
| Reporting | SECURITY.md and contribution process here | Working report channels and actual response history |
| Documentation | Integration guide and release notes here | Project purpose, interface docs, contribution requirements, license |

For each consumer, maintain an evidence inventory. It is an internal review aid;
the public documentation should explain what readers need without discussing a
badge application.

| Field | What to record |
| --- | --- |
| Criterion or practice | A short statement of the behavior being assessed |
| Applicability | Applicable, not applicable with a reason, or unresolved |
| Public evidence | A stable URL to documentation, releases, repository settings, or history |
| Verification | The CI check, manual review, or operational observation that tests the claim |
| Status | Met, incomplete, or needs investigation; never infer this from a template |
| Owner and review date | Who maintains the evidence and when it was last checked |
| Follow-up | A linked issue or pull request for missing work |

At minimum, review this inventory:

| Area | Evidence to locate and verify |
| --- | --- |
| Project entry points | Purpose, supported scope, obtaining a release, feedback, discussion, and contribution links |
| Legal | Project license in a standard location and applicable third-party notices |
| Documentation | Basic use, a working first example, public inputs and outputs, commands, configuration, formats, and APIs |
| Change control | Public version history, reviewable interim changes, unique release versions, immutable tags, and human-readable release notes |
| Reporting | Searchable issue history, enhancement path, private vulnerability channel, and actual response history |
| Build and tests | Clean source build, documented test command, CI execution, and a policy to test significant new behavior |
| Analysis | Appropriate warnings, linters, static analysis, dynamic analysis, and timely treatment of findings |
| Dependencies and delivery | Reviewed updates, dependency inventory, HTTPS delivery, artifact verification, provenance, and credential checks |
| Maintenance | Recent activity, supported versions, vulnerability handling, and periodic review of stale claims |

The [documentation guide](documentation-guide.md) expands the documentation row
into a reusable authoring and review process. Verify the configured reporting
channel works. Review repository permissions and branch rules against actual
successful checks, including signed commits where required. Do not mark criteria
met from a template's existence alone.

The nightly rollout must demonstrate a no-op, a successful candidate, and truthful
failure reporting using the repository's GITHUB_TOKEN. Verify required checks
resolve on the candidate and that validation does not publish anything. Preserve
those run/PR links as evidence.

Use OpenSSF Scorecard separately if desired; its automated checks are not the
Best Practices badge. Revisit practice evidence and response history periodically.


Automatic merging does not itself disqualify the passing Best Practices badge.
It does not count as human review. Gold requires at least 50% of proposed
modifications to be reviewed by another person before release; frequent unreviewed
bot changes must be considered when assessing that criterion. Scorecard is a
separate measurement and can deduct Code-Review points for unreviewed bot changes.
Document the opt-in and its tradeoff honestly; do not manufacture bot approvals.

Sources: [passing](https://www.bestpractices.dev/en/criteria/0),
[gold](https://www.bestpractices.dev/en/criteria/2),
[Scorecard Code-Review](https://github.com/ossf/scorecard/blob/main/docs/checks.md#code-review).
