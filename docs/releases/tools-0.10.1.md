# Shared automation 0.10.1

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
