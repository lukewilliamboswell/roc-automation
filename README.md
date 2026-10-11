# Roc automation

One way to maintain Roc packages and platforms, and the shared actions that
implement it.

**[Read the manual](https://lukewilliamboswell.github.io/roc-automation/)** ·
[PDF](https://lukewilliamboswell.github.io/roc-automation/roc-automation.pdf)

## The model

- The package or platform is always checked, tested and bundled with the latest
  Roc nightly. Nothing pins it.
- Repository tooling is Roc scripts run by `roc-stable`, a compiler named in
  `Blueprint.roc`. Blueprint, its lock, that compiler and a platform's host
  bindings change only through a maintainer's pull request.
- Pull-request CI tests the working tree, and checks that a platform's committed
  `roc glue` bindings match the nightly.
- A release is one immutable GitHub release: the bundle, an examples archive
  pinned to that bundle, a documentation archive and the manual, each tested as
  the exact bytes that are published.
- A nightly workflow runs the latest release's examples archive on the latest
  nightly. A failure means the package needs a patch release.

The [manual](https://lukewilliamboswell.github.io/roc-automation/) gives the
reason and the procedure for each of these.

## Shared actions

Reference an action by full commit SHA and upgrade it through a pull request.

| Action | Purpose |
| --- | --- |
| `actions/setup-blueprint` | Install Nix and the verified `blueprint` binary |
| `actions/build-docs` | Render `docs/*.adoc` as an HTML site and PDF manual |
| `actions/publish-build-inputs` | Publish linker inputs built on a pull request, and commit only their lock |

The manual's
[shared actions](https://lukewilliamboswell.github.io/roc-automation/actions.html)
chapter is the reference for inputs and outputs.

## Development

Python 3.10+ and the standard library are sufficient for the action tests:

```sh
python3 -m unittest discover -s tests -v
```

Build the manual locally with Docker:

```sh
python3 actions/build-docs/build_docs.py --workspace . --pdf-filename roc-automation.pdf
```

The sources are in `docs/`. Read the
[trust model](docs/security-model.adoc) before changing an action's behavior or
permissions.

## Project practices

- [Contributing](CONTRIBUTING.md)
- [Reporting a vulnerability](SECURITY.md)
- [Release notes](CHANGELOG.md)
- [Third-party documentation assets](THIRD_PARTY_LICENSES.md)
- [UPL-1.0 license](LICENSE)

Report ordinary bugs and enhancements through GitHub issues. Proposed changes
use pull requests. Security reports follow SECURITY.md.
