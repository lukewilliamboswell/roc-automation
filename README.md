# Roc automation

An opinionated approach for managing Roc packages and platforms, and the shared
actions that implement it.

**[Read the manual](https://lukewilliamboswell.github.io/roc-automation/)** ·
[PDF](https://lukewilliamboswell.github.io/roc-automation/roc-automation.pdf)

The manual covers the tooling environment, repository layout, examples, testing,
the pull-request, release and nightly workflows, platforms and their native
build inputs, and the trust model behind the shared actions.

## Shared actions

| Action | Purpose |
| --- | --- |
| `actions/setup-blueprint` | Install Nix and the verified `blueprint` binary |
| `actions/nightly-report` | Keep the one tracking issue of a nightly compatibility workflow |
| `actions/build-docs` | Render AsciiDoc or Markdown as an HTML site and PDF using a supplied Roc binary |
| `actions/publish-release` | Stage a draft release, verify every asset against the tested file, then publish |
| `actions/publish-build-inputs` | Publish linker inputs built on a pull request, and commit only their lock |

See [Shared actions](https://lukewilliamboswell.github.io/roc-automation/actions.html)
for how to use them.

## Contributing

See [Contribute to roc-automation](https://lukewilliamboswell.github.io/roc-automation/contributing.html)
in the manual. Report vulnerabilities as [SECURITY.md](SECURITY.md) describes.

[Release notes](https://lukewilliamboswell.github.io/roc-automation/releases.html) · [UPL-1.0 license](LICENSE) ·
[Third-party assets](THIRD_PARTY_LICENSES.md)
