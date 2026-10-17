# Third-party documentation assets

The shared documentation action redistributes the Space Grotesk and Plus
Jakarta Sans font files under `actions/build-docs/theme/fonts/`. Both are
licensed under the SIL Open Font License 1.1; the complete license texts are
stored beside the font files.

The optional legacy backend's container installs Asciidoctor, Asciidoctor PDF,
Asciidoctor Diagram, Rouge, Mermaid CLI, Chromium, Node.js, and their runtime
dependencies. Their upstream distributions retain their respective licenses.

The native `roc-docs` renderer embeds Noto Sans and Source Code Pro under the
SIL Open Font License 1.1. The font files, complete notices and pinned provenance
are in `tools/roc-docs/fonts/`. Notices are also embedded in `roc-docs --licenses`
and included in the tools release. Its renderer was adapted from the UPL-1.0
roc-ansi documentation spike; attribution is retained beside the source.

`roc-policy` embeds the MIT-licensed typos correction dictionary. Its exact
source version, license, provenance and offline digest verifier are in
`tools/roc-policy/vendor/typos/`. The permission notice is embedded in
`roc-policy --licenses` and included in the tools release.

The native PNG decoder adapts bounded inflation from the Apache-2.0 licensed
roc-deflate implementation. Its full license and adaptation notice are in
`tools/roc-docs/vendor/`, included in `roc-docs --licenses` and the tools release.
