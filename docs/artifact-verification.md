# Verify released artifacts without a routine attestation dependency

Use an immutable content hash as the identity of every released compiler,
package, platform, native library, tool, or other binary input consumed by
builds and tests. Record that hash in reviewed source, a lock file, or another
version-controlled manifest together with the artifact URL and release identity.

## Separate admission from routine use

An attestation answers who or which workflow produced an artifact and from which
source context. A content hash answers whether the bytes being consumed are the
exact bytes that were reviewed. These checks belong at different stages:

1. **Publish:** produce the artifact, checksum, provenance attestation, SBOM, and
   immutable release metadata.
2. **Adopt:** review the release, verify its attestation and source identity, test
   the artifact, and commit its content hash and URL in a reviewed change.
3. **Consume:** download or load the artifact, calculate its hash locally, and
   compare it with the reviewed pin before extraction or execution.
4. **Reuse:** recheck cached bytes against the same pin. A cache is storage, not
   a source of trust.
5. **Update:** repeat admission only when intentionally adopting different bytes.

The reviewed hash is the trust anchor for ordinary builds after adoption.
Attestation does not replace content verification, and repeating the same remote
attestation lookup does not strengthen the identity of already pinned bytes.
Admission asks whether the producer identity and source are acceptable; routine
use asks whether storage returned those admitted bytes. Keeping those questions
separate avoids turning every build into an online provenance-policy decision
while still detecting corrupted or substituted downloads and cache entries.

## Routine CI policy

Routine CI and local builds must not call a remote signing, transparency, or
attestation service when a reviewed content hash is available. This avoids
making builds depend on service availability, rate limits, authentication, or a
network response for evidence already evaluated during adoption.

A routine consumer should:

- reject a missing or malformed content hash;
- verify a newly downloaded file before moving it into a shared cache;
- verify a cached file again before using it;
- validate the archive inventory, internal manifests, target metadata, and
  release identity where those exist;
- extract into a temporary directory before replacing working inputs; and
- record the URL, release, and content hash in generated provenance without
  implying that a runtime attestation check occurred.

If the digest does not match, fail without extracting, executing, or installing
the artifact. Do not fall back to a floating URL, a newly discovered release, or
a remote attestation lookup.

## Appropriate attestation use

Use an attestation service during release publication, explicit lock adoption,
security investigation, periodic provenance audit, or manual verification by a
downstream user. Export attestation bundles with releases when practical so that
provenance can be checked offline and retained independently of the service.

If policy requires provenance verification on every run despite a reviewed
content hash, make that exceptional network dependency explicit and separate it
from the ordinary validation path. Document its availability and credential
requirements and do not describe it as content-integrity verification.

## Review checklist

Before adopting a released artifact, record evidence that:

- the release and tag are immutable and identify the intended source revision;
- the producing workflow and runner policy are acceptable;
- the attestation subject digest equals the downloaded artifact digest;
- the artifact passed its consumer-facing tests;
- the reviewed lock contains the exact URL, filename, release, and SHA-256; and
- routine CI verifies that SHA-256 without contacting the attestation service.

After adoption, a pull request that changes any URL or digest is a supply-chain
change and receives the same review as a dependency update.

A same-repository pull-request release is admissible when a trusted default-branch
publisher binds it to the exact branch SHA and producer workflow, verifies its
attestations, and commits only the resulting content lock. Merging that reviewed
lock is the adoption event. Routine consumers then use the same local hash checks
as for any other release; they do not query provenance again.
