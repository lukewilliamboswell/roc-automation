"""Exercise inert Pages restoration; admission metadata is explicitly modeled.

These tests verify the receiver's bindings and byte restoration, not live
GitHub admission, publication or deployment permissions.
"""
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

ROOT = Path(__file__).resolve().parents[1]
BINARY = os.environ.get("ROC_LIFECYCLE_PAGES_BINARY")
REAL_ZIP = os.environ.get("ROC_REAL_PAGES_ZIP")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipUnless(BINARY, "set ROC_LIFECYCLE_PAGES_BINARY to test the native Pages utility")
class LifecyclePagesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.public = self.root / "public"
        self.public.mkdir()
        self.output = self.root / "restored"
        self.github_output = self.root / "outputs"
        self.github_output.write_text("prior-output=preserved\n")
        self.topology = json.loads((ROOT / "tests/fixtures/lifecycle/topology-package.json").read_text())
        self.topology["name"] = "sample"
        self.topology["documentation"] = [dict(id="guide", directory="docs/guide", pdf=True), dict(id="reference", directory="docs/reference", pdf=False)]
        self.topology_path = self.root / "topology.json"
        self.topology_path.write_text(json.dumps(self.topology))
        self.version = "1.2.3"
        self.expected_root = "sample-guide-docs-1.2.3"
        self.archive = self.public / (self.expected_root + ".zip")
        self.zip({self.expected_root + "/index.html": b"<h1>actual archived documentation</h1>",
                  self.expected_root + "/run.sh": b"#!/bin/sh\ntouch should-never-run\n"})
        self.report = dict(schema_version=1, profile="roc-automation-release-evidence-v1", repository="owner/sample",
                           source_sha="a" * 40, run_id="10", producing_attempt="1", compiler="nightly-2026-10-23-18478ff",
                           version=self.version, topology_sha256=digest(self.topology_path),
                           workflow_ref="lukewilliamboswell/roc-automation/.github/workflows/release-lifecycle.yml@" + "c" * 40,
                           workflow_sha="c" * 40, policy_version="roc-automation-release-policy-1", schema_profile="release-evidence-1",
                           validator_version="roc-automation-release-admit-v1", verdict="admitted", publish=True,
                           assets=[self.file()], intermediates=[], notes_sha256="f" * 64, artifacts=[])
        self.env = {**os.environ, "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REPOSITORY": "owner/sample",
                    "GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "10", "GITHUB_RUN_ATTEMPT": "2",
                    "GITHUB_OUTPUT": str(self.github_output), "RA_PYTHON": sys.executable,
                    "RA_ARCHIVE_HELPER": str(ROOT / "actions/release-compatibility/archive.py")}

    def zip(self, entries, kind=stat.S_IFREG):
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in entries.items():
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (kind | 0o755) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, content)

    def file(self, subject="guide"):
        return dict(path=self.archive.name, role="documentation", subject=subject, target="source",
                    size=self.archive.stat().st_size, sha256=digest(self.archive))

    def invoke(self, requested="guide", env=None):
        admission = self.root / "admission.json"
        admission.write_text(json.dumps(self.report))
        return subprocess.run([BINARY, "restore", str(admission), str(self.topology_path), requested,
                               str(self.public), str(self.output)], env=env or self.env,
                              capture_output=True, text=True, timeout=20)

    def failed_without_handoff(self, result):
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.github_output.read_text(), "prior-output=preserved\n")

    def test_exact_selected_site_bytes_restored_without_execution_or_archive_mutation(self):
        before = self.archive.read_bytes()
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        site = self.output / self.expected_root
        self.assertEqual((site / "index.html").read_bytes(), b"<h1>actual archived documentation</h1>")
        self.assertEqual((site / "run.sh").read_bytes(), b"#!/bin/sh\ntouch should-never-run\n")
        self.assertFalse((self.root / "should-never-run").exists())
        self.assertEqual(self.archive.read_bytes(), before)
        fields = dict(line.split("=", 1) for line in self.github_output.read_text().splitlines())
        self.assertEqual(fields["site-directory"], str(site))
        self.assertEqual(fields["archive"], self.archive.name)
        self.assertEqual(fields["sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(fields["documentation-id"], "guide")

    def test_multiple_manuals_require_the_explicit_selected_id(self):
        reference = self.public / "sample-reference-docs-1.2.3.zip"
        with zipfile.ZipFile(reference, "w") as archive:
            info = zipfile.ZipInfo("sample-reference-docs-1.2.3/index.html")
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, b"reference")
        self.report["assets"].append(dict(path=reference.name, role="documentation", subject="reference", target="source", size=reference.stat().st_size, sha256=digest(reference)))
        result = self.invoke("reference")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.output / "sample-reference-docs-1.2.3/index.html").read_bytes(), b"reference")
        self.assertFalse((self.output / self.expected_root).exists())

    def test_unknown_missing_wrong_role_and_ambiguous_docs_never_restore(self):
        for requested in ["", "missing", "../guide"]:
            with self.subTest(requested=requested):
                self.failed_without_handoff(self.invoke(requested))
                self.assertFalse(self.output.exists())
        for assets in [[], [{**self.file(), "role": "bundle"}], [self.file(), self.file()]]:
            with self.subTest(assets=assets):
                self.report["assets"] = assets
                self.failed_without_handoff(self.invoke())
                self.assertFalse(self.output.exists())

    def test_changed_zip_size_hash_and_missing_file_fail_before_creating_output(self):
        original = self.file()
        for extra in [dict(size=original["size"] + 1), dict(sha256="0" * 64)]:
            with self.subTest(extra=extra):
                self.report["assets"] = [{**original, **extra}]
                self.failed_without_handoff(self.invoke())
                self.assertFalse(self.output.exists())
        self.report["assets"] = [original]
        self.archive.unlink()
        self.failed_without_handoff(self.invoke())
        self.assertFalse(self.output.exists())

    def test_wrong_root_extra_root_missing_or_empty_index_cleans_only_new_output(self):
        for entries in [{"other/index.html": b"wrong"},
                        {self.expected_root + "/index.html": b"valid", "other/file": b"undeclared root"},
                        {self.expected_root + "/missing.html": b"missing index"},
                        {self.expected_root + "/index.html": b""}]:
            with self.subTest(entries=entries):
                self.zip(entries)
                self.report["assets"] = [self.file()]
                self.failed_without_handoff(self.invoke())
                self.assertFalse(self.output.exists())

    def test_unsafe_zip_paths_and_links_cannot_escape_or_replace_old_site(self):
        for entries, kind in [({"../escape": b"bad"}, stat.S_IFREG),
                              ({self.expected_root + "/index.html": b"../escape"}, stat.S_IFLNK)]:
            with self.subTest(entries=entries):
                self.zip(entries, kind)
                self.report["assets"] = [self.file()]
                self.failed_without_handoff(self.invoke())
                self.assertFalse((self.root / "escape").exists())
                self.assertFalse(self.output.exists())
        self.output.mkdir()
        old = self.output / "index.html"
        old.write_bytes(b"previous publication")
        self.zip({self.expected_root + "/index.html": b"new"})
        self.report["assets"] = [self.file()]
        self.failed_without_handoff(self.invoke())
        self.assertEqual(old.read_bytes(), b"previous publication")
        self.assertEqual(list(self.output.iterdir()), [old])

    def test_wrong_run_source_repository_topology_or_pr_report_cannot_restore(self):
        original = dict(self.report)
        for extra in [dict(run_id="11"), dict(repository="other/sample"), dict(source_sha="0" * 40),
                      dict(topology_sha256="0" * 64), dict(publish=False), dict(validator_version="other"),
                      dict(verdict="passed"), dict(version="0.0.1-dry-run")]:
            with self.subTest(extra=extra):
                self.report = {**original, **extra}
                self.failed_without_handoff(self.invoke())
                self.assertFalse(self.output.exists())
        self.report = original
        self.failed_without_handoff(self.invoke(env={**self.env, "GITHUB_EVENT_NAME": "pull_request"}))
        self.assertFalse(self.output.exists())

    def test_strict_report_and_topology_unknown_fields_are_rejected(self):
        self.report["unknown"] = "must reject"
        self.failed_without_handoff(self.invoke())
        del self.report["unknown"]
        self.topology["unknown"] = "must reject"
        self.topology_path.write_text(json.dumps(self.topology))
        self.report["topology_sha256"] = digest(self.topology_path)
        self.failed_without_handoff(self.invoke())

    @unittest.skipUnless(REAL_ZIP, "set ROC_REAL_PAGES_ZIP to a real dispatch-version prepared fixture documentation ZIP")
    def test_actual_prepared_documentation_zip_is_restored_byte_for_byte(self):
        self.topology = json.loads((ROOT / "tests/fixtures/lifecycle/package/release-topology.json").read_text())
        self.topology_path.write_text(json.dumps(self.topology))
        real = Path(REAL_ZIP)
        self.archive = self.public / real.name
        self.archive.write_bytes(real.read_bytes())
        prefix = self.topology["name"] + "-docs-"
        version = real.name.removeprefix(prefix).removesuffix(".zip")
        self.report.update(version=version, topology_sha256=digest(self.topology_path), assets=[self.file(self.topology["documentation"][0]["id"])])
        before = self.archive.read_bytes()
        result = self.invoke(self.topology["documentation"][0]["id"])
        self.assertEqual(result.returncode, 0, result.stderr)
        site = self.output / real.name.removesuffix(".zip")
        with zipfile.ZipFile(real) as archive:
            expected = {entry.filename: archive.read(entry) for entry in archive.infolist() if not entry.is_dir()}
        actual = {file.relative_to(self.output).as_posix(): file.read_bytes() for file in site.rglob("*") if file.is_file()}
        self.assertEqual(actual, expected)
        self.assertEqual(self.archive.read_bytes(), before)
