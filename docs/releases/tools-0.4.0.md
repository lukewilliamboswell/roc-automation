# roc-automation tools 0.4.0

This release adopts Blueprint 0.8.0 for repository tooling and adds the shared
SBOM sidecar and supplier-chain actions. Adopt shared actions at a reviewed full
commit SHA and verify executable bytes against their reviewed SHA-256.

## Blueprint 0.8.0

The shared setup action admits Blueprint 0.8.0 executables on its three supported
installer platforms, each by the reviewed SHA-256 of the published binary.
Spec major 3, Lock 2, lock report format 2 and the embedded configuration compiler
`nightly-2026-10-21-5349569` are as in 0.7.0, so a `Blueprint.roc` and
`Blueprint.lock` written for 0.7.0 are read unchanged and no configuration
migrates. The repository's own lock and the copyable pure-package starter's lock
regenerated without a change under 0.8.0.

## SBOM sidecars and supplier chaining

`actions/sbom-sidecars` runs in a read-only build job. It takes an explicit list
of release files, inspects the reviewed composition against the evaluated Spec
and Lock with the Blueprint SBOM tool recorded for the repository's release,
binds SPDX 3.0.1 and CycloneDX 1.7 sidecars, and SPDX 2.3 on request, to each
file's final bytes, checks them offline and returns the complete asset set with
a `roc-automation-sbom-evidence-v1` envelope for the test, attestation and
publication jobs. Under 0.8.0 the tool is the native `blueprint-sbom-<system>`
binary admitted by its reviewed digest, so the job needs no Roc compiler; every
member of a source bundle is accounted for before its inventory is bound, and
each evidence manifest is checked, listed as an asset and recorded in the
envelope.

`actions/fetch-dependency-sboms` is the one network step. It finds each locked
dependency's published SBOM beside its file, records it bound to the Lock's
SHA-256 in `sbom/imports.json` for `inspect` to verify, keeps the target the
supplier's document states so a `source` bundle binds into a native inventory,
and reports a dependency without a published SBOM as a stub the maintainer
completes. A recorded document must be reproduced byte for byte on every later
run. Neither action holds a write token, an OIDC token or an attestation
permission.

`actions/setup-blueprint/sbom-tools.json` records the tool per Blueprint release:
the immutable app bundle URL, its Roc release, the generator name, the reviewed
SHA-256 of each native tool binary and the release's features. `Blueprint.roc`
remains the one place a repository names its release. `examples/sbom-chain/`
is a complete copyable release workflow that builds a bundle and per-system
executables, binds and tests their sidecars, attests every asset and publishes
the explicit asset list through `actions/publish-release`. CI exercises both
actions with the released tools on x86_64 Linux, arm64 Linux and Apple Silicon.

The independent validator and the privileged publisher's re-verification of the
envelope, retention and corrections, and supplier attestation at admission are
separate work; the envelope is a transfer manifest, not an authorization.

## Released utilities

`roc-docs`, `roc-policy`, `nightly-report`, `publish-release` and
`release-compatibility` are rebuilt from unchanged sources with unchanged
compiler pins. The documentation renderer keeps roc-parser 3.0.0 and compiler
`nightly-2026-10-21-5349569`; the published Markdown, `llms.txt` and PDF
defaults are as in tools 0.3.0. Privileged reporting and publication boundaries
are unchanged.
