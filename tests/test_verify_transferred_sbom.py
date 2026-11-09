"""The privileged recheck admits inert bindings and refuses changed handoffs."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "actions/sbom-sidecars"))
spec = importlib.util.spec_from_file_location("verify_transferred", ROOT / "actions/sbom-sidecars/verify_transferred.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class WorkflowBoundaryTests(unittest.TestCase):
    def test_download_recheck_then_attestation_uses_only_reviewed_controller_code(self):
        workflow = (ROOT / ".github/workflows/release-package.yml").read_text()
        publish = workflow.split("  publish:\n", 1)[1]
        downloaded = publish.index("uses: actions/download-artifact@")
        checked = publish.index("run: python3 .roc-automation/actions/sbom-sidecars/verify_transferred.py")
        attested = publish.index("uses: actions/attest@")
        released = publish.index("uses: ./.roc-automation/actions/publish-release")
        self.assertTrue(downloaded < checked < attested < released)
        self.assertIn("ref: ${{ job.workflow_sha }}", publish)
        self.assertNotIn("blueprint run", publish)
        self.assertIn("INPUT_PRODUCING_ATTEMPT: ${{ needs.build.outputs.sbom-attempt }}", publish)
        self.assertNotIn("GITHUB_RUN_ATTEMPT", publish)
        self.assertIn('run: cp -- "$EVIDENCE" dist/sbom-evidence.json', workflow)
        assets = workflow.split("      - id: assets\n", 1)[1].split("      - name: Upload", 1)[0]
        self.assertNotIn("sbom-evidence", assets)
        self.assertIn("sbom-attempt: ${{ steps.sbom.outputs.run-attempt }}", workflow)
        action = (ROOT / "actions/sbom-sidecars/action.yml").read_text()
        self.assertIn("value: ${{ steps.sidecars.outputs.run-attempt }}", action)


class TransferredTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "dist").mkdir()
        self.source = {"server": "https://github.com", "repository": "owner/repo", "commit": "a" * 40}
        self.producer = {"workflow": "owner/repo/.github/workflows/release.yml@refs/heads/main", "run_id": "77", "run_attempt": "2"}
        self.version = "0.1.0-dry-run"
        self.make_candidate()

    def write(self, path, value):
        path = self.root / path
        path.write_text(json.dumps(value, sort_keys=True))

    def make_candidate(self, blueprint="0.11.0", spdx_2_3=True):
        for path in (self.root / "dist").iterdir():
            if path.is_file() or path.is_symlink():
                path.unlink()
        tool = verifier.sbom_sidecars.sbom_tool(blueprint)
        created = "2026-10-29T00:00:00Z"
        generator = {"tool": tool["url"], "tool_sha256": "", "compiler": tool["compiler"], "generator": tool["generator"], "blueprint": blueprint, "created": created}
        subjects = []
        for name in ("first.tar.zst", "second.tar.zst"):
            path = "dist/" + name
            data = ("final " + name).encode()
            (self.root / path).write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            namespace = f"https://github.com/owner/repo/sbom/{self.source['commit']}/{name}/revision-1"
            comment = json.dumps({"target": "source"})
            spdx = {"@graph": [
                {"type": "CreationInfo", "specVersion": "3.0.1"},
                {"type": "SpdxDocument", "spdxId": namespace + "#spdx/document", "rootElement": [namespace + "#Subject"]},
                {"type": "software_Package", "spdxId": namespace + "#Subject", "name": name, "software_packageVersion": self.version, "comment": comment,
                 "verifiedUsing": [{"algorithm": "sha256", "hashValue": digest}]}]}
            cdx = {"bomFormat": "CycloneDX", "specVersion": "1.7", "serialNumber": "urn:uuid:5998af92-9ec5-8bf2-8f95-44417875cd13",
                   "metadata": {"component": {"bom-ref": "Subject", "name": name, "version": self.version, "hashes": [{"alg": "SHA-256", "content": digest}],
                                              "properties": [{"name": "blueprint:target", "value": "source"}]}}}
            two = {"spdxVersion": "SPDX-2.3", "SPDXID": "SPDXRef-DOCUMENT", "documentNamespace": namespace, "documentDescribes": ["SPDXRef-Subject"],
                   "packages": [{"SPDXID": "SPDXRef-Subject", "name": name, "versionInfo": self.version, "comment": comment, "checksums": [{"algorithm": "SHA256", "checksumValue": digest}]}]}
            sidecars = []
            for suffix, document in [(".spdx.json", spdx), (".cdx.json", cdx)] + ([(".spdx-2.3.json", two)] if spdx_2_3 else []):
                self.write(path + suffix, document)
                sidecars.append(self.entry(path + suffix, format=verifier.FORMATS[suffix]))
            subject = self.entry(path, target="source", version=self.version,
                                 url=f"https://github.com/owner/repo/releases/download/{self.version}/{name}", inventory_sha256=hashlib.sha256(name.encode()).hexdigest(), sidecars=sidecars)
            if "manifest" in tool["features"]:
                # bind's default manifest covers the two default renderings;
                # the envelope also covers the separately requested SPDX 2.3.
                self.write(path + ".sbom-manifest.json", {
                    "profile": tool["profile"], "subject": {"filename": name, "version": self.version, "target": "source", "sha256": digest, "size": len(data)},
                    "inventory_sha256": subject["inventory_sha256"],
                    "context": {"namespace": namespace, "revision": "1", "created": created, "generator": tool["generator"]},
                    "sidecars": [{"filename": entry["name"], "format": entry["format"].lower(), "sha256": entry["sha256"], "size": entry["size"]} for entry in sidecars[:2]]})
                sidecars.append(self.entry(path + ".sbom-manifest.json", format="manifest"))
            subjects.append(subject)
        self.envelope = {"format": "roc-automation-sbom-evidence-v1", "source": self.source.copy(), "producer": self.producer.copy(), "generator": generator, "subjects": subjects, "dependencies": [], "coverage": []}
        self.subjects = "\n".join(subject["path"] for subject in subjects)
        self.assets = "\n".join([subject["path"] for subject in subjects] + [entry["path"] for subject in subjects for entry in subject["sidecars"]])
        self.save()

    def entry(self, path, **extra):
        content = (self.root / path).read_bytes()
        return {"name": Path(path).name, "path": path, "size": len(content), "sha256": hashlib.sha256(content).hexdigest(), **extra}

    def save(self):
        self.write(verifier.EVIDENCE_PATH, self.envelope)

    def verify(self, **extra):
        return verifier.verify(self.root, self.subjects, self.assets, self.source, self.producer, self.version, **extra)

    def test_valid_existing_record_generations_need_no_candidate_program(self):
        with patch("subprocess.run", side_effect=AssertionError("no program may execute")):
            for blueprint in ("0.7.0", "0.8.0", "0.11.0", "0.12.0"):
                with self.subTest(blueprint=blueprint):
                    self.make_candidate(blueprint)
                    self.assertEqual(self.verify(), self.envelope)

    def test_publication_retry_uses_the_build_attempt(self):
        env = {"GITHUB_SERVER_URL": self.source["server"], "GITHUB_REPOSITORY": self.source["repository"], "GITHUB_SHA": self.source["commit"],
               "GITHUB_WORKFLOW_REF": self.producer["workflow"], "GITHUB_RUN_ID": self.producer["run_id"], "GITHUB_RUN_ATTEMPT": "3", "INPUT_PRODUCING_ATTEMPT": "2",
               "INPUT_SUBJECTS": self.subjects, "INPUT_ASSETS": self.assets, "INPUT_VERSION": self.version}
        with patch.object(verifier.Path, "cwd", return_value=self.root):
            verifier.main(env)
            with self.assertRaisesRegex(ValueError, "producing run or attempt"):
                verifier.main({**env, "INPUT_PRODUCING_ATTEMPT": "3"})

    def test_source_and_run_identity_mismatches_reject(self):
        original = copy.deepcopy(self.envelope)
        for group, field in (("source", "server"), ("source", "repository"), ("source", "commit"), ("producer", "workflow"), ("producer", "run_id"), ("producer", "run_attempt")):
            with self.subTest(group=group, field=field):
                self.envelope = copy.deepcopy(original)
                self.envelope[group][field] += "different"
                self.save()
                with self.assertRaisesRegex(ValueError, "identity|run or attempt"):
                    self.verify()

    def test_changed_or_missing_final_bytes_reject(self):
        names = self.assets.splitlines()
        for name in names:
            with self.subTest(name=name):
                path = self.root / name
                content = path.read_bytes()
                path.write_bytes(content + b" ")
                with self.assertRaisesRegex(ValueError, "bytes differ"):
                    self.verify()
                path.unlink()
                with self.assertRaises(OSError):
                    self.verify()
                path.write_bytes(content)

    def test_swapped_manifest_rejects_even_when_envelope_digest_is_updated(self):
        first, second = self.envelope["subjects"]
        manifest_path = first["path"] + ".sbom-manifest.json"
        shutil.copyfile(self.root / (second["path"] + ".sbom-manifest.json"), self.root / manifest_path)
        first["sidecars"][-1] = self.entry(manifest_path, format="manifest")
        self.save()
        with self.assertRaisesRegex(ValueError, "manifest subject differs"):
            self.verify()

    def test_sidecar_about_other_bytes_rejects_even_when_envelope_digest_is_updated(self):
        first = self.envelope["subjects"][0]
        path = first["path"] + ".spdx-2.3.json"
        document = json.loads((self.root / path).read_text())
        document["packages"][0]["checksums"][0]["checksumValue"] = "f" * 64
        self.write(path, document)
        first["sidecars"][2] = self.entry(path, format="SPDX-2.3")
        self.save()
        with self.assertRaisesRegex(ValueError, "sidecar subject differs"):
            self.verify()

    def test_duplicate_embedded_subject_metadata_is_not_an_identity(self):
        subject = self.envelope["subjects"][0]
        path = subject["path"] + ".spdx-2.3.json"
        document = json.loads((self.root / path).read_text())
        document["packages"][0]["comment"] = '{"target":"other","target":"source"}'
        self.write(path, document)
        subject["sidecars"][2] = self.entry(path, format="SPDX-2.3")
        self.save()
        with self.assertRaisesRegex(ValueError, "repeats JSON key"):
            self.verify()

    def test_expected_subjects_and_formats_cannot_be_omitted_or_relabelled(self):
        original = copy.deepcopy(self.envelope)
        variants = [lambda e: e["subjects"].pop(), lambda e: e["subjects"].append(e["subjects"][0]),
                    lambda e: e["subjects"][0]["sidecars"].pop(2),
                    lambda e: e["subjects"][0]["sidecars"][0].update(format="SPDX-2.3"),
                    lambda e: e["subjects"][0].update(version="1.0.0"), lambda e: e["subjects"][0].update(target="x86_64-linux")]
        for alter in variants:
            self.envelope = copy.deepcopy(original)
            alter(self.envelope)
            self.save()
            with self.assertRaises(ValueError):
                self.verify()

    def test_asset_handoff_cannot_omit_sidecars_or_publish_the_envelope(self):
        original = self.assets
        for listing in (original + "\n" + verifier.EVIDENCE_PATH, "\n".join(original.splitlines()[1:]),
                        original.replace("dist/first.tar.zst.spdx-2.3.json\n", ""), original + "\ndist/rogue.cdx.json"):
            self.assets = listing
            with self.assertRaises(ValueError):
                self.verify()

    def test_extra_regular_release_asset_is_checked_without_claiming_its_composition(self):
        (self.root / "dist/manual.pdf").write_bytes(b"%PDF extra")
        self.assets += "\ndist/manual.pdf"
        self.verify()

    def test_unsafe_and_aliased_paths_are_refused(self):
        original = copy.deepcopy(self.envelope)
        for path in ("/tmp/subject", "dist/../first.tar.zst", "dist//first.tar.zst", "dist/./first.tar.zst", "dist\\first.tar.zst", "dist/*.tar.zst", "dist/first.tar.zst\nother", "dist/-subject"):
            with self.subTest(path=path):
                self.envelope = copy.deepcopy(original)
                self.envelope["subjects"][0]["path"] = path
                self.save()
                with self.assertRaises(ValueError):
                    self.verify()

    def test_symlink_files_parents_and_hardlinks_are_refused(self):
        evidence = self.root / verifier.EVIDENCE_PATH
        saved = evidence.read_bytes()
        evidence.unlink()
        (self.root / "outside.json").write_bytes(saved)
        evidence.symlink_to(self.root / "outside.json")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.verify()
        evidence.unlink()
        evidence.write_bytes(saved)
        os.link(evidence, self.root / "alias.json")
        with self.assertRaisesRegex(ValueError, "unaliased"):
            self.verify()
        (self.root / "alias.json").unlink()
        (self.root / "dist").rename(self.root / "real-dist")
        (self.root / "dist").symlink_to(self.root / "real-dist", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.verify()

    def test_duplicate_json_non_json_and_size_depth_bounds_reject(self):
        evidence = self.root / verifier.EVIDENCE_PATH
        for content in (b'{"format":"one","format":"two"}', b'{"nested":{"key":1,"key":2}}', b'{"x":NaN}', '{"utf16":"unsupported"}'.encode("utf-16"), b"{", b" " * (verifier.MAX_JSON_BYTES + 1), b'{"nested":' * 66 + b"{}" + b"}" * 66):
            with self.subTest(content=content[:50]):
                evidence.write_bytes(content)
                with self.assertRaises(ValueError):
                    self.verify()

    def test_registry_and_manifest_inventory_context_and_profile_are_checked(self):
        original = copy.deepcopy(self.envelope)
        for field in ("tool", "tool_sha256", "compiler", "generator"):
            self.envelope = copy.deepcopy(original)
            self.envelope["generator"][field] += "wrong"
            self.save()
            with self.assertRaises(ValueError):
                self.verify()
        self.envelope = original
        path = self.envelope["subjects"][0]["path"] + ".sbom-manifest.json"
        baseline = json.loads((self.root / path).read_text())
        for field, replacement in (("inventory_sha256", "b" * 64), ("profile", "blueprint-spdx-3.0-v1"), ("context", {**baseline["context"], "generator": "Tool: other"})):
            changed = {**baseline, field: replacement}
            self.write(path, changed)
            self.envelope["subjects"][0]["sidecars"][-1] = self.entry(path, format="manifest")
            self.save()
            with self.assertRaises(ValueError):
                self.verify()


if __name__ == "__main__":
    unittest.main()
