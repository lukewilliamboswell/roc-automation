# roc-automation tools 0.3.0

This release adopts roc-parser 3.0.0 for the native documentation renderer and
Blueprint 0.7.0 for repository tooling. Adopt shared actions at a reviewed full
commit SHA and verify executable bytes against their reviewed SHA-256.

## Documentation renderer

`roc-docs` uses the immutable roc-parser 3.0.0 bundle and compiler
`nightly-2026-10-21-5349569`. Parser changes are reviewed through native renderer
tests and independent HTML, Markdown and PDF acceptance checks. The supported
checked-listing contract remains a single positive `tag=name` selection and
optional `indent=0`; this upgrade does not expose the parser's entire include
selection API.

The default publication remains an HTML site with mirrored Markdown for each
manual page, `llms.txt` and PDF. `--markdown false`, or `markdown: "false"` in
`actions/build-docs`, opts out of Markdown publication. Native targets remain
Linux x86-64, Linux ARM64 and Apple Silicon macOS. Source-policy, controller
and frozen build-runner compiler pins remain independent of the renderer pin.

## Blueprint provisioning and migration

The shared setup action admits Blueprint 0.7.0 executables on its three supported
installer platforms. Verified executable caching still keys by release, system
and reviewed digest. Blueprint 0.7 embeds its fixed configuration compiler and
checks its archive before extraction into the content-addressed store. First-use
compiler provisioning no longer requires a GitHub release download; declared
project tools and packages may still need network access.

Upgrade the CLI and platform together. Migrate `Overlay` declarations to `Input`,
retain explicit package sources and source-qualified tools, run `blueprint check`
and a full `blueprint update`, review the regenerated Lock 2 against prior pins,
and remove generated `.blueprint/` state. Earlier locks are refused outside the
full update path. Build declarations also need explicit named executors.

Task-only `RocPackages` bundles are pinned by their committed content-addressed
URLs rather than provider-lock entries. Missing bundles are verified through
`roc unbundle`; existing extracted Roc package directories are reused without
rehashing. Sandboxed-build bundles remain provider-locked and use a separate
build package cache. Blueprint artifact-cache verification does not authenticate
pre-existing Roc package cache directories.

Adoption checks use the public `blueprint lock show --json` format 2 interface,
select fetched programs by name and system, and compare actual executable
identities. The copyable pure-package starter uses Blueprint 0.7.0 and the
released tools 0.2.0 renderer, preserving the separate fixed tooling compiler.

Privileged reporting and publication boundaries are unchanged. This release
does not integrate Blueprint's SBOM generation, validation or publication APIs.
