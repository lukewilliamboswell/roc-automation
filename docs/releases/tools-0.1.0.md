# roc-automation tools 0.1.0

The first release of the programs that roc-automation's shared actions run,
as static executables for GitHub's Linux runners.

- `nightly-report` keeps the one tracking issue of a nightly compatibility
  workflow. It is what `actions/nightly-report` runs.
- `publish-release` stages a draft release, verifies every uploaded asset
  against the tested file, and then publishes. It is what
  `actions/publish-release` runs.

Each is built for `x86_64-linux` and `aarch64-linux`, linked statically against
musl, and needs nothing from the runner but the `gh` and `sha256sum` commands.
`sha256sums.txt` lists the SHA-256 of every executable.

Use these through the actions, which check the digest before running anything.
The manual describes each action's inputs:
https://lukewilliamboswell.github.io/roc-automation/actions.html
