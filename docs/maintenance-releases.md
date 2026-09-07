# Development and compiler compatibility releases

Start with the [package maintainer walkthrough](package-maintainer-guide.md) for
the adoption sequence. This page defines branch and release-policy details.

Keep `main` (the default branch) for development with the pinned Roc nightly.
Use `roc-0.1.x` for package development compatible with the upstream Roc
compiler's `0.1.x` line. These are **compiler compatibility branches**, not
package-version branches. A branch is not an LTS promise: document support scope,
end dates, and any paid support arrangements separately.

The intended long-term policy is to publish packages supporting an actually
released stable Roc compiler, even when development has moved ahead. Until a
usable versioned compiler exists, consumers can explicitly enable transitional
publication with an exact nightly on `main`. This is not stable compatibility. Package versions form an independent repository-wide namespace. A package change
on either branch receives a new immutable package release when published. For
example, package `2.3.1` could support Roc `0.1.2`, while package `3.0.0` requires a
newer compiler. Never republish changed contents under an old package version or
infer compiler support from the package's version number.

## Compiler policy

Each app/package/platform root keeps its compiler pin in its header `roc` entry.
Development package roots may use a newer compiler than public example roots.
The updater selects development roots by path and never uses a central version
registry to force these lanes to agree. Under the intended stable-only policy, a release candidate from `main` must
use and pass validation with an actually released stable compiler. If development
has moved beyond that compiler, prepare the release on its compatible branch.
The explicit bootstrap exception below supports today's exact-nightly releases. The existing nightly updater runs
only on the default branch and proposes pin-only PRs there. It does not manage
compiler compatibility branches. A reviewed update from Roc `0.1.2` to `0.1.3`
can remain on `roc-0.1.x`, with the complete relevant compatibility tests.
Moving to another compiler minor line requires a separate compatibility branch
and reviewed source adaptation. There is no automatic versioned-compiler updater
in this implementation; patch updates use ordinary reviewed PRs for now.

Do not create a compiler compatibility branch with a nightly pin and claim that
it supports a stable compiler release. Until a suitable versioned Roc compiler
exists, use `main` with a documented exact nightly. Publication requires explicit
bootstrap opt-in; remove that opt-in when a usable stable compiler is available.
Policy tests can use
synthetic final-version fixtures without claiming those upstream releases exist.

A supported versioned compiler and an exploratory nightly may test the same
source in separate CI jobs. Requiring a new compiler is a reviewed compatibility
change, not an automatic consequence of a green nightly check. Each package
release must document its supported compiler line and exact compiler used for
validation; its starter kit records that exact pin and immutable package URLs.

Keep public repository examples and starter downloads pinned to their published
dependencies and documented compiler. Use temporary copies rebound to current
source for development checks, and test proposed archives before publishing.
Consumers may instead keep separate development examples and published-release
fixtures if both checks remain explicit. If a consumer additionally
promises that the latest compiler works with a previous package release, keep
that cross-version check explicit and required. Do not silently change an existing
compatibility promise when adopting this layout.

## Release and backport workflow

1. Select a reviewed source commit compatible with the intended compiler. Create
   `roc-0.1.x` only when that upstream compiler line is available, set an
   exact final compiler pin in the line, and verify the source. Apply required
   checks and review rules to compatibility branches as well as the default branch.
2. Dispatch release preparation on the selected branch with a new independent
   package version. Capture the event SHA, check out that exact SHA, verify branch
   and compiler correspondence, then test source and the actual bundles intended
   for upload. Package `2.3.1` on `roc-0.1.x` is valid if the compiler pin
   belongs to Roc `0.1.x`; matching package numbers are neither needed nor sufficient.
3. Publish the tested archives with a new immutable package tag pointing at the
   tested SHA. Consume artifacts from that run instead of rebuilding or resolving
   a moving branch in the publishing job. Reject an existing tag or release rather
   than moving or replacing it. A rerun needs an explicitly reviewed recovery path.
4. Validate the actual published downloads from a fresh cache. Publish versioned
   documentation recording compiler compatibility. Show users how to choose a
   package release for their compiler; a global newest package version is not
   necessarily compatible with every supported compiler line.
5. Make source-controlled release follow-ups reviewed PRs to their owning branch.
   Update public landing links or the compatibility table on the default branch
   in a separate narrow PR if needed. Do not merge compatibility pins or old
   generated source wholesale into development.
6. Fix and test bugs on `main` where practical, then cherry-pick focused fixes into
   PRs targeting each affected compiler branch. Test the resulting commit with
   that branch's compiler and full relevant release gates. Publish each changed
   package as a new version. Forward-port urgent compatibility-branch-first fixes
   to `main` as separate reviewed PRs.

The merge commit's identity need not equal a development commit. Tag and release
only the final tested source commit. Subsequent documentation-only commits may
differ from it; verify package contents before claiming that a branch corresponds
to a published artifact. Support and LTS commitments remain separate from branch
names, compiler pins, and the existence of historical package releases.

## Reusable release guard

`actions/check-release` is a read-only policy check, independent of the nightly
controller. Pin it to a reviewed full SHA and run it after checkout in an explicit
`workflow_dispatch` release job:

```yaml
- uses: lukewilliamboswell/roc-automation/actions/check-release@REVIEWED_FULL_SHA
  id: release-policy
  with:
    version: ${{ inputs.release_version }}
    compiler-root: package/main.roc
    allow-default-branch: 'true'
```

