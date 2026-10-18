# roc-automation tools 0.1.0-rc.1

The first experimental release of the shared repository-policy and documentation
utilities, alongside the programs used by the automation actions. This candidate
starts a controlled migration to one shared AsciiDoc website and manual policy;
it is not a complete Asciidoctor replacement.

## Native utilities

- `roc-docs` builds a multi-page HTML site and searchable, tagged PDF 2.0 from
  supported AsciiDoc or Markdown. The binary embeds its parser, fonts, syntax
  highlighting and prepared diagram assets. Native rendering needs no Roc
  compiler, Docker, Ruby, Python, Node, browser, host fonts or network.
- `roc-policy` reads repository source policy from `Blueprint.roc`: spelling,
  public documentation, prohibited phrasing, import boundaries and compiler
  headers. Schema 2 adds named rules, shared offline profiles, ordered overrides
  and effective-configuration provenance. Source checks do not execute code.

Both utilities ship for Linux x86-64, Linux ARM64 and Apple Silicon macOS as
`roc-docs-<system>` and `roc-policy-<system>`. Linux binaries use musl. Windows
and Intel macOS binaries are not included. `sha256sums.txt` records asset digests;
adopt a reviewed digest and verify downloaded or cached bytes before execution.
The release includes font and dictionary license notices; `roc-docs --licenses`
also prints its embedded notices.

## Experimental documentation contract

Use a top-level `docs/index.adoc` book with top-level chapter files, explicit
cross-references, unique chapter titles and stable ASCII anchors. The shared
utility owns the theme, navigation, validation and source-highlighting policy;
repositories supply their source, metadata, entrypoint and prepared assets.
Documentation options currently use CLI arguments or shared-action inputs;
there is no separate documentation configuration schema in `Blueprint.roc` yet.

HTML provides hierarchical desktop/mobile contents, section numbering, heading
links, captions, description lists, sidebars, bundled fonts and dark mode. PDF
contents has three nested levels, bold chapter entries and wrapped indentation.
Both outputs highlight Roc, YAML, shell, JSON and diff; other languages remain
plain preformatted text. PDF pages remain selectable text, including code.

Supply supported non-interlaced eight-bit RGB/RGBA PNGs. Mermaid must be prepared
before publication and registered with source and asset SHA-256 digests in
`docs/prepared-diagrams.json`. An optional validated SVG companion improves HTML;
PDF uses the PNG. Convert unsupported JPEG, GIF, WebP or general SVG inputs to
supported PNG assets outside the renderer. For animation, supply a static print
image and a link to the separate animation; prebuilding a GIF does not make it
a supported native image input.

This candidate has deliberate limits and known migration gaps:

- Raw HTML, Asciidoctor extensions and YAML themes are unsupported. Ordinary PSV
  tables need header rows and simple cells; wrapped separators, cell spans and
  nested table content are not general Asciidoctor-compatible inputs.
- Includes use the bounded local documentation inventory. Direct `.roc` or
  `.expected` includes, ambiguous included chapter origins and included/tagged
  listings still need adapter work. Preserve checked-example validation when
  preparing an included documentation fragment as a temporary workaround.
- Use stable ASCII IDs and explicit chapter cross-references. Legacy implicit
  underscore anchors and arbitrary API/download routes need migration review.
  PDF links require absolute URLs or destinations within that PDF. Markdown PDF
  rendering uses only its selected entrypoint; it does not aggregate all pages.
- Printed contents page numbers, chapter-specific running headers and PDF table
  column proportions are not integrated. PDF callout/sidebar presentation differs
  from HTML. Ordinary Unicode outside the bundled font coverage can reject.
- Inputs and PDF preparation have resource limits. A failed build leaves the
  previous publication intact. No PDF/UA conformance claim is made.

Generated release snippets, API/version archives and interactive examples remain
explicit preparation or assembly stages. Optional `--api-entrypoint` needs a
caller-supplied Roc compiler. `roc-policy run-tests` likewise needs a supplied
compiler; its accounting does not prove a separate CI suite runs every test.

See the [shared documentation contract](https://lukewilliamboswell.github.io/roc-automation/documentation.html#experimental-native-docs-contract)
and the [renderer reference](https://github.com/lukewilliamboswell/roc-automation/blob/tools-0.1.0-rc.1/tools/roc-docs/README.adoc).

## Adoption and validation

Supply the verified executable to `actions/build-docs` as `renderer-binary`,
or invoke `roc-docs build` directly. Pin shared actions to a reviewed full commit
SHA. The action does not install or verify a binary for the caller. Run rendering
and any preparation/compiler stages in read-only jobs; deploy their artifacts
in a separate job. The explicit `backend: legacy` remains available while a
repository migrates.

The release workflow builds natively and tests the exact artifacts on all three
supported systems without a source checkout or compiler. Upstream publication
checks also use independent PDF extraction/colour/position checks and real
desktop/mobile browser checks. An 18-repository worktree evaluation found
roc-ansi a conditional pilot after its normal generated release inputs; other
manuals expose the limits above. Downstream adoption must verify links, source
text, tables, figures and both rendered outputs before switching publication.

## Automation executables

`nightly-report` maintains a nightly compatibility tracking issue.
`publish-release` stages a draft, verifies uploaded assets and then publishes.
These executables ship for Linux x86-64 and ARM64 and use `gh` and `sha256sum`.
Their shared actions preserve the existing privileged-job boundaries.
