# roc-automation tools 0.2.0

This release updates the experimental documentation and policy utilities and
adds the shared published-release compatibility runner. It follows
`tools-0.1.0-rc.1`. Adopt shared actions at a reviewed full commit SHA, and verify
released or cached executables against a reviewed SHA-256 before running them.

## Documentation for readers and agents

`roc-docs build` now produces an HTML site, mirrored Markdown for each manual
page, an `llms.txt` discovery index and a PDF by default. Each HTML page links to
its Markdown counterpart. These are ordinary static files, suitable for GitHub
Pages; publication does not depend on HTTP content negotiation. Use
`--markdown false`, or `markdown: "false"` with `actions/build-docs`, to opt out.
Generated compiler API pages are outside the Markdown export contract.

Markdown exports preserve source code, stable heading anchors, tables, image
and diagram descriptions, source-version metadata and links between chapters.
Unsafe or colliding output routes fail the publication. Failed builds retain
the previous output. Independent acceptance checks compare the exports with
the prepared source and HTML, alongside the existing PDF and browser checks.

Rendering improvements include printed PDF contents page references,
chapter-specific headers and opening pages, rich lists and splittable callouts
using roc-pdf rc6. Checked local listing includes support positive tag selection
and optional dedentation. Wrapped PSV cells retain following separators, and
table errors identify their source. `site-url` supplies published destinations
for PDF links to API documentation, downloads and website-only chapters.
Unicode PDF destination names remain consistent with website anchors.

AsciiDoc parsing uses roc-parser 2.1.0-rc.3. PNG preparation and HTML/PDF rendering
run inside the executable with embedded fonts and syntax highlighting. The
`actions/build-docs` action uses one shared renderer and built-in theme; the
Docker backend and the `backend` and `theme-directory` inputs have been removed.
Prepared Mermaid images are checked against their source and image digests.
The supported source subset and theme remain experimental, with no general
Asciidoctor-extension or PDF/UA conformance claim.

## Published-release compatibility

`release-compatibility` selects immutable published examples and one resolved
Roc nightly, runs declared compatibility targets and aggregates their evidence.
The shared workflow supports Linux, macOS and Windows runners. Successful
compiler stdout/stderr remains visible, so warnings are preserved and nonfatal.
Diagnostics, command arguments, expected output and failure evidence support
downstream debugging. Setup failures, skipped targets, missing evidence,
timeouts and output limits cannot establish compatibility or clear an issue.
Compiler-pin changes remain a manual downstream PR task.

The release includes Linux executables and the checksummed Python
standard-library ZIP admission helper, a required runtime companion for archive
admission. The release pipeline tests these files using a standalone
compatibility harness carried in CI artifacts; the harness is not a public
release asset. The shared action supplies
the Windows implementation from its pinned source; this release does not add
Windows executable assets. Process launch failures and signals cannot satisfy
expected normal exit statuses. Archive validation rejects unsafe entries and
applies the same admission rules across supported operating systems.

The README dashboard reports published-release compatibility separately from
roc-automation adoption, with follow-up actions and linked dated evidence.
Its daily Pages publication updates badges without daily README commits or PRs.

## Shared actions and build contracts

`actions/setup-blueprint` caches verified executables by release, system and
reviewed digest, validates cache hits and reuses valid bytes without another
release download. Native platform identity and content verification remain part
of the admission boundary.

The linker-input publisher preserves bounded, inert component-build evidence
from schema-2 producer manifests in release locks, supporting Blueprint 0.7
admission. Existing source, attestation and final-byte checks still apply;
publication does not interpret or execute component recipes.

The source-only `roc-build-runner` utility provides a versioned JSON contract
for recipes inside caller-provided Linux sandboxes. Consumers pin and compile
its source and toolchain. It is not a precompiled asset in this tools release;
namespace setup, read-only mounts and process containment remain caller duties.

## Policy and artifact coverage

`roc-policy` checks spelling, public documentation, phrasing, import boundaries
and source/header policy. Schema 2 supports named rules, embedded or
digest-pinned offline profiles, ordered overrides and `explain-config`
provenance. Source checks need no compiler; the optional test runner needs one.

`roc-docs` and `roc-policy` ship for `x86_64-linux`, `aarch64-linux` and
`aarch64-darwin`. `nightly-report`, `publish-release` and `release-compatibility`
ship for Linux x86-64 and ARM64. Linux executables are static. The release
workflow tests the exact executables on native runners without a source checkout
or compiler before publication. `sha256sums.txt` records asset digests, and font
and dictionary permission notices accompany the relevant utilities.
`nightly-report` and `publish-release` retain their `gh`/`sha256sum` runtime
requirements and isolated privileged-job boundaries.

The documentation utility uses compiler `nightly-2026-10-18-1df370f`, including
the upstream cold-builtin ABI fix for invalid LLVM bitcode (roc-lang/roc#12324).
Policy tooling retains its separately pinned compiler. Shared guidance now
records concrete risks, principles, controls and verification in the relevant
chapters, with separate lightweight PR checks and sustained fuzz/benchmark
campaigns. SBOM guidance describes artifact-bound component evidence; this
release does not add an SBOM generator or validator.
