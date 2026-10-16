# roc-automation tools 0.2.0

Adds two reusable, precompiled utilities for repositories and CI runners:

- `roc-policy` checks spelling, public documentation, phrasing, import boundaries
  and source/header policy. It reads named rules and repository overrides from
  `Blueprint.roc`, including immutable embedded profiles and digest-pinned local
  profiles. `explain-config` reports effective policy and its origins. Source
  checks require no Roc compiler; the optional test runner requires one.
- `roc-docs` renders Markdown into HTML and PDF with embedded fonts and Roc
  highlighting. It requires no compiler, Docker or runtime network access.
  `actions/build-docs` prepares supported AsciiDoc and image assets using Python's
  standard library and invokes a supplied binary. Prepared Mermaid images are
  checked against their source and image digests. Custom Asciidoctor themes
  require the explicit legacy backend.

Both utilities are built for `x86_64-linux`, `aarch64-linux` and `aarch64-darwin`.
The Linux executables are static. The release workflow tests the exact files on
native runners without a compiler or source checkout before publication.
`sha256sums.txt` records asset digests; dictionary and font permission notices
ship alongside the binaries and are available through each utility's `--licenses`.

`nightly-report` and `publish-release` retain their Linux targets and existing
runtime command requirements. The policy and documentation utilities are
experimental. The native documentation backend implements a bounded source
subset and built-in theme, rather than every Asciidoctor extension.
