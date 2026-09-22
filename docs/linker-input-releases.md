# Signed linker-input releases

Platform repositories own their external linker inputs and release them on an
independent `linker-inputs-vMAJOR.MINOR.PATCH` cadence. The reusable publisher in
`.github/workflows/publish-linker-inputs.yml` is deliberately narrower than a
general release workflow: it downloads an Actions artifact but never checks out
or executes caller-controlled code.

The controller implementation is exposed as the composite action at
`actions/publish-linker-inputs`. Privileged reusable workflows invoke that
action at an exact reviewed commit instead of checking this repository out into
their workspace.

The caller must invoke the workflow from an explicit `workflow_dispatch` on its
default branch and pin this reusable workflow with a full 40-character commit
SHA in `jobs.<job>.uses`. GitHub exposes the caller identity through
`github.workflow_ref` and `github.workflow_sha` inside a reusable workflow, so
the callee cannot reliably re-read its own `@ref`; pinning is enforced by code
review and repository tests in each caller. The workflow
uploads one artifact containing only top-level regular files:

- `dependency.json`, the release manifest;
- one or more deterministic archives, SPDX SBOMs, checksum files, or notices
  declared by that manifest.

`dependency.json` has this controller-owned envelope (additional optional
metadata is limited to the keys accepted by the controller):

```json
{
  "schema_version": 1,
  "release_tag": "linker-inputs-v1.0.0",
  "source": {
    "repository": "lukewilliamboswell/roc-platform-template-example",
    "commit": "0123456789abcdef0123456789abcdef01234567",
    "ref": "refs/heads/main"
  },
  "assets": [
    {
      "name": "roc-example-linker-inputs-1.0.0.tar.gz",
      "sha256": "<lowercase SHA-256>",
      "size": 1234,
      "role": "linker-input-archive"
    }
  ]
}
```

Names cannot contain paths, symlinks and directories are rejected, undeclared
files are rejected, and every byte count and SHA-256 must match. The controller
also requires the manifest repository and commit to equal the workflow event.

After validation, GitHub build-provenance attestations are generated for the
exact file set. The controller refuses an existing tag or release, creates a
draft, uploads the files, downloads them into a clean directory, compares the
complete set and hashes, and publishes. It finally requires GitHub to report the
release as immutable. Repositories must therefore enable immutable releases
before their first dispatch.

The platform's adoption lock is intentionally not created by this workflow. A
separate, reviewed consumer change records the published archive identity,
signer repository/workflow/SHA, source commit, SBOM identity, and input
fingerprint only after `gh attestation verify` succeeds.
