# Consumer validation and release lifecycle

A successful nightly update must establish compatibility with what users can
download, as well as with the consumer's working-tree changes. Testing only a
fresh local bundle can hide a broken published package or platform.

This is the existing nightly published-compatibility contract. Consumers adopting
[compiler compatibility branches](maintenance-releases.md) may instead keep source
examples on `main` and explicit release fixtures with their supported compiler.
Make that policy change reviewed and visible: testing a release fixture with its
old compiler does not establish compatibility with a new nightly.

## Validation contract

Use separate checks for these questions, all running with the candidate compiler:

| Check | Dependency used by examples | What a failure means |
| --- | --- | --- |
| Published examples | Committed, immutable release URLs, without rewriting | Users of the published package or platform may be broken by the compiler update |
| Working-tree package/platform | A fresh local bundle served over localhost to temporary example copies | Current source changes are incompatible or incorrect |
| Proposed release archive | The exact archive intended for upload, served over localhost | The release artifact is incomplete or unusable even if source tests pass |

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
If published examples fail while the local bundle passes, stop the nightly update
and diagnose the difference. A source fix may require a new package/platform
release and an example-URL update before accepting that compiler. Do not treat
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

When moving existing documentation out of Git, preserve published version URLs:
archive the existing output as release assets, verify restored files match, and
validate full site assembly before removing the tracked copies. Keep generation
and archive restoration in the consumer. The roc-ansi migration preserved five
versions this way; CI checks that generated docs stay untracked and that the site
can be assembled from the assets.

The roc-time pilot currently retains its historical `www/` output in Git and
includes generated docs in release follow-up PRs. That preserves its existing
versioned documentation while the compiler and package release flow is exercised;
it does not satisfy the artifact-only storage contract above. Keep that exception
scoped to the pilot. Before removing the tracked history, archive and verify the
published versions and prove the site can be restored from those artifacts.

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

## Evidence from the roc-ansi trial

These are historical observations, not a claim that every consumer is configured
correctly:

- The [first live run](https://github.com/lukewilliamboswell/roc-ansi/actions/runs/34059365699)
  passed dispatched validation but GitHub rejected the merge because required
  checks were not associated with the bot PR. The
  [shared reporting fix](https://github.com/lukewilliamboswell/roc-automation/pull/3)
  publishes pending statuses, then success only for verified successful jobs on
  the exact candidate. The merge job independently rechecks that evidence.
- The [successful trial](https://github.com/lukewilliamboswell/roc-ansi/actions/runs/34059965253)
  merged [PR #43](https://github.com/lukewilliamboswell/roc-ansi/pull/43) through
  active rules with no bypass. A
  [subsequent no-op](https://github.com/lukewilliamboswell/roc-ansi/actions/runs/34060098146)
  passed without another update. Refreshing the branch had closed stale PR #37;
  identify candidates by branch and commit, not a permanently stored PR number.
- Those runs proved the controller and merge protections worked. A later audit
  found that the consumer tested only freshly bundled source, so they did **not**
  prove compatibility with the published release. The changes in
  [roc-ansi PR #26](https://github.com/lukewilliamboswell/roc-ansi/pull/26) separate
  published and local validation, preserve the landing page, keep generated
  documentation out of Git, and sign release follow-ups. That PR remains unmerged;
  a new release using its revised publication path had not been exercised.

The [consumer scripts at the reviewed revision](https://github.com/lukewilliamboswell/roc-ansi/tree/7241d6d97ae1e5f27ef71864a277ac7db44fa91d/scripts)
are a concrete reference, not a universal runner template.

For each additional consumer, record its published URL pins, commands for all
three checks, required check names, signed follow-up behavior, and links to a
successful bot merge, a rejected/failed candidate, and a no-op. Verify the
published check cannot pass merely because local copies were rewritten. Assess
the release follow-up independently of the nightly controller's successful trial.
