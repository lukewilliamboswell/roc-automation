# Shared automation 0.10.1

The release utility follows Roc's pre-1.0 compatibility boundaries. A higher
minor within `0.x` permits every API change, as a higher major does after 1.0.
The version gate validates all numeric components before accepting that proof,
so an old bundle that no longer compiles does not force a premature 1.0 release.
Releases within the same boundary require API comparison unless a pure package
reproduces the exact bytes of its uniquely selected latest-final bundle. This
proof freshly bundles current tracked files, validates the member inventory,
matches the release filename and compares a bounded download from the exact
repository and final tag. It accepts only a later base version and never trusts
`dist/` or extracted package caches. Changed packages and platforms retain their
API comparison; matching names with different bytes or failed downloads stop
validation. Candidate compilation, examples, exact archive consumers and artifact
checks remain required.
Regression tests exercise invalid metadata, overflowing components, prereleases,
downgrades and failing current candidates as well as accepted breaking boundaries.
Executable package regressions also exercise stale outputs, untracked members,
ambiguous bundle identities, tampered or oversized bytes and download failure.

The shared nightly workflow and compatibility action now compose sibling actions
with GitHub's self-repository references. One consumer workflow SHA fixes the
controller, Blueprint installer and checksum registry, and issue reporter. These
components no longer select independently maintained internal revisions.
External actions remain pinned to full SHAs. This composition requires GitHub.com;
GitHub Enterprise Server does not support self-repository references.

A hosted smoke test uses the immutable roc-wasm4 0.8.0 consumer, actually installs
Blueprint 0.11 through the nested action, and executes all four published games.
Conflicting workspace actions must never run. Source regressions preserve the
read-only test jobs, isolated issue reporter, and same-revision composition.
