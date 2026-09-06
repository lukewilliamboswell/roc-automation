# Development and maintenance releases

Keep `main` (the default branch) for development. Use `release/0.1.x` for fixes
and releases in the package's `0.1` line when development has moved ahead, or cut
it earlier for stabilization. An immutable tag such as `0.1.1` identifies the
exact released source. A maintenance branch is not an LTS promise: document
supported lines, scope, end dates, and any paid support arrangements separately.

## Compiler policy

Each branch keeps an exact `.roc-version`. The existing nightly updater runs
only on the default branch and proposes pin-only PRs there. Do not dispatch it
on maintenance branches or cherry-pick its compiler updates into them by default.
Until a suitable versioned Roc compiler exists, a release can explicitly support
a known-good nightly pin; do not describe that compiler as a stable release.

A supported versioned compiler and an exploratory nightly can test the same
source in separate CI jobs. Requiring a new compiler is a reviewed compatibility
change, not an automatic consequence of a green nightly check. Each released
starter records the compiler used to verify its immutable package URLs.

Keep repository examples useful for developing current source. Preserve distinct
published-release fixtures or starter downloads, tested with their documented
compiler, and test proposed archives before publishing. If a consumer additionally
promises that the latest compiler works with the previous published package, keep
that cross-version check explicit and required; do not silently change an existing
compatibility promise when adopting this layout.

## Release and patch workflow

1. Select a reviewed source commit. Create `release/0.1.x` from it when a separate
   stabilization or maintenance line is needed. Keep required checks and review
   rules on maintenance branches as well as the default branch.
2. Dispatch release preparation on that branch. Capture the event SHA, check out
   that exact SHA, verify the branch/version relationship and compiler, then test
   source and the actual bundles intended for upload. A release of `0.1.2` belongs
   to `release/0.1.x`; a `0.2` release does not.
3. Publish the tested archives with a new immutable tag pointing at the tested
   SHA. Consume build artifacts from that run instead of rebuilding or resolving
   a moving branch in the publishing job. Reject an existing tag or release rather
   than moving or replacing it. A rerun needs an explicitly reviewed recovery path.
4. Validate the actual published downloads from a fresh cache. Publish versioned
   documentation; keep the public stable landing page on the selected stable
   release rather than whichever branch or prerelease ran most recently.
5. Make any source-controlled release follow-up a reviewed PR to the owning
   release line. Update public landing links on the default branch in a separate
   narrow PR if needed. Do not merge a maintenance pin or old generated source
   wholesale into development.
6. For a patch, fix and test the bug on `main` where practical, then cherry-pick
   the focused fix into a PR targeting `release/0.1.x`. Test the resulting commit
   with that line's compiler and full relevant release gates. Resolve conflicts
   deliberately; a development test result does not validate the backport.
   Forward-port urgent maintenance-first fixes to `main` as a separate reviewed PR.

The merge commit's identity need not equal a development commit. Tag and release
only the final tested source commit. Subsequent documentation-only commits may
differ from it; verify that package contents still correspond before making any
claim that a branch represents a published artifact.

## Reusable release guard

`actions/check-release` is a read-only policy check, independent of the nightly
controller. Pin it to a reviewed full SHA and run it after checkout in an explicit
`workflow_dispatch` release job:

```yaml
- uses: lukewilliamboswell/roc-automation/actions/check-release@REVIEWED_FULL_SHA
  id: release-policy
  with:
    version: ${{ inputs.release_version }}
```

The default accepts only `release/<major>.<minor>.x` matching the unprefixed SemVer
version (including prereleases, excluding build metadata). Set
`allow-default-branch: 'true'` deliberately if initial releases from `main` are
part of the consumer policy. Tags, PR refs, and mismatched checkouts are rejected.
Outputs are `version`, `sha`, and `release-line`. Use `sha` throughout subsequent
build/publish jobs. The action reads GitHub's event context and local Git HEAD;
it needs no write permission or API token.

This check does **not** verify tests, compiler provenance, tag uniqueness, artifact
digests, reviewer approval, or repository protections. Consumers must enforce
those separately. Keep release authority out of PR and validation-only jobs.
A dispatched candidate workflow must not gain publishing authority merely because
it knows a version string. Existing `nightly_validation: true` paths remain
non-publishing, including follow-up PR creation and documentation deployment.

GitHub-token-created PRs may need explicitly dispatched validation. Require
results on their latest commit; never assume a push triggered checks. Keep bot
writes isolated, give no branch-rule bypass or self-approval, and review workflow
changes independently. These are practices supporting the OpenSSF goals described
in [the rollout checklist](openssf.md), not a claim of badge compliance.

## Pilot and rollout

Use roc-time's core/tzdb pair and starters to rehearse artifact identity and patch
release preparation; use roc-ansi to confirm the same contracts apply to a simpler
package. For each consumer, record the exact compiler policy, branch rules,
validation-only path, supported lines, and source/published/archive test commands.
Exercise a mismatched version, failed validation, a successful candidate and a
maintenance backport before enabling unattended publication. Roll out reviewed
full-SHA automation updates through PRs. Maintenance does not require a second
nightly updater, automatic branch promotion, or automatic backport merging.
