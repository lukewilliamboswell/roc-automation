# roc-automation tools 0.7.0

This release lets platform repositories release with `release-package`, and
publishes it for Intel macOS and Windows as well as Linux and Apple Silicon.

`release-package` now treats `platform/main.roc` like a package root. Examples
may depend on the platform with `pf: platform "../../platform/main.roc"`, and
`test-examples` runs them against the working tree, including the host outputs
the repository built. `build VERSION --platform-bundle DIR` takes the one
bundle the repository's own bundle command wrote to `DIR`. It checks that the
bundle holds every tracked `platform/*.roc`, names it, and rewrites the
archived examples to `platform "<release URL>"`. Notes write the platform's URL
as `{platform-url}`. `test-archive` serves that bundle over loopback with an
empty package cache, as for a package. The host build, target layout, linker
inputs and bundle inventory stay with the repository.

`release-package` is published as `release-package-<system>` for x86_64 and
arm64 Linux, Apple Silicon and Intel macOS, and x86_64 Windows, so one
Blueprint `Recipes.file` template fetches it everywhere. `test-examples` and
`test-archive` start nothing but the compiler, and on Windows read the
examples archive with the system's own `tar.exe`. A repository can therefore
test its archive on every system it releases for. `build` and
`check-version` still need Git, `gh` and the archive tools.

Package bundles are now taken from a fresh output directory rather than by
parsing the `Created:` line of `roc bundle`, and an archived `platform` field
is now rewritten correctly. Pure-package releases are otherwise unchanged.

The native utilities keep their existing command-line interfaces. Consumers
select the executable for their system and verify it against the release's
`SHA256SUMS`; Blueprint consumers record that digest in `Blueprint.lock`.