The version input is the **package version**: unprefixed SemVer, optionally a
prerelease, without build metadata. On `roc-<major>.<minor>.x`, the action
reads the `compiler-root` header from the exact checkout and requires a matching final compiler
version. Header pins follow Roc spelling, for example `0.1.2` without a `v` prefix.
The legacy `.roc-version` adapter additionally accepts `v0.1.2`; this is
an input-format policy, not evidence that a particular upstream release exists.
Floating names, nightly pins and prerelease compiler versions cannot establish
stable-line compatibility. The action queries the official `roc-lang/roc` release with exactly that tag
spelling and requires a published, non-draft, non-prerelease release with assets.
Missing releases and API failures fail closed. Consumers must separately verify
the downloaded compiler version and test its actual supported platform artifacts.

`allow-default-branch: 'true'` explicitly permits package releases from `main`,
with a final stable compiler release, just like compatibility branches. To enable
the temporary nightly exception, also set `allow-nightly-bootstrap: 'true'` (its
default is false). Both opt-ins are required, and the exception applies only to
the actual default branch with an exact `nightly-YYYY-MM-DD-<commit>` pin. The
action verifies that exact tag in `roc-lang/nightlies` is published with assets;
nightly pins are never accepted on compiler compatibility branches. Remove the
bootstrap opt-in through a reviewed PR when a usable stable compiler becomes
available. The action does not infer readiness from an unrelated existing tag.

Omit default-branch opt-in
to restrict the workflow to compiler compatibility branches. Tags, PR refs,
mismatched compiler lines and mismatched checkouts are rejected. Outputs are
`version`, `sha`, `compiler-pin`, `compiler-release`, `compiler-channel` (`stable`
or `nightly-bootstrap`), and `maintenance-branch` (empty on the default
branch). Use `sha` throughout build/publish jobs. The action uses the short-lived GitHub token for read-only official release
metadata; it requires `gh` and no write permission.

This check does **not** verify tests, compiler binary integrity/provenance, tag
uniqueness, artifact digests, reviewer approval, or repository protections.
Consumers enforce those separately. Keep release authority out of PR and
validation-only jobs. Existing `nightly_validation: true` paths remain
non-publishing, including follow-up creation and documentation deployment.

GitHub-token-created PRs may need explicitly dispatched validation. Require
results on their latest commit; never assume a push triggered checks. Keep bot
writes isolated, give no branch-rule bypass or self-approval, and review workflow
changes independently. These practices support the OpenSSF goals described in
[the rollout checklist](openssf.md); they are not a badge-compliance claim.

## Pilot and rollout

Use roc-time's core/tzdb pair and starters to rehearse artifact identity and
compiler compatibility metadata; use roc-ansi to confirm the same contracts apply
to a simpler package. Until a versioned compiler is available, validate rejection
and matching-line behavior with synthetic fixtures. Consumers may opt into the
main-only nightly bootstrap described above, but the roc-time pilot instead
restricts publication to its explicitly mapped simulated support branch. Report
the actual nightly requirement honestly; fixture success does not establish a
live stable-compiler pilot.

For each consumer, record compiler policy, branch rules, validation-only paths,
support commitments, and source/published/archive test commands. Once available,
exercise a compiler-line mismatch, failed validation, a successful candidate and
a reviewed backport against the real compiler. Roll out full-SHA automation changes
through PRs. No automatic promotion, compiler-patch tracker, or backport merging
is included in this implementation.

## Explicit two-compiler rehearsal

A consumer may rehearse before final upstream versions exist. For the current
pilot, `nightly-2026-09-05-b195f5b` represents pretend Roc `0.1.x` on `roc-0.1.x`,
while development roots use `nightly-2026-09-06-d85e877`. Published examples keep
the pretend-stable pin and released dependency URLs; development examples or
source test copies are separate validation inputs.

Configure the roc-time release guard explicitly:

```yaml
compiler-root: package/main.roc
allow-default-branch: 'false'
allow-nightly-bootstrap: 'false'
simulated-stable-pin: nightly-2026-09-05-b195f5b
simulated-stable-line: '0.1'
```

These are action inputs for this rehearsal. Main uses the September 6 compiler
and cannot publish; only `roc-0.1.x` with the exact September 5 pin can publish.
Public examples also retain September 5, independently of development. This
models the future support-branch release policy without claiming that the
September 5 nightly is an upstream stable release. Shared main-only bootstrap
remains available to other consumers, but is disabled in this pilot.
Release notes and metadata must label `compiler-channel: simulated-stable`, name
the actual nightly, and link to its real upstream release. Package version tags
remain independent, immutable, and globally unique across development/compatibility
branches. Remove the mapping when ending the pilot.

Exercise package changes, a compiler update on main, and a reviewed backport with
new package releases. For each, validate exact artifacts, then published starter
execution and versioned documentation. Update the public compatibility mapping
and example URLs through reviewed follow-ups without replacing their compiler
pins with the development pin. A new release follows every published source
change; never edit existing release archives to update examples retroactively.

For generated package roots, keep the mutable compiler header pin separate from
checksums of generated data. A compiler update should not make unchanged zone or
calendar data appear regenerated. Validate the version-neutral generated payload
and validate the header pin independently; preserve provenance and integrity
checks on the actual generated data.
