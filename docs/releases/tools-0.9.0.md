# Tools 0.9.0

These notes describe the next tools release; no release is published by this change.

- Fix #114: exercise `plume` consistently in the native downstream-status fixture,
  keeping scheduled/manual selection and spoofed, stale and incomplete evidence
  checks against the actual dashboard utility.

The tools release and SBOM-chain example now use `0.0.1-dryrun` consistently
for nonpublishing builds (#82). No release or consumer pin changes are required.
