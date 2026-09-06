import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import os
import tempfile

spec = importlib.util.spec_from_file_location("release_policy", Path(__file__).resolve().parents[1] / "actions/check-release/check_release.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class ReleasePolicyTests(unittest.TestCase):
    def check(self, version="0.1.1", ref="refs/heads/release/roc-0.1.x", **kwargs):
        return policy.validate(version, ref, "main", "a" * 40, kwargs.pop("checkout_sha", "a" * 40), kwargs.pop("compiler_pin", "0.1.2"), **kwargs)

    def test_patch_and_prerelease(self):
        for version in ("0.1.0", "0.1.1", "0.1.12-rc.1", "0.1.0-rc1"):
            self.assertEqual(self.check(version)["maintenance-branch"], "release/roc-0.1.x")

    def test_main_requires_explicit_opt_in(self):
        with self.assertRaises(ValueError):
            self.check(ref="refs/heads/main")
        self.assertEqual(self.check(ref="refs/heads/main", allow_default=True)["sha"], "a" * 40)

    def test_wrong_line_tags_and_pr_refs_rejected(self):
        for ref in ("refs/heads/release/roc-0.2.x", "refs/heads/release/0.1.x", "refs/tags/0.1.1", "refs/pull/1/merge", "refs/heads/feature"):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                self.check(ref=ref, allow_default=True)

    def test_invalid_versions_rejected(self):
        for version in ("v0.1.1", "0.01.1", "0.1.1-01", "0.1", "0.1.1+build", "0.1.1\nsha=bad", "0.1.1-", "0.1.1-rc..1"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.check(version)

    def test_untested_checkout_rejected(self):
        with self.assertRaisesRegex(ValueError, "exact workflow event commit"):
            self.check(checkout_sha="b" * 40)

    def test_entrypoint_writes_only_validated_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            env = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/release/roc-0.1.x",
                   "GITHUB_SHA": "a" * 40, "RELEASE_VERSION": "0.1.2", "GITHUB_OUTPUT": str(output)}
            with patch.dict(os.environ, env, clear=True), patch.object(policy.subprocess, "check_output", return_value="a" * 40 + "\n") as git, patch.object(policy.Path, "read_text", return_value="v0.1.2\n"):
                policy.main()
                git.assert_called_once_with(["git", "rev-parse", "HEAD"], text=True)
            self.assertEqual(output.read_text(), "version=0.1.2\nsha=" + "a" * 40 + "\ncompiler-pin=v0.1.2\nmaintenance-branch=release/roc-0.1.x\n")

    def test_entrypoint_rejects_other_events_before_git(self):
        for event in ("pull_request", "push", "schedule"):
            with patch.dict(os.environ, {"GITHUB_EVENT_NAME": event}, clear=True), patch.object(policy.subprocess, "check_output") as git:
                with self.assertRaisesRegex(ValueError, "explicit workflow dispatch"):
                    policy.main()
                git.assert_not_called()

    def test_package_versions_are_independent_of_compiler_branch(self):
        for version in ("0.9.0", "1.0.0", "8.3.7-rc1"):
            self.assertEqual(self.check(version)["version"], version)

    def test_compiler_branch_rejects_nightly_and_other_compiler_line(self):
        for compiler in ("nightly-2026-09-04-c125b82", "0.2.0", "0.1.0-rc1", "latest", "0.1.2\nsha=bad"):
            with self.subTest(compiler=compiler), self.assertRaises(ValueError):
                self.check(compiler_pin=compiler)

    def test_main_can_track_pinned_nightly(self):
        result = self.check(ref="refs/heads/main", compiler_pin="nightly-2026-09-04-c125b82", allow_default=True)
        self.assertEqual(result["maintenance-branch"], "")
