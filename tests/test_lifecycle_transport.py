"""Exercise exact-ID download normalization and inert candidate assembly."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile

BINARY = os.environ.get("ROC_LIFECYCLE_TRANSPORT_BINARY")
ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "actions/release-compatibility/archive.py"


@unittest.skipUnless(BINARY, "set ROC_LIFECYCLE_TRANSPORT_BINARY to the built utility")
class LifecycleTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        self.extracted = self.root / "evidence"
        self.candidate = self.root / "candidate"
        self.manifest = self.root / "transport.json"

    def trace(self, stage, files):
        return json.dumps(dict(schema_version=1, profile="roc-automation-lifecycle-trace-v1",
                              repository="o/r", source_sha="a"*40, run_id="100", run_attempt="1",
                              stage=stage, runner="ubuntu-24.04", obligations=[], files=files)).encode()

    def archive(self, parent, stage="prepare", content=b"tested bytes", extra=None):
        parent.mkdir(parents=True, exist_ok=True)
        path = parent / "artifact"
        with zipfile.ZipFile(path, "w") as archive:
            files = {f"{stage}-trace.json": self.trace(stage, ["dist/bundle.tar.zst"]), "dist/bundle.tar.zst": content}
            if extra:
                files.update(extra)
            for name, data in files.items():
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, data)
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()

    def run_tool(self, *args, success=True, extra_env=None):
        ran = subprocess.run([BINARY, *map(str, args)], capture_output=True, text=True, timeout=15, env={**os.environ, **(extra_env or {})})
        if success:
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        else:
            self.assertNotEqual(ran.returncode, 0)
        return ran

    def extract(self, artifacts, success=True):
        self.manifest.write_text(json.dumps(dict(artifacts=artifacts)))
        return self.run_tool("extract", self.manifest, self.raw, self.extracted, sys.executable, HELPER, success=success)

    def test_one_artifact_flat_download_is_normalized_by_verified_identity(self):
        digest = self.archive(self.raw)
        self.extract([dict(id=1, name="lifecycle-prepare-ubuntu-24.04-1", digest=digest)])
        self.assertEqual((self.extracted / "lifecycle-prepare-ubuntu-24.04-1/dist/bundle.tar.zst").read_bytes(), b"tested bytes")
        self.run_tool("assemble", self.extracted, self.candidate)
        self.assertEqual((self.candidate / "dist/bundle.tar.zst").read_bytes(), b"tested bytes")
        self.assertFalse(list(self.candidate.glob("**/*trace.json")))

    def test_multiple_artifacts_keep_names_and_identical_bytes_can_recur(self):
        artifacts = []
        for index, stage in enumerate(("prepare", "seal"), 1):
            name = f"lifecycle-{stage}-ubuntu-24.04-1"
            digest = self.archive(self.raw / name, stage)
            artifacts.append(dict(id=index, name=name, digest=digest))
        self.extract(artifacts)
        self.run_tool("assemble", self.extracted, self.candidate)
        self.assertEqual((self.candidate / "dist/bundle.tar.zst").read_bytes(), b"tested bytes")

    def test_conflicting_copies_cannot_be_combined(self):
        artifacts = []
        for index, stage in enumerate(("prepare", "seal"), 1):
            name = f"lifecycle-{stage}-ubuntu-24.04-1"
            digest = self.archive(self.raw / name, stage, bytes([index]))
            artifacts.append(dict(id=index, name=name, digest=digest))
        self.extract(artifacts)
        ran = self.run_tool("assemble", self.extracted, self.candidate, success=False)
        self.assertIn("conflicting candidate bytes", ran.stderr)

    def test_raw_digest_mismatch_rejects_before_extraction(self):
        self.archive(self.raw)
        self.extract([dict(id=1, name="selected", digest="sha256:"+"0"*64)], success=False)
        self.assertFalse((self.extracted / "selected").exists())

    def test_traversal_in_raw_archive_rejects_before_extraction(self):
        digest = self.archive(self.raw, extra={"../escape": b"bad"})
        self.extract([dict(id=1, name="selected", digest=digest)], success=False)
        self.assertFalse((self.extracted / "selected").exists())
        self.assertFalse((self.root / "escape").exists())

    def test_duplicate_identity_and_unexpected_raw_file_are_rejected(self):
        digest = self.archive(self.raw)
        artifact = dict(id=1, name="selected", digest=digest)
        self.extract([artifact, artifact], success=False)
        (self.raw / "extra").write_bytes(b"unexpected")
        self.extract([artifact], success=False)

    def test_sbom_subject_mapping_and_trace_require_exact_declared_outputs(self):
        self.candidate.mkdir()
        subject = "sample-" + "a"*44 + ".tar.zst"
        (self.candidate / subject).write_bytes(b"subject")
        topology = json.loads((ROOT / "tests/fixtures/lifecycle/topology-package.json").read_text())
        context = json.loads((ROOT / "tests/fixtures/lifecycle/context-pr.json").read_text())
        plan = self.root / "plan.json"
        plan.write_text(json.dumps(dict(schema=1, context=context, topology=topology, full_rehearsal=True,
                                       obligations=[], intermediates=[], bundle_order=["package"], assets=[dict(subject="package", role="bundle", target="source", path="sample-{hash}.tar.zst")])))
        outputs = self.root / "outputs"
        outputs.write_text("")
        env = dict(GITHUB_WORKSPACE=str(self.root), GITHUB_OUTPUT=str(outputs))
        self.run_tool("subject", plan, "0", self.candidate, extra_env=env)
        self.assertEqual(outputs.read_text(), "files=candidate/"+subject+"\n")

        outside = self.run_tool("subject", plan, "0", self.candidate, success=False,
                                extra_env={**env, "GITHUB_WORKSPACE": str(self.root / "other-workspace")})
        self.assertIn("inside the workspace", outside.stderr)
        evidence = self.root / "producer.json"
        evidence.write_text("{}")
        scratch = self.root / "sbom"
        self.run_tool("sbom-trace", plan, "0", self.candidate, evidence, evidence, scratch, success=False, extra_env=env)
        self.assertFalse((scratch / "sbom-trace.json").exists())
        for suffix in ("spdx.json", "cdx.json", "sbom-manifest.json"):
            (self.candidate / (subject+"."+suffix)).write_text("{}")
        self.run_tool("sbom-trace", plan, "0", self.candidate, evidence, evidence, scratch, extra_env=env)
        trace = json.loads((scratch / "sbom-trace.json").read_text())
        self.assertEqual(trace["obligations"], [dict(id="sbom-0", outcome="passed")])
        self.assertEqual(trace["files"], [subject]+[subject+"."+suffix for suffix in ("spdx.json", "cdx.json", "sbom-manifest.json")])
        self.run_tool("subject", plan, "1", self.candidate, success=False, extra_env=env)

    def test_pack_uploads_only_public_files_metadata_and_inert_diagnostics(self):
        self.candidate.mkdir()
        (self.candidate / "dist").mkdir()
        (self.candidate / "dist/bundle.tar.zst").write_bytes(b"one built candidate")
        scratch = self.root / "scratch"
        scratch.mkdir()
        trace = scratch / "prepare-trace.json"
        trace.write_bytes(self.trace("prepare", ["dist/bundle.tar.zst"]))
        metadata = scratch / "bundle-map.json"
        metadata.write_text("{}")
        (scratch / "untrusted-executable").write_bytes(b"not evidence")
        diagnostics = scratch / "diagnostics"
        diagnostics.mkdir()
        (diagnostics / "stdout.log").write_text("::error::inert diagnostic")
        packed = self.root / "packed"
        self.run_tool("pack", trace, self.candidate, metadata, diagnostics, packed)
        self.assertEqual(sorted(str(path.relative_to(packed)) for path in packed.rglob("*") if path.is_file()),
                         ["bundle-map.json", "diagnostics/stdout.log", "dist/bundle.tar.zst", "prepare-trace.json"])
        self.assertEqual((packed / "dist/bundle.tar.zst").read_bytes(), b"one built candidate")
        self.run_tool("pack", trace, self.candidate, metadata, diagnostics, packed, success=False)

    def test_host_restore_preserves_exact_bytes_and_refuses_checkout_overwrite(self):
        self.candidate.mkdir()
        (self.candidate / "platform/targets").mkdir(parents=True)
        (self.candidate / "platform/targets/linux.a").write_bytes(b"tested linux host")
        (self.candidate / "platform/targets/windows.a").write_bytes(b"tested windows host")
        topology = json.loads((ROOT / "tests/fixtures/lifecycle/topology-platform.json").read_text())
        context = json.loads((ROOT / "tests/fixtures/lifecycle/context-pr.json").read_text())
        plan = self.root / "plan.json"
        plan.write_text(json.dumps(dict(schema=1, context=context, topology=topology, full_rehearsal=True,
                                       obligations=[], intermediates=[], bundle_order=["core","platform"], assets=[])))
        workspace = self.root / "workspace"
        workspace.mkdir()
        self.run_tool("restore", plan, self.candidate, workspace)
        self.assertEqual((workspace / "platform/targets/linux.a").read_bytes(), b"tested linux host")
        (workspace / "platform/targets/linux.a").write_bytes(b"checkout source must survive")
        self.run_tool("restore", plan, self.candidate, workspace, success=False)
        self.assertEqual((workspace / "platform/targets/linux.a").read_bytes(), b"checkout source must survive")

    def test_candidate_is_not_reused_or_followed(self):
        digest = self.archive(self.raw)
        self.extract([dict(id=1, name="selected", digest=digest)])
        self.candidate.mkdir()
        (self.candidate / "previous").write_bytes(b"preserve")
        self.run_tool("assemble", self.extracted, self.candidate, success=False)
        self.assertEqual((self.candidate / "previous").read_bytes(), b"preserve")

    def test_public_assembly_excludes_host_intermediates_but_retains_their_evidence(self):
        artifacts = []
        for index, stage in enumerate(("prepare", "host"), 1):
            name = f"lifecycle-{stage}-ubuntu-24.04-1"
            digest = self.archive(self.raw / name, stage)
            artifacts.append(dict(id=index, name=name, digest=digest))
        self.extract(artifacts)
        host = self.extracted / artifacts[1]["name"]
        (host / "dist/bundle.tar.zst").unlink()
        (host / "native").mkdir()
        (host / "native/host.a").write_bytes(b"linker input")
        (host / "host-trace.json").write_bytes(self.trace("host", ["native/host.a"]))
        self.run_tool("assemble-public", self.extracted, self.candidate)
        self.assertEqual((self.candidate / "dist/bundle.tar.zst").read_bytes(), b"tested bytes")
        self.assertFalse((self.candidate / "native").exists())
        self.assertEqual((host / "native/host.a").read_bytes(), b"linker input")


if __name__ == "__main__":
    unittest.main()
