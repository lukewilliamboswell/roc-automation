import importlib.util
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("publisher", Path(__file__).parents[1] / "actions/publish-linker-inputs/publish.py")
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class PublisherTests(unittest.TestCase):
    def test_privileged_workflow_uses_only_sha_pinned_action_bundle(self):
        workflow = (Path(__file__).parents[1] / ".github/workflows/publish-linker-inputs.yml").read_text()
        self.assertNotIn("actions/checkout", workflow)
        self.assertNotIn("github.workflow_ref", workflow)
        self.assertNotIn("github.workflow_sha", workflow)
        refs = re.findall(r"uses: lukewilliamboswell/roc-automation/actions/publish-linker-inputs@([0-9a-f]{40})", workflow)
        self.assertEqual(refs, ["e21bc1b63f8d5ae4f293f596e5102e5c8f21f637"] * 2)

    def candidate(self, root, *, extra=None, name="archive.tar.gz"):
        root = Path(root)
        data = b"deterministic archive"
        (root / name).write_bytes(data)
        manifest = {
            "schema_version": 1,
            "release_tag": "linker-inputs-v1.0.0",
            "source": {"repository": "roc-lang/example", "commit": "a" * 40},
            "assets": [{"name": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}],
        }
        (root / "dependency.json").write_text(json.dumps(manifest))
        if extra:
            (root / extra).write_text("unexpected")
        return root

    def validate(self, root, **kwargs):
        return publisher.validate_candidate(Path(root), "dependency.json", kwargs.get("tag", "linker-inputs-v1.0.0"), "roc-lang/example", "a" * 40)

    def test_accepts_exact_regular_file_set(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual([p.name for p in self.validate(self.candidate(directory))], ["dependency.json", "archive.tar.gz"])

    def test_rejects_extra_tampered_and_unsafe_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.candidate(directory, extra="extra")
            with self.assertRaisesRegex(ValueError, "file set"):
                self.validate(root)
            (root / "extra").unlink()
            (root / "archive.tar.gz").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "identity"):
                self.validate(root)
        with tempfile.TemporaryDirectory() as directory:
            root = self.candidate(directory)
            manifest = json.loads((root / "dependency.json").read_text())
            manifest["assets"][0]["name"] = "../escape"
            (root / "dependency.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "top-level"):
                self.validate(root)

    def test_rejects_symlink_and_wrong_source_or_tag(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.candidate(directory)
            (root / "archive.tar.gz").unlink()
            (root / "archive.tar.gz").symlink_to(root / "dependency.json")
            with self.assertRaisesRegex(ValueError, "non-regular|regular file"):
                self.validate(root)
        with tempfile.TemporaryDirectory() as directory:
            root = self.candidate(directory)
            with self.assertRaisesRegex(ValueError, "release tag"):
                self.validate(root, tag="runtime-v1.0.0")

    def test_rejects_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.candidate(directory)
            text = (root / "dependency.json").read_text()
            (root / "dependency.json").write_text(text[:-1] + ',"schema_version":1}')
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                self.validate(root)

    def test_event_requires_manual_default_branch_exact_sha(self):
        valid = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REPOSITORY": "roc-lang/example", "GITHUB_SHA": "a" * 40, "GITHUB_REF": "refs/heads/main"}
        with patch.dict(os.environ, valid, clear=True), patch.object(publisher, "gh_json", return_value={"default_branch": "main"}):
            self.assertEqual(publisher.enforce_event("roc-lang/example", "a" * 40), "main")
        for changed in ({"GITHUB_EVENT_NAME": "push"}, {"GITHUB_REF": "refs/heads/feature"}, {"GITHUB_SHA": "b" * 40}):
            env = {**valid, **changed}
            with self.subTest(changed=changed), patch.dict(os.environ, env, clear=True), patch.object(publisher, "gh_json", return_value={"default_branch": "main"}), self.assertRaises(ValueError):
                publisher.enforce_event("roc-lang/example", "a" * 40)

    def test_existing_release_or_tag_is_never_overwritten(self):
        with patch.object(publisher.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "HTTP/2.0 200 OK\n"
            with self.assertRaisesRegex(ValueError, "already exists"):
                publisher.ensure_absent("roc-lang/example", "linker-inputs-v1.0.0")

    def test_absence_requires_two_authoritative_404_responses(self):
        not_found = type("Result", (), {"returncode": 1, "stdout": "HTTP/2.0 404 Not Found\n", "stderr": ""})()
        with patch.object(publisher.subprocess, "run", return_value=not_found) as run:
            publisher.ensure_absent("roc-lang/example", "linker-inputs-v1.0.0")
            self.assertEqual(run.call_count, 2)
        network_failure = type("Result", (), {"returncode": 1, "stdout": "", "stderr": "offline"})()
        with patch.object(publisher.subprocess, "run", return_value=network_failure), self.assertRaisesRegex(ValueError, "could not prove"):
            publisher.ensure_absent("roc-lang/example", "linker-inputs-v1.0.0")

    def test_publish_requires_immutable_result(self):
        with tempfile.TemporaryDirectory() as directory:
            files = self.validate(self.candidate(directory))
            with patch.object(publisher, "ensure_absent"), patch.object(publisher, "compare_download"), patch.object(publisher.subprocess, "run"), patch.object(publisher, "gh_json", return_value={"draft": False, "immutable": False}), patch.dict(os.environ, {"GITHUB_SHA": "a" * 40}):
                with self.assertRaisesRegex(ValueError, "not immutable"):
                    publisher.publish("roc-lang/example", "linker-inputs-v1.0.0", "", files)


if __name__ == "__main__":
    unittest.main()
