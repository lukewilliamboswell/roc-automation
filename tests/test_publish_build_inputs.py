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
        release = {"id": 42, "tag_name": "link-inputs-sha256-" + "d" * 64, "target_commitish": "b" * 40, "assets": [
            {"name": "build-input-release.json", "size": (self.root / "build-input-release.json").stat().st_size},
            {"name": "link-inputs.lock.json", "size": 3},
            {"name": "link-inputs-x64glibc.tar", "size": 7},
        ], "draft": False, "immutable": False}
        with (patch.object(p, "release_by_tag", return_value=release),
              patch.object(p, "api", return_value=release),
              patch.object(p, "verify_release_download")):
            with self.assertRaisesRegex(ValueError, "not immutable"):
                p.publish(self.root, manifest, "d" * 64, "link-inputs-sha256-" + "d" * 64,
                          "link-inputs.lock.json", b"{}\n", "https://example.invalid/run")

    def test_release_recovery_redownloads_and_hashes_every_asset(self):
        files = []
        records = []
        for i, (name, data) in enumerate((("one", b"same-size-a"), ("two", b"second"))):
            path = self.root / name
            path.write_bytes(data)
            files.append(path)
            records.append({"name": name, "id": i + 1})

        def fake_run(command, *, stdout):
            identifier = int(command[2].rsplit("/", 1)[-1])
            stdout.write(b"same-size-b" if identifier == 1 else b"second")

        with patch.object(p, "run", side_effect=fake_run):
            with self.assertRaisesRegex(ValueError, "one"):
                p.verify_release_download({"assets": records}, files)

    def test_existing_run_must_match_current_pr_and_successful_dispatch(self):
        run = {"event": "workflow_dispatch", "head_sha": "b" * 40,
               "head_branch": "feature", "path": ".github/workflows/link-inputs.yml",
               "head_repository": {"full_name": "owner/project"},
               "status": "completed", "conclusion": "success", "html_url": "run-url"}
        with patch.object(p, "api", return_value=run):
            self.assertEqual(p.completed_run("123", "link-inputs.yml", "feature", "b" * 40),
                             ("123", "run-url"))
        for change in ({"event": "pull_request"}, {"head_sha": "c" * 40},
                       {"head_branch": "other"}, {"path": ".github/workflows/other.yml"},
                       {"head_repository": {"full_name": "fork/project"}},
                       {"status": "in_progress"}, {"conclusion": "failure"},
                       {"conclusion": "cancelled"}):
            with self.subTest(change=change), patch.object(p, "api", return_value={**run, **change}):
                with self.assertRaises(ValueError):
                    p.completed_run("123", "link-inputs.yml", "feature", "b" * 40)
        with patch.object(p, "api") as api:
            with self.assertRaises(ValueError):
                p.completed_run("../other", "link-inputs.yml", "feature", "b" * 40)
            api.assert_not_called()

    def test_release_recovery_finds_a_draft_beyond_the_first_page(self):
        wanted = {"id": 42, "tag_name": "wanted", "draft": True}
        with patch.object(p, "api", side_effect=[[{"tag_name": str(i)} for i in range(100)], [wanted]]) as api:
            self.assertEqual(p.release_by_tag("wanted"), wanted)
            self.assertTrue(api.call_args.args[0].endswith("page=2"))

    def test_new_draft_uses_returned_id_without_rediscovering_through_listing(self):
        manifest = self.candidate()
        tag = "link-inputs-sha256-" + "d" * 64
        draft = {"id": 42, "tag_name": tag, "target_commitish": "b" * 40, "draft": True}
        uploads = []
        def upload(command):
            self.assertIn("/releases/42/assets?name=", command[4])
            path = Path(command[-1])
            record = {"id": len(uploads) + 1, "name": path.name, "size": path.stat().st_size}
            uploads.append(record)
            return json.dumps(record)
        def api(endpoint, data=None, method=None):
            if endpoint.endswith("/releases"):
                self.assertTrue(data["draft"])
                return dict(draft)
            self.assertTrue(endpoint.endswith("/releases/42"))
            if method == "PATCH":
                draft["draft"] = False
            return {**draft, "assets": list(uploads), "immutable": not draft["draft"]}
        with (patch.object(p, "release_by_tag", return_value=None) as lookup,
              patch.object(p, "api", side_effect=api), patch.object(p, "run", side_effect=upload),
              patch.object(p, "verify_release_download") as verify):
            p.publish(self.root, manifest, "d" * 64, tag, "link-inputs.lock.json", b"{}\n", "run-url")
            lookup.assert_called_once_with(tag)
            self.assertEqual(verify.call_args.args[0]["id"], 42)
            self.assertEqual(len(uploads), 3)

    def test_recovery_never_publishes_a_draft_with_mismatched_bytes(self):
        manifest = self.candidate()
        tag = "link-inputs-sha256-" + "d" * 64
        release = {"id": 42, "tag_name": tag, "target_commitish": "b" * 40, "draft": True,
                   "assets": [{"id": i, "name": name, "size": size} for i, (name, size) in enumerate([
                       ("build-input-release.json", (self.root / "build-input-release.json").stat().st_size),
                       ("link-inputs.lock.json", 3), ("link-inputs-x64glibc.tar", 7)])]}
        with (patch.object(p, "release_by_tag", return_value=release),
              patch.object(p, "verify_release_download", side_effect=ValueError("digest mismatch")),
              patch.object(p, "api") as api):
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                p.publish(self.root, manifest, "d" * 64, tag, "link-inputs.lock.json", b"{}\n", "run-url")
            api.assert_not_called()

    def test_resume_reverifies_candidate_and_attestations_without_dispatch(self):
        env = {"INPUT_PULL_REQUEST": "31", "INPUT_PRODUCER_WORKFLOW": "link-inputs.yml",
               "INPUT_CANDIDATE_ARTIFACT": "candidate", "INPUT_LOCK_PATH": "link-inputs.lock.json",
               "INPUT_RELEASE_PREFIX": "link-inputs", "INPUT_PRODUCER_RUN": "123"}
        manifest = self.candidate()
        encoded = (self.root / "build-input-release.json").read_bytes()
        with (patch.dict(os.environ, env), patch.object(p, "require_default_dispatch"),
              patch.object(p, "pr_source", return_value=("feature", "b" * 40)),
              patch.object(p, "completed_run", return_value=("123", "run-url")) as selected,
              patch.object(p, "dispatch_and_wait") as dispatch,
              patch.object(p, "download_candidate", return_value=self.root),
              patch.object(p, "validate_candidate", wraps=p.validate_candidate) as validate,
              patch.object(p, "verify_attestations") as attest,
              patch.object(p, "publish") as publish, patch.object(p, "signed_lock_commit", return_value="c" * 40)):
            p.main()
            selected.assert_called_once_with("123", "link-inputs.yml", "feature", "b" * 40)
            dispatch.assert_not_called()
            validate.assert_called_once()
            attest.assert_called_once_with(self.root, manifest)
            self.assertEqual(publish.call_args.args[2], hashlib.sha256(encoded).hexdigest())


if __name__ == "__main__":
    unittest.main()
