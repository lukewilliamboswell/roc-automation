"""Exercise the native collector against an inert GitHub CLI, never the network."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

BINARY = os.environ.get("ROC_LIFECYCLE_GITHUB_BINARY")
PLANNER = os.environ.get("ROC_RELEASE_PLAN_BINARY")
ROOT = Path(__file__).resolve().parents[1]

FAKE_GH = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
assert args[:3] == ["api", "--method", "GET"], args
path = args[3]
with open(os.environ["GH_LOG"], "a") as log: log.write(json.dumps(args)+"\\n")
if path == os.environ.get("FAIL_PATH"): sys.exit(1)
if "/contents/release-topology.json?ref=" in path: sys.stdout.write(os.environ["REVIEWED_TOPOLOGY"])
elif "/git/matching-refs/" in path: print(os.environ.get("TAG_REFS", "[]"))
elif "/contents/docs/releases/" in path: print(os.environ.get("NOTES_KIND", "regular"))
elif path.endswith("/artifacts?per_page=100"): print(os.environ.get("ARTIFACTS", "[]"))
elif "/git/commits/" in path: print(json.dumps(["c"*40, "b"*40]))
elif path.endswith("/pulls/1"): print(os.environ.get("LIVE_HEAD", "b"*40) if args[-1] == ".head.sha" else "c"*40)
elif path.endswith("/releases/latest"): print("nightly-2026-10-23-18478ff")
elif path.endswith("/files?per_page=100"):
    assert "--paginate" in args and "--slurp" in args
    print(os.environ.get("CHANGES", '[{"status":"modified","old_path":"package/main.roc","new_path":"package/main.roc"}]'))
elif path.endswith("/releases?per_page=100"):
    assert "--paginate" in args and "--slurp" in args
    print(os.environ.get("RELEASE_TAGS", "[]") if args[-1] == "flatten | map(.tag_name)" else os.environ.get("PRIOR_RELEASES", "[]"))
elif "/actions/runs/" in path:
    print("1" if "pull_requests" in args[-1] else (os.environ.get("RUN_HEAD", "b"*40) if args[-1] == ".head_sha" else os.environ.get("ACTUAL_EVENT", "pull_request")))
else: print("main")
'''


@unittest.skipUnless(BINARY and PLANNER, "set native lifecycle collector and planner binaries")
class LifecycleGitHubTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launcher = None
        if os.name == "nt":
            cls.launcher_temp = tempfile.TemporaryDirectory()
            cls.addClassCleanup(cls.launcher_temp.cleanup)
            cls.launcher = Path(cls.launcher_temp.name) / "launcher.exe"
            subprocess.run(["clang", str(ROOT / "tests/fixtures/compatibility_launcher.c"),
                            "-o", str(cls.launcher)], check=True, capture_output=True, text=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        gh = self.bin / ("gh.py" if self.launcher else "gh")
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        if self.launcher:
            shutil.copy(self.launcher, self.bin / "gh.exe")
        self.out = self.root / "outputs"
        self.out.write_text("")
        self.log = self.root / "requests"
        self.topology = (ROOT / "tests/fixtures/lifecycle/topology-package.json").read_text()
        self.env = {
            **os.environ, "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""), "FAKE_PYTHON": sys.executable,
            "GH_LOG": str(self.log), "GITHUB_OUTPUT": str(self.out),
            "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "owner/sample", "GITHUB_RUN_ID": "100", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_EVENT_NAME": "pull_request", "GITHUB_REF": "refs/pull/1/merge", "GITHUB_SHA": "a" * 40,
            "RO_DIRECTORY": str(self.root / "evidence"), "RO_PLAN_BINARY": str(PLANNER),
            "REVIEWED_TOPOLOGY": self.topology, "INPUT_FULL_DRY_RUN": "false", "INPUT_VERSION": "",
        }

    def run_collector(self, **extra):
        return subprocess.run([BINARY, "plan"], env={**self.env, **extra}, capture_output=True, text=True, timeout=15)

    def plan(self):
        return json.loads((self.root / "evidence/plan.json").read_text())

    def test_source_only_selection_binds_exact_topology_and_resolves_compiler_once(self):
        ran = self.run_collector()
        self.assertEqual(ran.returncode, 0, ran.stderr)
        plan = self.plan()
        self.assertFalse(plan["full_rehearsal"])
        self.assertEqual(plan["context"]["topology_sha256"], hashlib.sha256(self.topology.encode()).hexdigest())
        self.assertEqual(plan["context"]["version"], "")
        self.assertIn("version=0.0.1-dry-run", self.out.read_text())
        requests = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(sum("releases/latest" in args[3] for args in requests), 1)
        self.assertTrue(all(args[:3] == ["api", "--method", "GET"] for args in requests))
        self.assertIn("source-matrix=", self.out.read_text())

    def test_unreadable_change_inventory_requires_full_rehearsal(self):
        ran = self.run_collector(FAIL_PATH="repos/owner/sample/pulls/1/files?per_page=100")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertTrue(self.plan()["full_rehearsal"])

    def test_pages_selection_must_name_a_declared_manual_before_any_release(self):
        ran = self.run_collector(INPUT_PAGES_DOCUMENTATION="unknown-manual")
        self.assertNotEqual(ran.returncode, 0)
        self.assertIn("must be declared", ran.stderr)

    def test_changed_live_pr_cannot_select_from_a_later_diff(self):
        ran = self.run_collector(LIVE_HEAD="d" * 40)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertTrue(self.plan()["full_rehearsal"])

    def test_unknown_change_format_requires_full_rehearsal(self):
        ran = self.run_collector(CHANGES="not JSON")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertTrue(self.plan()["full_rehearsal"])

    def test_api_failure_cannot_claim_first_release(self):
        ran = self.run_collector(FAIL_PATH="repos/owner/sample/releases?per_page=100")
        self.assertNotEqual(ran.returncode, 0)
        self.assertFalse((self.root / "evidence/prior.json").exists())
        self.assertEqual(self.out.read_text(), "")

    def test_actual_event_mismatch_is_rejected(self):
        ran = self.run_collector(ACTUAL_EVENT="workflow_dispatch")
        self.assertNotEqual(ran.returncode, 0)
        self.assertFalse((self.root / "evidence").exists())

    def test_dispatch_off_default_branch_is_rejected(self):
        ran = self.run_collector(ACTUAL_EVENT="workflow_dispatch", GITHUB_EVENT_NAME="workflow_dispatch",
                                 GITHUB_REF="refs/heads/feature", INPUT_VERSION="1.0.0")
        self.assertNotEqual(ran.returncode, 0)
        self.assertEqual(self.out.read_text(), "")

    def test_dispatch_preflight_rejects_existing_tag_draft_and_unsafe_notes(self):
        base = dict(ACTUAL_EVENT="workflow_dispatch", RUN_HEAD="a"*40, GITHUB_EVENT_NAME="workflow_dispatch",
                    GITHUB_REF="refs/heads/main", INPUT_VERSION="1.0.0")
        for changes in (dict(TAG_REFS='["refs/tags/1.0.0"]'), dict(RELEASE_TAGS='["1.0.0"]'), dict(NOTES_KIND="invalid")):
            with self.subTest(changes=changes):
                ran = self.run_collector(**base, **changes)
                self.assertNotEqual(ran.returncode, 0)
                self.assertEqual(self.out.read_text(), "")
        ran = self.run_collector(**base)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertTrue(self.plan()["full_rehearsal"])

    def test_prior_bundle_requires_unique_immutable_asset(self):
        asset = {"name": "sample-abc.tar.zst", "url": "https://github.com/owner/sample/releases/download/1.0.0/sample-abc.tar.zst"}
        prior = [{"tag": "1.0.0", "assets": [asset]}]
        ran = self.run_collector(PRIOR_RELEASES=json.dumps(prior))
        self.assertEqual(ran.returncode, 0, ran.stderr)
        previous = json.loads((self.root / "evidence/prior.json").read_text())
        self.assertFalse(previous["first_release"])
        self.assertEqual(previous["bundles"], [{"id": "package", "url": asset["url"]}])

    def test_upstream_transport_uses_unique_current_run_artifact_ids(self):
        self.assertEqual(self.run_collector().returncode, 0)
        artifact = dict(id=12, name="lifecycle-source-ubuntu-24.04-1", digest="sha256:"+"f"*64, expired=False, run_id=100)
        env = {**self.env, "ARTIFACTS": json.dumps([artifact])}
        destination = self.root / "transport.json"
        command = [BINARY, "resolve", str(self.root / "evidence/plan.json"), "source", str(destination)]
        ran = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertEqual(json.loads(destination.read_text())["artifacts"], [{"id":12, "name":artifact["name"], "digest":artifact["digest"]}])
        self.assertIn("artifact-ids=12", self.out.read_text())
        for changes in (dict(expired=True), dict(run_id=101), dict(digest="sha256:"+"0"*63)):
            with self.subTest(changes=changes):
                ran = subprocess.run(command, env={**env, "ARTIFACTS": json.dumps([{**artifact, **changes}])}, capture_output=True, text=True, timeout=15)
                self.assertNotEqual(ran.returncode, 0)
        ran = subprocess.run(command, env={**env, "ARTIFACTS": json.dumps([artifact, artifact])}, capture_output=True, text=True, timeout=15)
        self.assertNotEqual(ran.returncode, 0)

    def test_wrong_prior_url_is_rejected(self):
        prior = [{"tag": "1.0.0", "assets": [{"name": "sample-abc.tar.zst", "url": "https://example.org/swapped.tar.zst"}]}]
        ran = self.run_collector(PRIOR_RELEASES=json.dumps(prior))
        self.assertNotEqual(ran.returncode, 0)
        self.assertEqual(self.out.read_text(), "")


if __name__ == "__main__":
    unittest.main()
