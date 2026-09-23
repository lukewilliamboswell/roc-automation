import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "actions/publish-build-inputs/publish_build_inputs.py"
spec = importlib.util.spec_from_file_location("publish_build_inputs", MODULE)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class BuildInputPublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, GITHUB_REPOSITORY="owner/project", DEFAULT_BRANCH="main",
                              GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_REF="refs/heads/main",
                              GITHUB_SHA="a" * 40, GITHUB_OUTPUT=str(self.root / "output"))
        self.env.start()
        self.addCleanup(self.env.stop)

    def candidate(self):
        archive = self.root / "link-inputs-x64glibc.tar"
        archive.write_bytes(b"archive")
        manifest = {
            "schema_version": 1, "kind": "link-inputs",
            "source": {"repository": "owner/project", "sha": "b" * 40,
                       "ref": "refs/heads/feature",
                       "workflow": "owner/project/.github/workflows/link-inputs.yml",
                       "input_fingerprint": "c" * 64},
            "assets": {"x64glibc": {"asset": archive.name, "size": archive.stat().st_size,
                                      "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}},
        }
        (self.root / "build-input-release.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
        return manifest

    def test_candidate_is_bound_to_exact_branch_workflow_and_bytes(self):
        manifest = self.candidate()
        observed, encoded, kind = p.validate_candidate(self.root, "link-inputs.yml", "feature", "b" * 40)
        self.assertEqual(observed, manifest)
        self.assertEqual(kind, "link-inputs")
        self.assertEqual(hashlib.sha256(encoded).hexdigest(),
                         hashlib.sha256((self.root / "build-input-release.json").read_bytes()).hexdigest())
        (self.root / "link-inputs-x64glibc.tar").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "differs"):
            p.validate_candidate(self.root, "link-inputs.yml", "feature", "b" * 40)

    def test_default_branch_dispatch_is_the_only_privileged_entry(self):
        with patch.object(p, "api", return_value={"object": {"sha": "a" * 40}}):
            p.require_default_dispatch()
        for event, ref in (("pull_request", "refs/heads/main"),
                           ("workflow_dispatch", "refs/heads/feature"),
                           ("workflow_dispatch", "refs/tags/main")):
            with self.subTest(event=event, ref=ref), patch.dict(
                    os.environ, GITHUB_EVENT_NAME=event, GITHUB_REF=ref):
                with self.assertRaises(ValueError):
                    p.require_default_dispatch()

    def test_signed_commit_is_lease_guarded_and_lock_only(self):
        responses = [
            {"data": {"createCommitOnBranch": {"commit": {"oid": "d" * 40}}}},
            {"commit": {"verification": {"verified": True}},
             "files": [{"filename": "link-inputs.lock.json"}]},
            {"head": {"sha": "d" * 40}},
        ]
        with patch.object(p, "api", side_effect=responses) as api:
            result = p.signed_lock_commit("7", "feature", "b" * 40,
                                          "link-inputs.lock.json", b"{}\n", "deps-link-inputs-sha256-x")
        self.assertEqual(result, "d" * 40)
        request = api.call_args_list[0].args[1]["variables"]["input"]
        self.assertEqual(request["expectedHeadOid"], "b" * 40)
        self.assertEqual([item["path"] for item in request["fileChanges"]["additions"]],
                         ["link-inputs.lock.json"])

    def test_signed_commit_tolerates_stale_pr_projection_of_leased_head(self):
        responses = [
            {"data": {"createCommitOnBranch": {"commit": {"oid": "d" * 40}}}},
            {"commit": {"verification": {"verified": True}},
             "files": [{"filename": "link-inputs.lock.json"}]},
            {"head": {"sha": "b" * 40}},
            {"head": {"sha": "d" * 40}},
        ]
        with (patch.object(p, "api", side_effect=responses),
              patch.object(p.time, "sleep") as sleep):
            result = p.signed_lock_commit("7", "feature", "b" * 40,
                                          "link-inputs.lock.json", b"{}\n",
                                          "deps-link-inputs-sha256-x")
        self.assertEqual(result, "d" * 40)
        sleep.assert_called_once_with(2)

    def test_signed_commit_rejects_an_unexpected_pr_head(self):
        responses = [
            {"data": {"createCommitOnBranch": {"commit": {"oid": "d" * 40}}}},
            {"commit": {"verification": {"verified": True}},
             "files": [{"filename": "link-inputs.lock.json"}]},
            {"head": {"sha": "e" * 40}},
        ]
        with (patch.object(p, "api", side_effect=responses),
              patch.object(p.time, "sleep") as sleep):
            with self.assertRaisesRegex(ValueError, "did not advance"):
                p.signed_lock_commit("7", "feature", "b" * 40,
                                     "link-inputs.lock.json", b"{}\n",
                                     "deps-link-inputs-sha256-x")
        sleep.assert_not_called()

    def test_lock_paths_cannot_escape_or_select_arbitrary_files(self):
        for value in ("../lock.json", "/lock.json", "README.md", "deep/path/to/too/many.lock.json"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                p.checked_lock_path(value)
        self.assertEqual(p.checked_lock_path("link-inputs.lock.json"), "link-inputs.lock.json")

    def test_publication_requires_repository_immutability(self):
        manifest = self.candidate()
        release = {"target_commitish": "b" * 40, "assets": [
            {"name": "build-input-release.json", "size": (self.root / "build-input-release.json").stat().st_size},
            {"name": "link-inputs.lock.json", "size": 3},
            {"name": "link-inputs-x64glibc.tar", "size": 7},
        ], "draft": False, "immutable": False}
        with (patch.object(p, "release_by_tag", side_effect=[release, release]),
              patch.object(p, "verify_release_download")):
            with self.assertRaisesRegex(ValueError, "not immutable"):
                p.publish(self.root, manifest, "d" * 64, "link-inputs-sha256-" + "d" * 64,
                          "link-inputs.lock.json", b"{}\n", "https://example.invalid/run")

    def test_release_recovery_redownloads_and_hashes_every_asset(self):
        files = []
        for name, data in (("one", b"same-size-a"), ("two", b"second")):
            path = self.root / name
            path.write_bytes(data)
            files.append(path)
        downloaded = self.root / "downloaded"
        downloaded.mkdir()

        def fake_run(command, check):
            destination = Path(command[command.index("--dir") + 1])
            for path in files:
                (destination / path.name).write_bytes(path.read_bytes())
            (destination / "one").write_bytes(b"same-size-b")

        with patch.object(p.subprocess, "run", side_effect=fake_run):
            with self.assertRaisesRegex(ValueError, "one"):
                p.verify_release_download("tag", files)


if __name__ == "__main__":
    unittest.main()
