# roc-automation tools 0.2.0

Adds two reusable, precompiled utilities for repositories and CI runners:

- `roc-policy` checks spelling, public documentation, phrasing, import boundaries
  and source/header policy. It reads named rules and repository overrides from
  `Blueprint.roc`, including immutable embedded profiles and digest-pinned local
  profiles. `explain-config` reports effective policy and its origins. Source
  checks require no Roc compiler; the optional test runner requires one.
- `roc-docs` builds AsciiDoc or Markdown websites and PDF manuals with embedded
  fonts and syntax highlighting. AsciiDoc parsing uses roc-parser 2.1.0-rc.3; PNG
  decoding and preparation also run in Roc. The `actions/build-docs`
  action needs no Python, compiler, Docker or runtime network access.
  Prepared Mermaid images are
  checked against their source and image digests. The action uses one shared
  renderer and built-in theme: the Docker backend and the `backend` and
  `theme-directory` inputs have been removed. SHA-pinned consumers retain their
  existing behavior until they adopt the new action revision.

Both utilities are built for `x86_64-linux`, `aarch64-linux` and `aarch64-darwin`.
The Linux executables are static. The release workflow tests the exact files on
native runners without a compiler or source checkout before publication.
`sha256sums.txt` records asset digests; dictionary and font permission notices
ship alongside the binaries and are available through each utility's `--licenses`.

`nightly-report` and `publish-release` retain their Linux targets and existing
runtime command requirements. The policy and documentation utilities are
experimental. The native documentation backend implements a bounded source
subset and built-in theme, rather than every Asciidoctor extension.
