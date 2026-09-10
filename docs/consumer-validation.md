# Consumer validation and release lifecycle

Use the [package maintainer walkthrough](package-maintainer-guide.md) to choose
and adopt the workflow. This page specifies what each validation proves.

The nightly compatibility policy advances selected public-example and development
compiler pins together. Public examples retain their released dependency URLs:
the candidate must work with those releases and with current source before it can
merge. A failed released-package check may require a fix, a new release, and a
reviewed URL update before retrying the compiler bump.

## Validation contract

Keep published-release compatibility and current-source validation as distinct
lanes with distinct check names. They answer different questions and neither is a
substitute for the other:

| Proposed change | Published released dependencies | Current source/local bundle |
| --- | --- | --- |
| Compiler pin only | Required | Required |
| Package/platform source only | Not required unless the documented release combination also changes | Required |
| Compiler pin and source together | Required | Required |
| Published example URL or header | Required | Required when source is also changed |

A consumer may implement these as separate workflows or clearly separated jobs.
The nightly controller must dispatch and require both lanes. Ordinary pull-request
triggers may use changed paths, but each configured nightly workflow must always
run its real validation when dispatched with `nightly_validation: true`.

| Check | Compiler | Dependency used by examples | What a failure means |
| --- | --- | --- | --- |
| Published examples | The example's declared compiler | Committed, immutable release URLs, without rewriting | The documented user experience is broken |
| Working-tree package/platform | The development candidate compiler | A fresh local bundle served over localhost to temporary example copies | Current source changes are incompatible or incorrect |
| Proposed release archive | The intended release compiler | The exact archive intended for upload, served over localhost | The release artifact is incomplete or unusable even if source tests pass |

Select the public app headers in `compiler_roots` for this policy. The updater
changes their `roc` pins in the candidate commit; validation reads those pins and
does not rewrite them or the released URLs. Keep published, source, and artifact
checks required for automatic merging.

Alternatively, leave independent example compilers outside `compiler_roots`.
Test their declared versions separately from the development candidate. That
policy proves only the documented combinations, not that an older release works
with a newer compiler. An additional cross-version check can establish that
promise; make any temporary compiler-pin adaptation explicit and retain the URLs.

Published-example validation should check, test, run, and build examples where
those operations are supported. Keep example URLs on the latest working release
through a reviewed release follow-up PR. Test those committed URLs; do not silently
replace them with a floating `latest` URL, a newly discovered release, or local
source. An example may depend on both a library and a platform: preserve both
released dependencies in this check.

For local development, scripts should bundle current source, bind a server to
loopback on an available port, copy examples into a temporary directory, and
rewrite only those copies. Clean up the server and copies when execution ends.
A single-example runner should retain terminal input/output when needed.

For platforms, a fresh local bundle can combine current platform source with
unchanged, hash-verified host/engine and external linker inputs. It does not
require rebuilding every native input on every PR. Verify that each selected
artifact's build-input fingerprint still matches its relevant source and build
configuration; changed host inputs require new host outputs. The
[platform build-input contract](platform-build-inputs.md) explains those separate
cycles, local hash verification, optional provenance and final-link feedback.

Require the relevant published, source, and artifact checks in the branch rules.
If a required published-example or cross-version check fails while the local
bundle passes, stop the nightly update and diagnose the difference. A source fix
may require a new package/platform release and an example-URL update before
accepting that compiler. Do not treat
local success as permission to bypass published compatibility.

`.github/roc-nightly.json` selects workflows, not their meaning. The configuration
check verifies the pin and workflow filenames. The controller verifies actual run
and job evidence, but cannot determine whether a consumer quietly rewrote URLs or
omitted an important test. Review that contract before adopting the updater's
default merge behavior; set `auto_merge: false` until the contract is ready.

## Release follow-up contract

The consumer's explicit release workflow should:

1. Validate source and test the exact proposed archive before upload.
2. Publish the versioned release asset.
3. Update example URLs to that asset and validate the published examples again.
4. Generate documentation into ignored build output and publish it as deployment
   or release artifacts. Keep rendered `roc docs` output out of the source
   repository and preserve the custom landing page.
5. Open a signed, reviewable PR updating the checked-in example URLs. Do not add
   generated documentation to that PR.

When moving generated documentation out of Git, preserve published version URLs:
archive the existing output as release assets, verify restored files match, and
validate full site assembly before removing the tracked copies. Keep generation
and archive restoration in the consumer. During migration, retain the tracked
output until those checks pass; this temporary arrangement is not completion of
the artifact-only storage contract. Avoid mixing that migration with compiler or
package changes when it would make failures harder to diagnose.

For consumers with published examples on the default branch, merge the follow-up
there. For compiler-compatible releases, target the owning compiler branch and update
public landing links separately; do not overwrite development source or pins.
The nightly updater does not publish releases or merge these follow-up PRs. Its
automatic merge policy permits only compiler pin changes: literal contents in
the selected `compiler_roots` headers, or the legacy `.roc-version` file when
header roots are not configured. Validation-only runs must exclude publication,
follow-up creation, and deployment.

Check the follow-up creator against the actual repository rules. Git bot identity
configuration does not sign a commit. GitHub's commit API can create a verified
signed commit; check the resulting verification and refuse to overwrite unrelated
branch work. A signed follow-up still needs its required checks and review process.

Keep package-specific test runners and release generation in the consumer for now.
They need to know how to bundle that project and exercise its examples. Share the
contract here; extract more runtime code only when multiple consumers demonstrate
the same requirements.

## Verify rollout

For each consumer, record its published URL pins, commands for all
three checks, required check names, signed follow-up behavior, and links to a
successful bot merge, a rejected/failed candidate, and a no-op. Verify the
published check cannot pass merely because local copies were rewritten. Assess
the release follow-up independently of the nightly controller. Keep run links
and acceptance history in the rollout PR, not in this reusable guide.
