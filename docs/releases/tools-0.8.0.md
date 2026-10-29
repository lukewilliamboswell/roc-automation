# Tools 0.8.0

`roc-docs` now renders Mermaid directly from AsciiDoc blocks and Markdown fences,
including nested blocks. Flowchart, sequence, ER, class and state subsets use
roc-graph-layout 0.3.0-rc3. Websites and exported Markdown reference deterministic
source-addressed SVGs with embedded Inter fonts; PDFs contain vector figures and
searchable labels. Unsupported syntax and resource-limit failures stop publication.

**Breaking migration:** remove `prepared-diagrams.json`, downstream Mermaid
rendering scripts and unused generated diagram assets. Keep the Mermaid source
and run the ordinary build. Registries now reject; bundled diagrams are removed.
The experimental preparation manifest advances to schema 3 with distinct raster
and vector entries. No action inputs or commands are added.

PDF body text uses Inter; headings use Roc Docs Serif Bold, a renamed static
Merriweather instance. Font preparation is reproducible with fontTools 4.61.1 and
is performed before release. The compiler is `nightly-2026-10-28-bad769b` and
roc-pdf is pinned to 0.2.0. Font sources, hashes and notices accompany the assets;
`--licenses` includes their notices.
