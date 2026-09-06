# Roc automation

Shared, reviewable maintenance automation for Roc packages and platforms.
The nightly updater creates a GitHub-signed compiler-pin commit, validates that
exact commit using the project's existing workflows, and reports results on a PR.

The implementation and tests live here. Consumer repositories keep their daily
schedule, `.roc-version`, `.github/roc-nightly.json`, and actual test/release workflows.
The reusable workflow keeps prepare, validate, and report in separate jobs with
separate token permissions. No PAT is required.

## Use

See [integration and permissions](docs/integration.md) for the complete caller
workflow and rollout checks. Always reference a full commit SHA. Upgrade that
reference through a PR; shared code changes do not silently change consumers.

The repository configuration is:

```json
{"workflows": ["ci.yml", "release.yml"]}
```

Every listed workflow must accept the boolean `workflow_dispatch` input
`nightly_validation`. When true, it must run the relevant tests and exclude
publication and deployment. A successful workflow with incomplete tests is not
useful evidence; each project owns its validation contract.

## Development

Python 3.10+ and the standard library are sufficient for the controller tests:

```sh
python3 -m unittest discover -s tests -v
```

The controller runtime also requires `git` and the GitHub CLI. The reusable
workflows use Ubuntu runners that include these tools. GitHub CI tests Python
behavior and performs CodeQL analysis of Python and Actions workflows.

The action's `phase` input accepts `prepare`, `validate`, `report`, or `check`.
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
