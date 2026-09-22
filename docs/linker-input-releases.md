# Publish linker inputs from a pull request

This page specifies the operational contract for
`actions/publish-build-inputs`. Read the
[platform build-input guide](platform-build-inputs.md) first for the ownership,
rebuild, and consumer model. The [security model](security-model.md) is normative
for the publisher's authority boundaries.

A platform repository can need new linker-input bytes before the change that uses
them is mergeable. The producer therefore runs on a same-repository pull-request
branch, while publication remains controlled by a workflow dispatched from the
trusted default branch. The release belongs to that exact PR commit. It is not a
temporary preview and is not rebuilt or promoted after merge.

## Producer contract

The unprivileged producer workflow builds and tests every supported target from
the PR branch. It uploads one flat Actions artifact containing top-level regular
files only:

- `build-input-release.json`; and
- one deterministic archive for every target declared by the manifest.

The manifest is canonical JSON with this envelope:

```json
{
  "schema_version": 1,
  "kind": "link-inputs",
  "source": {
    "repository": "OWNER/PLATFORM",
    "sha": "0123456789abcdef0123456789abcdef01234567",
    "ref": "refs/heads/change-link-inputs",
    "workflow": "OWNER/PLATFORM/.github/workflows/link-inputs.yml",
    "input_fingerprint": "<versioned producer-input identity>"
  },
  "assets": {
    "x64glibc": {
      "asset": "link-inputs-x64glibc.tar",
      "sha256": "<lowercase SHA-256>",
      "size": 1234
    }
  }
}
```

Use the platform's complete supported target set rather than publishing a partial
replacement under the same contract. The input fingerprint covers every source,
recipe, tool pin, generated input, target option, and other value that can affect
the archives. It determines whether the selected release is current for the
working tree; each asset SHA-256 identifies the exact bytes.

The producer must have no release, repository-write, or pull-request-write
permission. Generate GitHub build-provenance attestations for the manifest and
each target archive. Producer jobs may compile and execute PR code because they
do not possess publication authority.

## Trusted publisher contract

The platform owns a small `workflow_dispatch` publisher workflow on its default
branch. That workflow uses the `actions/publish-build-inputs` composite action
from `roc-automation` at one reviewed full commit SHA and supplies only:

- the same-repository pull-request number;
- the producer workflow filename;
- the candidate Actions artifact name;
- the repository-relative lock path; and
- the release tag prefix.

Give that job `actions: write`, `attestations: read`, `contents: write`, and
`pull-requests: read`; set all other permissions to none. A typical caller is:

```yaml
name: Publish linker inputs

on:
  workflow_dispatch:
    inputs:
      pull-request:
        description: Same-repository material-change PR number
        required: true
        type: string

permissions: {}

jobs:
  publish:
    runs-on: ubuntu-24.04
    permissions:
      actions: write
      attestations: read
      contents: write
      pull-requests: read
    steps:
      - uses: OWNER/roc-automation/actions/publish-build-inputs@REVIEWED_FULL_SHA
        with:
          pull-request: ${{ inputs.pull-request }}
          producer-workflow: link-inputs.yml
          candidate-artifact: link-input-release
          lock-path: link-inputs.lock.json
          release-prefix: linker-inputs
```

The composite action obtains its controller implementation from that exact
`roc-automation` revision. Its privileged job does not check out the platform PR,
execute platform scripts, import candidate modules, or source files from the
candidate artifact.

The controller requires a manual dispatch on the live default-branch commit. It
resolves the open PR, dispatches the named producer workflow at the exact current
PR head, captures the resulting run, and rejects a run whose repository, event,
workflow, ref, SHA, or conclusion differs. It validates the artifact's complete
file set, bounded extraction, manifest schema, target set, byte sizes, and hashes.
It then verifies every attestation against the expected repository, producer
workflow, source ref, and source SHA.

The release tag is the configured prefix followed by `-sha256-` and the SHA-256
of the exact manifest bytes. The controller creates a draft, uploads the manifest,
generated lock, and declared target archives, verifies their complete names and
sizes, re-downloads every release asset into a clean directory, verifies its
SHA-256 against the candidate, then publishes. Existing mismatched tags or
releases are rejected; an interrupted draft can be resumed only when every
published byte matches the candidate.
Enable immutable releases in the platform repository.

This PR-branch admission action is distinct from the generic
`.github/workflows/publish-linker-inputs.yml` reusable workflow. The generic
workflow publishes a caller's default-branch artifact under an explicit semantic
version and does not update an adoption lock. Use `actions/publish-build-inputs`
when a platform must publish and adopt the exact material-change PR before merge.

Finally, the controller creates one lease-guarded, GitHub-signed commit on the PR
branch. It modifies only the configured lock path and verifies the resulting
signature, file set, parent, and live PR head. Any competing branch update stops
publication adoption rather than being overwritten.

## Review and merge the adoption

The generated lock records the manifest identity, source identity, input
fingerprint, release, repository, and every target archive's filename, SHA-256,
and size. Publication and lock creation are one admission transaction, but the
lock is not adopted until the updated PR passes its required checks and is merged.

Review the PR as a dependency change:

1. Confirm that the source changes actually require or intentionally regenerate
   linker inputs and that the producer-input inventory is complete.
2. Inspect the producer and publisher run links, exact source SHA, attestations,
   target coverage, and consumer-facing link tests.
3. Confirm that the publisher added only the lock and that its commit is verified.
4. Rerun routine validation on the new PR head. It must consume the release through
   the lock and must not rebuild linker inputs.
5. Merge with a merge commit so the exact attested producer commit and signed
   lock commit remain in history.

Do not rebuild, copy, retag, or promote the release after merge. The merged lock
permanently selects the PR-built bytes. If source or build inputs change again,
produce a new candidate and content-derived release. Byte-identical recovery may
reuse the same content identity; changed bytes require a new identity.

Fork pull requests cannot receive the signed lock commit. Move an accepted change
to a same-repository branch before publication. Do not grant a fork or PR workflow
write credentials as a workaround.

## Routine consumption

Ordinary pull requests, nightly compiler updates, local builds, and platform
release assembly read the committed lock. They restore or download each selected
archive, recompute size and SHA-256 even on a cache hit, validate the archive and
manifest, and install it through fresh staging. They do not call the attestation
service and do not fall back to a source build when the cache is empty.

A cache key is an optimization derived from the reviewed content identity. The
lock, not the cache key or release name, authorizes bytes. A missing archive is a
download/cache failure; a stale input fingerprint is a request for an intentional
new linker-input release. See the
[artifact verification guide](artifact-verification.md) for the distinction
between admission evidence and routine integrity checks.
