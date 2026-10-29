# Third-party documentation assets

The native `roc-docs` renderer embeds Space Grotesk and Plus Jakarta Sans
for its website theme. Their font files, complete SIL Open Font License 1.1
notices and pinned provenance are in `tools/roc-docs/fonts/web/`.

The native `roc-docs` renderer embeds Inter, Roc Docs Serif (a renamed Merriweather instance) and Source Code Pro under the
SIL Open Font License 1.1. The font files, complete notices and pinned provenance
are in `tools/roc-docs/fonts/`, alongside the deterministic fontTools 4.61.1
preparation recipe. Inter also supplies the embedded fonts in generated diagram
SVGs, with the complete Inter notice retained in SVG metadata; the full Regular face matches roc-graph-layout's measurement data. Notices are also embedded in `roc-docs --licenses`
and included in the tools release. Its renderer was adapted from the UPL-1.0
roc-ansi documentation spike; attribution is retained beside the source.

`roc-policy` embeds the MIT-licensed typos correction dictionary. Its exact
source version, license, provenance and offline digest verifier are in
`tools/roc-policy/vendor/typos/`. The permission notice is embedded in
`roc-policy --licenses` and included in the tools release.

The native PNG decoder adapts bounded inflation from the Apache-2.0 licensed
roc-deflate implementation. Its full license and adaptation notice are in
`tools/roc-docs/vendor/`, included in `roc-docs --licenses` and the tools release.
