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

Documentation migration improvements include printed PDF contents page references,
chapter-specific headers and opening pages, native rich lists and splittable
callouts using roc-pdf rc6. Checked local listing includes support positive tag
selection and optional dedentation. Wrapped PSV cells retain following separators,
and table errors identify their source. `site-url` supplies published destinations
for PDF links to API documentation, downloads and website-only chapters. Unicode
PDF destination names are encoded consistently without changing website anchors.

The documentation utility uses compiler `nightly-2026-10-18-1df370f`, which
includes the upstream cold-builtin ABI fix for invalid LLVM bitcode
(roc-lang/roc#12324). Policy tooling retains its separately pinned compiler.
