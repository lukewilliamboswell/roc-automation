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
| `actions/release-compatibility` | Check immutable published examples on one nightly, preserving diagnostics |
| `actions/nightly-report` | Keep the one tracking issue of a nightly compatibility workflow |
| `actions/blueprint-tool` | Hand off a persistent Blueprint utility path with optional process diagnostics |
| `actions/build-docs` | Render AsciiDoc or Markdown as an HTML site and PDF using a supplied Roc binary |
| `actions/publish-release` | Stage a draft release, verify every asset against the tested file, then publish |
| `actions/publish-build-inputs` | Publish linker inputs built on a pull request, and commit only their lock |

See [Shared actions](https://lukewilliamboswell.github.io/roc-automation/actions.html)
for how to use them. Pure Roc packages can start with the complete
[Nix-free adoption guide](https://lukewilliamboswell.github.io/roc-automation/pure-package.html).

## Shared native utilities

| Utility | Purpose |
| --- | --- |
| [roc-docs](tools/roc-docs/README.adoc) | Build an HTML site and PDF from documentation sources |
| [roc-build-runner](tools/roc-build-runner/README.adoc) | Execute a versioned build specification with verified isolation and checked inputs and outputs |

The build runner is a standalone Roc program. Consumers pin its source and
compiler inputs and compile a native executable; the runner has no Blueprint
dependency. Blueprint owns project planning and lock validation and supplies
the runner's inputs. The current isolation contract requires Linux namespaces.

## Downstream status

Published-release compatibility and automation revisions for the supported
downstream repositories we maintain. Each badge states the result and next action;
click it for evidence, exact versions and diagnostic links.

| Repository | Published release × latest Roc nightly / action | roc-automation revision / action |
| --- | --- | --- |
| [aoc](https://github.com/lukewilliamboswell/aoc) | [![aoc published-release compatibility and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Faoc-compatibility.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) | [![aoc automation revision and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Faoc.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [basic-ssg](https://github.com/lukewilliamboswell/basic-ssg) | [![basic-ssg published-release compatibility and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Fbasic-ssg-compatibility.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) | [![basic-ssg automation revision and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Fbasic-ssg.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-ansi](https://github.com/lukewilliamboswell/roc-ansi) | [![roc-ansi published-release compatibility and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-ansi-compatibility.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) | [![roc-ansi automation revision and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-ansi.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-blueprint](https://github.com/lukewilliamboswell/roc-blueprint) | [![roc-blueprint published-release compatibility and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-blueprint-compatibility.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) | [![roc-blueprint automation revision and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-blueprint.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-fuzz](https://github.com/lukewilliamboswell/roc-fuzz) | [![roc-fuzz published-release compatibility and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-fuzz-compatibility.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) | [![roc-fuzz automation revision and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-fuzz.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-graph-layout](https://github.com/lukewilliamboswell/roc-graph-layout) | [![roc-graph-layout published-release compatibility and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-graph-layout-compatibility.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) | [![roc-graph-layout automation revision and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-graph-layout.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-pandoc](https://github.com/lukewilliamboswell/roc-pandoc) | [![roc-pandoc published-release compatibility and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-pandoc-compatibility.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) | [![roc-pandoc automation revision and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-pandoc.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-parser](https://github.com/lukewilliamboswell/roc-parser) | [![roc-parser published-release compatibility and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-parser-compatibility.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) | [![roc-parser automation revision and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-parser.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-pdf](https://github.com/lukewilliamboswell/roc-pdf) | [![roc-pdf published-release compatibility and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-pdf-compatibility.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) | [![roc-pdf automation revision and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-pdf.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-platform-template-go](https://github.com/lukewilliamboswell/roc-platform-template-go) | [![roc-platform-template-go published-release compatibility and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-platform-template-go-compatibility.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) | [![roc-platform-template-go automation revision and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-platform-template-go.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-platform-template-rust](https://github.com/lukewilliamboswell/roc-platform-template-rust) | [![roc-platform-template-rust published-release compatibility and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-platform-template-rust-compatibility.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) | [![roc-platform-template-rust automation revision and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-platform-template-rust.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-platform-template-zig](https://github.com/lukewilliamboswell/roc-platform-template-zig) | [![roc-platform-template-zig published-release compatibility and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-platform-template-zig-compatibility.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) | [![roc-platform-template-zig automation revision and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-platform-template-zig.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-ray](https://github.com/lukewilliamboswell/roc-ray) | [![roc-ray published-release compatibility and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-ray-compatibility.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) | [![roc-ray automation revision and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-ray.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-signals](https://github.com/lukewilliamboswell/roc-signals) | [![roc-signals published-release compatibility and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-signals-compatibility.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) | [![roc-signals automation revision and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-signals.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-time](https://github.com/lukewilliamboswell/roc-time) | [![roc-time published-release compatibility and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-time-compatibility.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) | [![roc-time automation revision and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-time.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [roc-wasm4](https://github.com/lukewilliamboswell/roc-wasm4) | [![roc-wasm4 published-release compatibility and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-wasm4-compatibility.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) | [![roc-wasm4 automation revision and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Froc-wasm4.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |
| [weaver](https://github.com/lukewilliamboswell/weaver) | [![weaver published-release compatibility and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Fweaver-compatibility.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) | [![weaver automation revision and action](https://img.shields.io/endpoint?url=https%3A%2F%2Flukewilliamboswell.github.io%2Froc-automation%2Fdownstream-status%2Fweaver.json)](https://lukewilliamboswell.github.io/roc-automation/downstream-status/) |

**Compatibility:** green means every release target passed the shared nightly
runner. Red means published examples failed: inspect diagnostics and prepare a
compatible patch release if needed. “Fix setup / rerun” covers incomplete evidence,
skipped cases and infrastructure failures; a pass older than 48 hours requires a
rerun. “Adopt shared nightly” means this repository does not yet report through
[the shared contract](https://lukewilliamboswell.github.io/roc-automation/nightly-compatibility.html).
Pin-updater runs, development CI and weekly floating-input checks are excluded.
Compiler warnings remain visible and nonfatal; green does not mean warning-free.

**Automation:** the badge lists direct pinned roc-automation SHAs. “Matches main”
requires every reference to equal the observed upstream `main` SHA. “Review
upgrade” means maintainers should review and update their pins; even a
documentation-only upstream change can trigger it. Unpinned references need a full
SHA; missing adoption and unreadable evidence call for setup review or investigation.
Nested references inside pinned shared code are outside this inventory.

Badges refresh through the daily Documentation deployment, with no daily README
commits or PRs. The [status report](https://lukewilliamboswell.github.io/roc-automation/downstream-status/)
records check time, run dates and the comparison SHA. The 48-hour age check runs
when this snapshot refreshes. If its check date is over 48 hours old, investigate
the Documentation deployment before relying on a green badge: static badges
retain their last colour when publication stops. Compiler-pin updates remain a manual downstream
PR task; the shared nightly only tests published releases and reports results.

## Contributing

See [Contribute to roc-automation](https://lukewilliamboswell.github.io/roc-automation/contributing.html)
in the manual. Report vulnerabilities as [SECURITY.md](SECURITY.md) describes.

[Release notes](https://lukewilliamboswell.github.io/roc-automation/releases.html) · [UPL-1.0 license](LICENSE) ·
[Third-party assets](THIRD_PARTY_LICENSES.md)
