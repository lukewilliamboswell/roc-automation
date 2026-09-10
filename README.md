# Roc automation

Shared, reviewable maintenance automation for Roc packages and platforms.
The nightly updater creates a GitHub-signed compiler-pin-only commit, validates that
exact commit using the project's existing workflows, and reports results on a PR.

The implementation and tests live here. Consumer repositories keep their daily
schedule, compiler pins in selected Roc root headers, `.github/roc-nightly.json`, and actual test/release workflows.
The reusable workflow keeps prepare, validate, and report in separate jobs with
separate token permissions. A fourth job merges validated pin-only PRs by default;
consumers can explicitly opt out with `"auto_merge": false`. No PAT is required.

## Nightly status

Nightly updaters for my repositories in [Roc Awesome](https://github.com/lukewilliamboswell/roc-awesome).
Click a badge to inspect scheduled and manual runs on the default branch.

| Repository | Nightly updater |
| --- | --- |
| [aoc](https://github.com/lukewilliamboswell/aoc) | Not configured |
| [basic-ssg](https://github.com/lukewilliamboswell/basic-ssg) | [![basic-ssg nightly updater](https://github.com/lukewilliamboswell/basic-ssg/actions/workflows/update-roc-nightly.yml/badge.svg?branch=main)](https://github.com/lukewilliamboswell/basic-ssg/actions/workflows/update-roc-nightly.yml?query=branch%3Amain) |
| [roc-ansi](https://github.com/lukewilliamboswell/roc-ansi) | [![roc-ansi nightly updater](https://github.com/lukewilliamboswell/roc-ansi/actions/workflows/update-roc-nightly.yml/badge.svg?branch=main)](https://github.com/lukewilliamboswell/roc-ansi/actions/workflows/update-roc-nightly.yml?query=branch%3Amain) |
| [roc-blueprint](https://github.com/lukewilliamboswell/roc-blueprint) | [![roc-blueprint nightly updater](https://github.com/lukewilliamboswell/roc-blueprint/actions/workflows/update-roc-nightly.yml/badge.svg?branch=main)](https://github.com/lukewilliamboswell/roc-blueprint/actions/workflows/update-roc-nightly.yml?query=branch%3Amain) |
| [roc-fuzz](https://github.com/lukewilliamboswell/roc-fuzz) | [![roc-fuzz nightly updater](https://github.com/lukewilliamboswell/roc-fuzz/actions/workflows/update-roc-nightly.yml/badge.svg?branch=trunk)](https://github.com/lukewilliamboswell/roc-fuzz/actions/workflows/update-roc-nightly.yml?query=branch%3Atrunk) |
| [roc-graph-layout](https://github.com/lukewilliamboswell/roc-graph-layout) | [![roc-graph-layout nightly updater](https://github.com/lukewilliamboswell/roc-graph-layout/actions/workflows/update-roc-nightly.yml/badge.svg?branch=main)](https://github.com/lukewilliamboswell/roc-graph-layout/actions/workflows/update-roc-nightly.yml?query=branch%3Amain) |
| [roc-pandoc](https://github.com/lukewilliamboswell/roc-pandoc) | [![roc-pandoc nightly updater](https://github.com/lukewilliamboswell/roc-pandoc/actions/workflows/update-roc-nightly.yml/badge.svg?branch=main)](https://github.com/lukewilliamboswell/roc-pandoc/actions/workflows/update-roc-nightly.yml?query=branch%3Amain) |
| [roc-parser](https://github.com/lukewilliamboswell/roc-parser) | [![roc-parser nightly updater](https://github.com/lukewilliamboswell/roc-parser/actions/workflows/update-roc-nightly.yml/badge.svg?branch=main)](https://github.com/lukewilliamboswell/roc-parser/actions/workflows/update-roc-nightly.yml?query=branch%3Amain) |
| [roc-pdf](https://github.com/lukewilliamboswell/roc-pdf) | [![roc-pdf nightly updater](https://github.com/lukewilliamboswell/roc-pdf/actions/workflows/update-roc-nightly.yml/badge.svg?branch=main)](https://github.com/lukewilliamboswell/roc-pdf/actions/workflows/update-roc-nightly.yml?query=branch%3Amain) |
| [roc-platform-template-go](https://github.com/lukewilliamboswell/roc-platform-template-go) | [![roc-platform-template-go nightly updater](https://github.com/lukewilliamboswell/roc-platform-template-go/actions/workflows/update-roc-nightly.yml/badge.svg?branch=main)](https://github.com/lukewilliamboswell/roc-platform-template-go/actions/workflows/update-roc-nightly.yml?query=branch%3Amain) |
| [roc-platform-template-rust](https://github.com/lukewilliamboswell/roc-platform-template-rust) | [![roc-platform-template-rust nightly updater](https://github.com/lukewilliamboswell/roc-platform-template-rust/actions/workflows/update-roc-nightly.yml/badge.svg?branch=main)](https://github.com/lukewilliamboswell/roc-platform-template-rust/actions/workflows/update-roc-nightly.yml?query=branch%3Amain) |
| [roc-platform-template-zig](https://github.com/lukewilliamboswell/roc-platform-template-zig) | [![roc-platform-template-zig nightly updater](https://github.com/lukewilliamboswell/roc-platform-template-zig/actions/workflows/update-roc-nightly.yml/badge.svg?branch=main)](https://github.com/lukewilliamboswell/roc-platform-template-zig/actions/workflows/update-roc-nightly.yml?query=branch%3Amain) |
| [roc-ray](https://github.com/lukewilliamboswell/roc-ray) | [![roc-ray nightly updater](https://github.com/lukewilliamboswell/roc-ray/actions/workflows/update-roc-nightly.yml/badge.svg?branch=main)](https://github.com/lukewilliamboswell/roc-ray/actions/workflows/update-roc-nightly.yml?query=branch%3Amain) |
| [roc-signals](https://github.com/lukewilliamboswell/roc-signals) | [![roc-signals nightly updater](https://github.com/lukewilliamboswell/roc-signals/actions/workflows/update-roc-nightly.yml/badge.svg?branch=main)](https://github.com/lukewilliamboswell/roc-signals/actions/workflows/update-roc-nightly.yml?query=branch%3Amain) |
| [roc-time](https://github.com/lukewilliamboswell/roc-time) | [![roc-time nightly updater](https://github.com/lukewilliamboswell/roc-time/actions/workflows/update-roc-nightly.yml/badge.svg?branch=main)](https://github.com/lukewilliamboswell/roc-time/actions/workflows/update-roc-nightly.yml?query=branch%3Amain) |
| [roc-wasm4](https://github.com/lukewilliamboswell/roc-wasm4) | [![roc-wasm4 nightly updater](https://github.com/lukewilliamboswell/roc-wasm4/actions/workflows/update-roc-nightly.yml/badge.svg?branch=main)](https://github.com/lukewilliamboswell/roc-wasm4/actions/workflows/update-roc-nightly.yml?query=branch%3Amain) |
| [weaver](https://github.com/lukewilliamboswell/weaver) | [![weaver nightly updater](https://github.com/lukewilliamboswell/weaver/actions/workflows/update-roc-nightly.yml/badge.svg?branch=main)](https://github.com/lukewilliamboswell/weaver/actions/workflows/update-roc-nightly.yml?query=branch%3Amain) |

A normally completed CI failure is recorded on the candidate PR without failing the
nightly controller run; the incompatible candidate remains open and cannot merge.
Cancelled, skipped, timed-out, malformed, or unverifiable validation still fails the
controller. A successful run can also skip validation when there is no new update to
attempt, including an existing candidate already attempted. These badges show
automation health, not candidate compatibility or how recently validation ran.

GitHub supplies the badge results; this repository list is maintained manually.

## Start here

Follow the [package maintainer walkthrough](docs/package-maintainer-guide.md) to
set up examples, compiler versions, releases and repository permissions in order.
It explains the choices and the work that remains manual.

## Use

See [integration and permissions](docs/integration.md) for the complete caller
workflow and rollout checks. Always reference a full commit SHA. Upgrade that
reference through a PR; shared code changes do not silently change consumers.

A header-based repository enables automatic merging by default:

```json
{"workflows": ["ci.yml", "release.yml"], "compiler_roots": ["package/main.roc"]}
```

See [automatic merging](docs/integration.md#automatic-merging) for the required
repository rules and the explicit opt-out.

List development package/platform roots and public application headers in
`compiler_roots` to advance their compiler pins while retaining released dependency
URLs. Require both released-example and current-source validation before merging.
Independent example compilers are an explicit alternative: leave their roots out
of the list. These are reviewed source paths, not a second version registry.
Legacy consumers omitting this field retain
`.roc-version`. See the integration guide for the complete configuration contract.

Every listed workflow must accept the boolean `workflow_dispatch` input
`nightly_validation`. When true, it must run the relevant tests and exclude
publication and deployment. A successful workflow with incomplete tests is not
useful evidence; each project owns its validation contract.

Use the [consumer validation and release lifecycle guide](docs/consumer-validation.md)
to distinguish published-release compatibility from local bundle tests. It also
describes requirements for signed, validated release follow-ups.

Use the [maintenance release guide](docs/maintenance-releases.md) for development
on `main`, `roc-0.1.x` upstream compiler compatibility lines, independent package releases, and the read-only
`actions/check-release` guard. Branch existence does not promise LTS.

## Development

Python 3.10+ and the standard library are sufficient for the controller tests:

```sh
python3 -m unittest discover -s tests -v
```

The controller runtime also requires `git` and the GitHub CLI. The reusable
workflows use Ubuntu runners that include these tools. GitHub CI tests Python
behavior and performs CodeQL analysis of Python and Actions workflows.

The action's `phase` input accepts `prepare`, `validate`, `report`, `check`, or `merge`.
`check` validates the local compiler pin and workflow configuration without API
calls or writes. The other phases are wired by the reusable nightly workflow;
see [the trust model](docs/security-model.md) for their environment and outputs.

## Project practices

- [Contributing](CONTRIBUTING.md)
- [Reporting a vulnerability](SECURITY.md)
- [Release notes](CHANGELOG.md)
- [OpenSSF rollout checklist](docs/openssf.md)
- [UPL-1.0 license](LICENSE)

Report ordinary bugs and enhancements through GitHub issues. Proposed changes
use pull requests. Security reports follow SECURITY.md.
