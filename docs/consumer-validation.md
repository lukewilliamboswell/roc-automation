# Consumer validation and release lifecycle

Use the [package maintainer walkthrough](package-maintainer-guide.md) to choose
and adopt the workflow. This page specifies what each validation proves.

The recommended independent-compiler policy keeps development source and public
examples on their declared compilers. A nightly update validates development with
the candidate compiler while also preserving the documented public experience.
Do not infer cross-version compatibility from those two separate successes.

## Validation contract

| Check | Compiler | Dependency used by examples | What a failure means |
| --- | --- | --- | --- |
| Published examples | The example's declared compiler | Committed, immutable release URLs, without rewriting | The documented user experience is broken |
| Working-tree package/platform | The development candidate compiler | A fresh local bundle served over localhost to temporary example copies | Current source changes are incompatible or incorrect |
| Proposed release archive | The intended release compiler | The exact archive intended for upload, served over localhost | The release artifact is incomplete or unusable even if source tests pass |

Some existing consumers additionally require published packages to work with each
new nightly. For that policy, also run the published examples with the candidate
compiler as an explicit cross-version check. Preserve the released dependency
URLs; any temporary root-pin adaptation must be visible in the check definition.
Keep this check required until a reviewed policy change removes that promise.

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

Require the relevant published, source, and artifact checks in the branch rules.
If a required published-example or cross-version check fails while the local
bundle passes, stop the nightly update and diagnose the difference. A source fix
may require a new package/platform release and an example-URL update before
accepting that compiler. Do not treat
local success as permission to bypass published compatibility.

`.github/roc-nightly.json` selects workflows, not their meaning. The configuration
check verifies the pin and workflow filenames. The controller verifies actual run
and job evidence, but cannot determine whether a consumer quietly rewrote URLs or
omitted an important test. Review that contract before enabling `auto_merge`.

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
