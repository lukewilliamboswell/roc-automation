# Tools 0.9.0

These notes describe the next tools release; no release is published by this change.

- Fix #114: exercise `plume` consistently in the native downstream-status fixture,
  keeping scheduled/manual selection and spoofed, stale and incomplete evidence
  checks against the actual dashboard utility.

The tools release and SBOM-chain example now use `0.0.1-dryrun` consistently
for nonpublishing builds (#82). No release or consumer pin changes are required.

SBOM generation accepts hyphenated SemVer prereleases and rejects empty or
leading-zero numeric identifiers and build metadata consistently with the
publisher. Mixed source releases enumerate only `.tar.zst` members; ZIP/PDF
subjects keep composition-only inventories. Reviewed Blueprint 0.11.0 SBOM
tools and the corrected v2 document profile are admitted on all five native
systems, with real released-fixture and legacy supplier-chain acceptance (#66).

The shared package workflow transfers the existing SBOM envelope outside the
published asset set and rechecks source/run/build-attempt identity plus exact
subject, manifest and sidecar bytes before attestation. The bounded Python
controller runs no candidate code or Blueprint tool, supports publication-only
retries, and rejects missing/changed files, duplicate JSON and unsafe aliases.
This verifies final-byte consistency, not inventory completeness or supplier
provenance (#62).

- Fix #109: preserve published native dotted heading IDs in the independent
  reference oracle, check shared expected-ID fixtures, and distinguish generated
  and authored heading failures in inventory diagnostics.
- Fix #97: honor scoped AsciiDoc heading prefix/separator attributes, including
  empty and multi-character separators, document titles and included chapters;
  keep duplicate generated IDs deterministic and explicit IDs exact.
- Fix #70: support checked listing single-line and closed inclusive range
  selections through the pinned parser, with optional dedenting, exact-byte
  provenance and strict selector/path/delimiter rejection before publication.
- Fix #98: documentation now uses roc-font 0.1.0-rc4's bundled static defaults
  across HTML, PDF and diagrams: Inter body text, Merriweather Bold headings and
  upright Fira Code. Inline code and listings preserve the exact `⌘K` label;
  unsupported glyphs still stop publication. HTML includes all ten faces and
  their three family notices. Local font copies, derivatives and preparation
  scripts are removed, and the released executable supplies its license text.
