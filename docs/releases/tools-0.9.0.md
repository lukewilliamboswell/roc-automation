# Tools 0.9.0

These notes describe the next tools release; no release is published by this change.

- Fix #114: exercise `plume` consistently in the native downstream-status fixture,
  keeping scheduled/manual selection and spoofed, stale and incomplete evidence
  checks against the actual dashboard utility.

The tools release and SBOM-chain example now use `0.0.1-dryrun` consistently
for nonpublishing builds (#82). No release or consumer pin changes are required.

SBOM generation accepts hyphenated SemVer prereleases and rejects empty or
leading-zero numeric identifiers and build metadata consistently with the
publisher. Mixed source releases enumerate only `.tar.zst` members; ZIP/PDF
subjects keep composition-only inventories. Reviewed Blueprint 0.11.0 SBOM
tools and the corrected v2 document profile are admitted on all five native
systems, with real released-fixture and legacy supplier-chain acceptance (#66).
