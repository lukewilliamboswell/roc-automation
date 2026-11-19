# roc-automation tools 0.12.0

- Shared package releases can use Kai for builds and CycloneDX SBOM generation. The SBOM actions support Kai alongside Blueprint, including dependency SBOM collection and verification of the transferred release files. The admitted Kai SBOM plugin includes version 0.0.13.
- Shared nightly compatibility jobs use a verified, cached Roc nightly installer. Compatibility setup commands can request Kai, and compatibility suites support the compiler's development specialization mode.
- Blueprint setup fetches the Blueprint 0.12 Core asset from its release download address.

The repository also includes a local downstream compatibility sweep with an offline HTML report. See the downstream regression runner guide in `tools/downstream-regressions/README.adoc` for configuration and usage.
