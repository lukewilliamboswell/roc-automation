# Tools 0.9.0

`roc-docs` uses Inter, Merriweather Bold and upright Fira Code from roc-font
0.1.0-rc4 across HTML, PDF and diagrams. All ten static web faces and their
licenses ship with the renderer; PDF code preserves `⌘K`. Generated heading IDs
preserve published links and honor scoped prefix/separator attributes. Listing
includes accept positive single lines and closed ranges with optional dedenting.

SBOM generation accepts hyphenated prereleases, supports released Blueprint
0.11 tools, and handles mixed source bundles, ZIPs and PDFs. The package workflow
rechecks transferred subject and sidecar bindings before attestation, including
publication-only retries. Dry runs use `0.0.1-dryrun`, and native dashboard
acceptance uses the current `plume` inventory.

The manual focuses on current contracts and procedures; archived change logs,
superseded guidance and duplicated implementation reports are removed.

Fixes #62, #66, #70, #82, #97, #98, #109 and #114.
