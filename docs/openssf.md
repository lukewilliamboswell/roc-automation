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

For each consumer, record links to its README, license, contribution and security
policy, CI invocation, required checks, and release notes. Verify the configured
reporting channel works. Review repository permissions and branch rules against
actual successful checks, including signed commits where required. Do not mark
criteria met from a template's existence alone.

The nightly rollout must demonstrate a no-op, a successful candidate, and truthful
failure reporting using the repository's GITHUB_TOKEN. Verify required checks
resolve on the candidate and that validation does not publish anything. Preserve
those run/PR links as evidence.

Use OpenSSF Scorecard separately if desired; its automated checks are not the
Best Practices badge. Revisit practice evidence and response history periodically.
