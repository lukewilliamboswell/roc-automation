# Header compiler pilot

Objective: deliver the shared header-pin updater and explicit two-compiler release
policy used by roc-time, preserving independently pinned public examples.

Remaining work:

- Unlock configured GPG signing and commit the staged implementation. Do not
  bypass the repository signed-commit rule.
- Update reusable workflows to the resulting immutable controller/action SHA in
  a separate signed commit, then push and update PR #5 around its final scope.
- Validate current commits in remote CI; earlier green runs cover earlier code.
- Have roc-time vendor the final scanner revision with provenance and pin the
  final shared workflow/action SHA.
- Rehearse real support-branch publication, resulting docs/notes/starters/example
  follow-ups, development compiler update isolation, and a reviewed backport.
  Record live acceptance links in the PR. No end-to-end success is claimed yet.

Local evidence currently consists of 56 passing controller/policy/header tests.
The runtime pilot uses main on nightly-2026-09-06-d85e877, and public examples plus
roc-0.1.x on nightly-2026-09-05-b195f5b. Default-branch publication and nightly
bootstrap are disabled; only the explicit simulated mapping permits publication.
This is a rehearsal, not evidence of an available versioned stable compiler.

Remove this plan when the reviewed integration and live acceptance are complete.
