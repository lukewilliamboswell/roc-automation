# Shared release tools 0.10.0

The release version gate accepts a requested major greater than the latest final
major before API extraction: every possible API change fits that version bump.
Same-major releases still require a successful compiler comparison. Malformed,
noncanonical version metadata or unsupported major numbers fails closed. Candidate compilation,
examples, exact-artifact tests, SBOM checks and publication boundaries are unchanged.

The reusable package-release workflow accepts `build-timeout-minutes`, defaulting
to 30 and constrained to integers from 1 to 360, so large check suites can retain
all their validation before assembly.

Executable regressions cover major and prerelease proofs, same-major comparison
success and failure, inaccessible/malformed baselines, ambiguous bundles, failed
candidate compilation, first releases and dry runs. Workflow tests execute the
actual timeout guard and verify that publication depends on successful build and
test jobs.
