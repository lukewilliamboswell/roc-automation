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
| `actions/setup-blueprint` | Install verified Blueprint, with optional Nix and artifact caching |
| `actions/nightly-report` | Keep the one tracking issue of a nightly compatibility workflow |
| `actions/blueprint-tool` | Hand off a persistent Blueprint utility path with optional process diagnostics |
| `actions/build-docs` | Render AsciiDoc or Markdown as an HTML site and PDF using a supplied Roc binary |
| `actions/publish-release` | Stage a draft release, verify every asset against the tested file, then publish |
| `actions/publish-build-inputs` | Publish linker inputs built on a pull request, and commit only their lock |

See [Shared actions](https://lukewilliamboswell.github.io/roc-automation/actions.html)
for how to use them. Pure Roc packages can start with the complete
[Nix-free adoption guide](https://lukewilliamboswell.github.io/roc-automation/pure-package.html).

## Downstream status

Scheduled checks and automation versions for the supported downstream
repositories we maintain.
Click a CI badge for its runs, or a version badge for full SHAs and source links.

| Repository | Scheduled CI | roc-automation version / upgrade |
| --- | --- | --- |
| [aoc](https://github.com/lukewilliamboswell/aoc) | Not configured | [![aoc roc-automation version](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Faoc.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [basic-ssg](https://github.com/lukewilliamboswell/basic-ssg) | [![basic-ssg scheduled checks](https://github.com/lukewilliamboswell/basic-ssg/actions/workflows/update-roc-nightly.yml/badge.svg?branch=main&event=schedule)](https://github.com/lukewilliamboswell/basic-ssg/actions/workflows/update-roc-nightly.yml?query=event%3Aschedule+branch%3Amain) | [![basic-ssg roc-automation version](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Fbasic-ssg.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-ansi](https://github.com/lukewilliamboswell/roc-ansi) | [![roc-ansi scheduled checks](https://github.com/lukewilliamboswell/roc-ansi/actions/workflows/nightly.yml/badge.svg?branch=main&event=schedule)](https://github.com/lukewilliamboswell/roc-ansi/actions/workflows/nightly.yml?query=event%3Aschedule+branch%3Amain) | [![roc-ansi roc-automation version](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-ansi.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-blueprint](https://github.com/lukewilliamboswell/roc-blueprint) | [![roc-blueprint scheduled checks](https://github.com/lukewilliamboswell/roc-blueprint/actions/workflows/floating.yml/badge.svg?branch=main&event=schedule)](https://github.com/lukewilliamboswell/roc-blueprint/actions/workflows/floating.yml?query=event%3Aschedule+branch%3Amain) (weekly) | [![roc-blueprint roc-automation version](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-blueprint.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-fuzz](https://github.com/lukewilliamboswell/roc-fuzz) | [![roc-fuzz scheduled checks](https://github.com/lukewilliamboswell/roc-fuzz/actions/workflows/nightly.yml/badge.svg?branch=trunk&event=schedule)](https://github.com/lukewilliamboswell/roc-fuzz/actions/workflows/nightly.yml?query=event%3Aschedule+branch%3Atrunk) | [![roc-fuzz roc-automation version](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-fuzz.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-graph-layout](https://github.com/lukewilliamboswell/roc-graph-layout) | [![roc-graph-layout scheduled checks](https://github.com/lukewilliamboswell/roc-graph-layout/actions/workflows/update-roc-nightly.yml/badge.svg?branch=main&event=schedule)](https://github.com/lukewilliamboswell/roc-graph-layout/actions/workflows/update-roc-nightly.yml?query=event%3Aschedule+branch%3Amain) | [![roc-graph-layout roc-automation version](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-graph-layout.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-pandoc](https://github.com/lukewilliamboswell/roc-pandoc) | [![roc-pandoc scheduled checks](https://github.com/lukewilliamboswell/roc-pandoc/actions/workflows/update-roc-nightly.yml/badge.svg?branch=main&event=schedule)](https://github.com/lukewilliamboswell/roc-pandoc/actions/workflows/update-roc-nightly.yml?query=event%3Aschedule+branch%3Amain) | [![roc-pandoc roc-automation version](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-pandoc.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-parser](https://github.com/lukewilliamboswell/roc-parser) | [![roc-parser scheduled checks](https://github.com/lukewilliamboswell/roc-parser/actions/workflows/update-roc-nightly.yml/badge.svg?branch=main&event=schedule)](https://github.com/lukewilliamboswell/roc-parser/actions/workflows/update-roc-nightly.yml?query=event%3Aschedule+branch%3Amain) | [![roc-parser roc-automation version](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-parser.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-pdf](https://github.com/lukewilliamboswell/roc-pdf) | [![roc-pdf scheduled checks](https://github.com/lukewilliamboswell/roc-pdf/actions/workflows/update-roc-nightly.yml/badge.svg?branch=main&event=schedule)](https://github.com/lukewilliamboswell/roc-pdf/actions/workflows/update-roc-nightly.yml?query=event%3Aschedule+branch%3Amain) | [![roc-pdf roc-automation version](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-pdf.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-platform-template-go](https://github.com/lukewilliamboswell/roc-platform-template-go) | [![roc-platform-template-go scheduled checks](https://github.com/lukewilliamboswell/roc-platform-template-go/actions/workflows/update-roc-nightly.yml/badge.svg?branch=main&event=schedule)](https://github.com/lukewilliamboswell/roc-platform-template-go/actions/workflows/update-roc-nightly.yml?query=event%3Aschedule+branch%3Amain) | [![roc-platform-template-go roc-automation version](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-platform-template-go.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-platform-template-rust](https://github.com/lukewilliamboswell/roc-platform-template-rust) | [![roc-platform-template-rust scheduled checks](https://github.com/lukewilliamboswell/roc-platform-template-rust/actions/workflows/update-roc-nightly.yml/badge.svg?branch=main&event=schedule)](https://github.com/lukewilliamboswell/roc-platform-template-rust/actions/workflows/update-roc-nightly.yml?query=event%3Aschedule+branch%3Amain) | [![roc-platform-template-rust roc-automation version](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-platform-template-rust.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-platform-template-zig](https://github.com/lukewilliamboswell/roc-platform-template-zig) | [![roc-platform-template-zig scheduled checks](https://github.com/lukewilliamboswell/roc-platform-template-zig/actions/workflows/update-roc-nightly.yml/badge.svg?branch=main&event=schedule)](https://github.com/lukewilliamboswell/roc-platform-template-zig/actions/workflows/update-roc-nightly.yml?query=event%3Aschedule+branch%3Amain) | [![roc-platform-template-zig roc-automation version](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-platform-template-zig.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-ray](https://github.com/lukewilliamboswell/roc-ray) | [![roc-ray scheduled checks](https://github.com/lukewilliamboswell/roc-ray/actions/workflows/update-roc-nightly.yml/badge.svg?branch=main&event=schedule)](https://github.com/lukewilliamboswell/roc-ray/actions/workflows/update-roc-nightly.yml?query=event%3Aschedule+branch%3Amain) | [![roc-ray roc-automation version](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-ray.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-signals](https://github.com/lukewilliamboswell/roc-signals) | [![roc-signals scheduled checks](https://github.com/lukewilliamboswell/roc-signals/actions/workflows/update-roc-nightly.yml/badge.svg?branch=main&event=schedule)](https://github.com/lukewilliamboswell/roc-signals/actions/workflows/update-roc-nightly.yml?query=event%3Aschedule+branch%3Amain) | [![roc-signals roc-automation version](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-signals.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-time](https://github.com/lukewilliamboswell/roc-time) | [![roc-time scheduled checks](https://github.com/lukewilliamboswell/roc-time/actions/workflows/nightly.yml/badge.svg?branch=main&event=schedule)](https://github.com/lukewilliamboswell/roc-time/actions/workflows/nightly.yml?query=event%3Aschedule+branch%3Amain) | [![roc-time roc-automation version](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-time.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-wasm4](https://github.com/lukewilliamboswell/roc-wasm4) | [![roc-wasm4 scheduled checks](https://github.com/lukewilliamboswell/roc-wasm4/actions/workflows/update-roc-nightly.yml/badge.svg?branch=main&event=schedule)](https://github.com/lukewilliamboswell/roc-wasm4/actions/workflows/update-roc-nightly.yml?query=event%3Aschedule+branch%3Amain) | [![roc-wasm4 roc-automation version](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-wasm4.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [weaver](https://github.com/lukewilliamboswell/weaver) | [![weaver scheduled checks](https://github.com/lukewilliamboswell/weaver/actions/workflows/ci.yml/badge.svg?branch=main&event=schedule)](https://github.com/lukewilliamboswell/weaver/actions/workflows/ci.yml?query=event%3Aschedule+branch%3Amain) | [![weaver roc-automation version](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Fweaver.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |

CI badges show scheduled workflow outcomes, not proof of successful validation:
an updater can finish successfully after skipping an unchanged candidate or
reporting an incompatible nightly. roc-blueprint checks floating inputs weekly;
weaver runs its CI daily.

Version badges refresh with the daily Documentation deployment and show their
check date. Versions are the **direct action and reusable-workflow commit SHAs**
in each consumer's default-branch workflow and composite-action files. “Latest”
means every reference equals the observed roc-automation `main` commit; older or
mixed pins show “upgrade available”. This compares exact commits, so even a
documentation-only upstream change can flag an upgrade. Unpinned references and
unreadable evidence are labeled explicitly. Nested dependencies inside the pinned
roc-automation code are outside this inventory. The
[version report](https://lukewilliamboswell.github.io/roc-automation/downstream-status/)
records the target SHA and observation time. If deployment fails, its timestamp
stays unchanged. The refresh publishes badge data to Pages without committing to
this repository or changing downstream consumers.

## Contributing

See [Contribute to roc-automation](https://lukewilliamboswell.github.io/roc-automation/contributing.html)
in the manual. Report vulnerabilities as [SECURITY.md](SECURITY.md) describes.

[Release notes](https://lukewilliamboswell.github.io/roc-automation/releases.html) · [UPL-1.0 license](LICENSE) ·
[Third-party assets](THIRD_PARTY_LICENSES.md)
