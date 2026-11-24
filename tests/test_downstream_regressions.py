"""Exercise the local corpus helper without GitHub or compiler execution."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("downstream_support", ROOT / "tools/downstream-regressions/support.py")
support = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(support)


class LocalCorpusTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="downstream tests ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.config = self.root / "sweep.json"
        self.config.write_text(json.dumps({"nightlies": ["bin/roc", "missing"]}))
        self.catalog = self.root / "index.json"
        self.catalog.write_text(json.dumps({"schema_version": 1, "repositories": [
            {"repository": "owner/package", "examples": [{"path": "examples/main.roc"}]}]}))
        self.archive = self.root / "fixture.zip"
        self.make_archive()
        self.asset = {"id": 123, "name": "package-examples-1.0.0.zip", "size": self.archive.stat().st_size,
                      "digest": "sha256:" + support.file_hash(self.archive), "download_count": 10}
        self.release = {"id": 12, "tag_name": "1.0.0", "published_at": "2026-11-01T12:00:00Z",
                        "assets": [self.asset]}
        outer = self

        class Client:
            downloads = 0

            def metadata(self, endpoint):
                return [outer.release]

            def download(self, repository, asset, destination):
                self.downloads += 1
                shutil.copyfile(outer.archive, destination)

        self.client = Client()

    def make_archive(self, extra=None, version="1.0.0"):
        suite = {"schema_version": 1, "release": version,
                 "dependencies": [f"https://github.com/owner/package/releases/download/{version}/hash.tar.zst"],
                 "apps": [{"path": "examples/main.roc", "cases": [{"name": "hello", "args": ["argument"],
                           "stdin": "input", "stdout": "hello\n", "stderr": "", "exit_code": 0}]}]}
        with zipfile.ZipFile(self.archive, "w") as archive:
            for name, content in {"spec.json": json.dumps(suite), "examples/main.roc": "app [] {}", **(extra or {})}.items():
                entry = zipfile.ZipInfo(f"package-examples-{version}/" + name)
                entry.create_system = 3
                entry.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(entry, content)

    def prepare(self):
        return support.prepare(self.config, self.catalog, self.client)

    def test_config_paths_resolve_from_file_and_do_not_require_working_directory(self):
        config = support.load_config(self.config)
        self.assertEqual([compiler["path"] for compiler in config["nightlies"]],
                         [str(self.root / "bin/roc"), str(self.root / "missing")])
        self.assertEqual(config["cache"], str(self.root / ".downstream-cache"))
        self.assertEqual(config["output"], str(self.root / "downstream-report.html"))
        self.config.write_text(json.dumps({"nightlies": ["~/roc"], "output": "reports/results.html"}))
        self.assertEqual(support.load_config(self.config)["nightlies"][0]["path"],
                         str((Path.home() / "roc").resolve()))

    def test_config_rejects_discovery_and_missing_or_invalid_compiler_lists(self):
        for config in ({}, {"nightlies": []}, {"nightlies": "roc"}, {"nightlies": [1]},
                       {"nightlies": [""]}, {"nightlies": ["roc"], "sweep": True}):
            with self.subTest(config=config):
                self.config.write_text(json.dumps(config))
                with self.assertRaises(ValueError):
                    support.load_config(self.config)

    def test_config_validates_source_commit_and_utc_date(self):
        entry = {"path": "bin/roc", "source_commit": "a" * 40, "source_date": "2026-11-23"}
        self.config.write_text(json.dumps({"nightlies": ["nightly/roc", entry]}))
        compiler = support.load_config(self.config)["nightlies"][1]
        self.assertEqual(compiler, {**entry, "path": str(self.root / "bin/roc")})
        for invalid in ({**entry, "source_commit": "a" * 7}, {**entry, "source_date": "2026-02-29"},
                        {**entry, "source_date": "20261123"}, {**entry, "extra": True},
                        {"path": "bin/roc", "source_commit": entry["source_commit"]}):
            with self.subTest(entry=invalid), self.assertRaises(ValueError):
                self.config.write_text(json.dumps({"nightlies": [invalid]}))
                support.load_config(self.config)
        with self.assertRaisesRegex(ValueError, "configure each compiler path once"):
            self.config.write_text(json.dumps({"nightlies": ["bin/roc", entry]}))
            support.load_config(self.config)

    def test_latest_release_includes_prereleases_and_uses_publication_time(self):
        for tag, flag in [("6.0.0-rc.5", True), ("v6.0.0-beta.1", False), ("2.0.0", True)]:
            with self.subTest(tag=tag, prerelease=flag):
                newest = {**self.release, "tag_name": tag, "prerelease": flag,
                          "published_at": "2026-11-19T00:27:00Z"}
                higher_version = {**self.release, "tag_name": "100.0.0",
                                  "published_at": "2026-11-18T23:00:00Z"}
                with patch.object(self.client, "metadata", return_value=[higher_version, newest, self.release]):
                    self.assertEqual(support.latest_release(self.client, "owner/package")["tag_name"], tag)
        newest_stable = {**self.release, "tag_name": "6.0.0", "published_at": "2026-11-20T00:00:00Z"}
        with patch.object(self.client, "metadata", return_value=[newest, newest_stable]):
            self.assertEqual(support.latest_release(self.client, "owner/package")["tag_name"], "6.0.0")

    def test_compiler_release_candidate_uses_its_exact_tag_and_utc_publication_date(self):
        for tag in ("0.1.0_RC1", "0.2.0_RC12"):
            with self.subTest(tag=tag):
                release = {"tag_name": tag, "published_at": "2026-11-22T00:30:00+02:00"}
                with patch.object(self.client, "metadata", return_value=release) as request:
                    date = support.compiler_release_date(f"Roc compiler version {tag}", self.client)
                self.assertEqual(date, "2026-11-21")
                request.assert_called_once_with(f"repos/roc-lang/roc/releases/tags/{tag}")

    def test_compiler_release_candidate_rejects_wrong_or_unpublished_release(self):
        release = {"tag_name": "0.1.0_RC1", "published_at": "2026-11-21T19:38:08Z"}
        for metadata in ({**release, "tag_name": "0.1.0_RC2"}, {**release, "draft": True},
                         {**release, "published_at": None}, {**release, "published_at": "2026-11-21"}):
            with self.subTest(metadata=metadata), patch.object(self.client, "metadata", return_value=metadata):
                with self.assertRaises(ValueError):
                    support.compiler_release_date("Roc compiler version 0.1.0_RC1", self.client)
        with patch.object(self.client, "metadata") as request:
            self.assertEqual(support.compiler_release_date("Roc compiler version 0.1.0_RC1/other", self.client), "")
        request.assert_not_called()

    def test_latest_release_excludes_drafts_and_auxiliary_tags(self):
        excluded = [{**self.release, "tag_name": tag, "published_at": "2027-01-01T00:00:00Z"}
                    for tag in ["tools-9.0.0", "runtime-9.0.0", "linker-inputs-9.0.0", "nightly-latest"]]
        excluded.append({**self.release, "tag_name": "9.0.0-rc.1", "draft": True,
                         "published_at": "2027-01-01T00:00:00Z"})
        with patch.object(self.client, "metadata", return_value=excluded + [self.release]):
            self.assertEqual(support.latest_release(self.client, "owner/package")["tag_name"], "1.0.0")

    def test_newest_prerelease_without_examples_never_falls_back_to_stable(self):
        newest = {**self.release, "tag_name": "6.0.0-rc.5", "prerelease": True,
                  "published_at": "2026-11-19T00:27:00Z", "assets": []}
        with patch.object(self.client, "metadata", return_value=[self.release, newest]):
            manifest = self.prepare()
        self.assertIn("lacks one", manifest["apps"][0]["skip"])
        self.assertEqual(manifest["apps"][0]["release"], "6.0.0-rc.5")
        self.assertEqual(self.client.downloads, 0)

    def test_prerelease_archive_and_dependencies_are_bound_to_selected_tag(self):
        version = "6.0.0-rc.5"
        self.make_archive(version=version)
        self.asset.update(name=f"package-examples-{version}.zip", size=self.archive.stat().st_size,
                          digest="sha256:" + support.file_hash(self.archive))
        self.release.update(tag_name=version, prerelease=True, published_at="2026-11-19T00:27:00Z")
        manifest = self.prepare()
        self.assertEqual(manifest["apps"][0]["release"], version)
        self.assertEqual(manifest["apps"][0]["earliest_nightly"], "2026-11-18")
        self.assertEqual(manifest["apps"][0]["skip"], "")
        self.assertEqual(len(manifest["apps"][0]["cases"]), 1)

    def test_pagination_can_find_the_most_recently_published_release(self):
        newest = {**self.release, "tag_name": "6.0.0-rc.5", "prerelease": True,
                  "published_at": "2026-11-19T00:27:00Z"}
        with patch.object(self.client, "metadata", side_effect=[[self.release] * 100, [newest]]) as requests:
            self.assertEqual(support.latest_release(self.client, "owner/package")["tag_name"], "6.0.0-rc.5")
        self.assertIn("page=2", requests.call_args.args[0])

    def test_grace_cutoff_handles_calendar_boundaries_and_preserves_publication_date(self):
        for published, cutoff in [("2026-11-19", "2026-11-18"),
                                  ("2026-03-01", "2026-02-28"),
                                  ("2024-03-01", "2024-02-29"),
                                  ("2026-01-01", "2025-12-31")]:
            with self.subTest(published=published):
                self.release["published_at"] = published + "T01:00:00Z"
                manifest = self.prepare()
                self.assertEqual(manifest["apps"][0]["date"], published)
                self.assertEqual(manifest["apps"][0]["earliest_nightly"], cutoff)

    def test_grace_policy_changes_corpus_identity_so_old_skips_cannot_resume(self):
        current = self.prepare()
        with patch.object(support, "earliest_nightly_date", side_effect=lambda date: date):
            old_policy = self.prepare()
        self.assertNotEqual(current["corpus_hash"], old_policy["corpus_hash"])

    def test_compiler_hash_is_bytes_and_missing_compiler_remains_visible(self):
        compiler = self.root / "bin/roc"
        compiler.parent.mkdir()
        compiler.write_bytes(b"compiler bytes")
        manifest = self.prepare()
        self.assertEqual(manifest["nightlies"][0]["sha256"], hashlib.sha256(b"compiler bytes").hexdigest())
        self.assertEqual(manifest["nightlies"][1]["sha256"], "unavailable")
        self.assertEqual(manifest["apps"][0]["cases"][0]["args"], ["argument"])

    def test_verified_archive_cache_is_reused_but_extracted_workspaces_are_fresh(self):
        first = self.prepare()
        source = Path(first["apps"][0]["root"]) / "examples/main.roc"
        source.write_text("modified working source")
        self.asset["download_count"] = 99
        second = self.prepare()
        self.assertEqual(self.client.downloads, 1)
        self.assertNotEqual(first["apps"][0]["root"], second["apps"][0]["root"])
        self.assertEqual((Path(second["apps"][0]["root"]) / "examples/main.roc").read_text(), "app [] {}")
        self.assertEqual(first["corpus_hash"], second["corpus_hash"])

    def test_corrupt_cached_bytes_are_replaced_only_by_verified_download(self):
        manifest = self.prepare()
        archive = next((Path(manifest["cache"]) / "archives").glob("*.zip"))
        archive.write_bytes(b"corrupt")
        self.prepare()
        self.assertEqual(self.client.downloads, 2)
        self.assertEqual(support.file_hash(archive), self.asset["digest"][7:])
        archive.write_bytes(b"corrupt")
        with patch.object(self.client, "download", side_effect=lambda repository, asset, path: Path(path).write_bytes(b"bad")):
            failed = self.prepare()
        self.assertIn("SHA-256 differs", failed["apps"][0]["skip"])
        self.assertEqual(archive.read_bytes(), b"corrupt")
        self.assertEqual(list(archive.parent.glob("download-*")), [])

    def test_archive_links_are_rejected_before_a_runnable_app_is_emitted(self):
        with zipfile.ZipFile(self.archive, "a") as archive:
            entry = zipfile.ZipInfo("package-examples-1.0.0/examples/link")
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(entry, "../../outside")
        self.asset.update(size=self.archive.stat().st_size, digest="sha256:" + support.file_hash(self.archive))
        manifest = self.prepare()
        self.assertIn("links", manifest["apps"][0]["skip"])
        self.assertEqual(manifest["apps"][0]["cases"], [])

    def test_managed_workspaces_can_be_replaced_but_cannot_escape_cache(self):
        source = self.root / "source"
        source.mkdir()
        (source / "data").write_text("original")
        cache = self.root / "cache"
        destination = cache / "work/hash/app"
        support.copy_workspace(source, destination, cache)
        (destination / "data").write_text("mutated")
        support.copy_workspace(source, destination, cache)
        self.assertEqual((destination / "data").read_text(), "original")
        sentinel = self.root / "unrelated"
        sentinel.mkdir()
        (sentinel / "keep").write_text("keep")
        for unsafe in (sentinel, cache / "work", cache / "work/hash"):
            with self.subTest(path=unsafe), self.assertRaises(ValueError):
                support.copy_workspace(source, unsafe, cache)
        link = cache / "work/escape"
        link.symlink_to(sentinel, target_is_directory=True)
        with self.assertRaises(ValueError):
            support.copy_workspace(source, link / "app", cache)
        self.assertEqual((sentinel / "keep").read_text(), "keep")

    def test_atomic_observation_save_preserves_previous_file_on_replace_failure(self):
        path = self.root / "observations.json"
        support.atomic_json(path, [{"key": "previous"}])
        with patch.object(support.os, "replace", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                support.atomic_json(path, [{"key": "new"}])
        self.assertEqual(json.loads(path.read_text()), [{"key": "previous"}])
        self.assertEqual(list(self.root.glob(".observations.json.*")), [])

    def test_report_embeds_escaped_diagnostics_and_excludes_other_corpus_or_host(self):
        manifest = self.prepare()
        path = self.root / "observations.json"
        record = {"key": manifest["corpus_hash"] + "|" + manifest["host"] + "|compiler|hash|app",
                  "compiler_hash": manifest["nightlies"][0]["sha256"],
                  "compiler_path": manifest["nightlies"][0]["path"], "version": "nightly-2026-11-02-a",
                  "app_id": "owner/package:examples/main.roc", "nightly_date": "2026-11-02",
                  "stage": "run", "case_name": "<case>", "status": "exited", "code": 1,
                  "expected_code": 0, "stdout": "<script>alert('bad')</script>", "stderr": "&raw",
                  "expected_stdout": "expected", "expected_stderr": "", "argv": ["roc", "<app>"]}
        path.write_text(json.dumps([record, {**record, "key": "other-host", "stdout": "STALE RECORD"}]))
        support.render(self.config, path)
        output = Path(manifest["output"]).read_text()
        self.assertNotIn("<script>alert", output)
        self.assertIn("&lt;script&gt;alert", output)
        self.assertIn("&amp;raw", output)
        self.assertIn("expected", output)
        self.assertNotIn("STALE RECORD", output)

    def test_report_labels_source_commit_and_excludes_prior_metadata(self):
        entry = {"path": "bin/roc", "source_commit": "a" * 40, "source_date": "2026-11-23"}
        self.config.write_text(json.dumps({"nightlies": [entry]}))
        manifest = self.prepare()
        manifest["apps"][0]["expectations"] = [{
            "nightly": "nightly-2026-11-23-abcdef0", "stage": "check", "case_name": "",
            "code": 1, "note": "Declared only for the published binary",
        }]
        support.atomic_json(Path(manifest["cache"]) / "manifest.json", manifest)
        compiler = manifest["nightlies"][0]
        prefix = f"{manifest['corpus_hash']}|{manifest['host']}|"
        key = f"{compiler['path']}|{compiler['sha256']}|app|v1|check"
        record = {"key": prefix + compiler["source_key"] + key, "compiler_path": compiler["path"],
                  "compiler_hash": compiler["sha256"], "version": "Roc compiler version ReleaseFast-aaaaaaa",
                  "nightly_date": entry["source_date"], "app_id": manifest["apps"][0]["id"],
                  "stage": "check", "status": "exited", "code": 0, "stdout": "CURRENT-SOURCE-RESULT"}
        path = self.root / "observations.json"
        path.write_text(json.dumps([record, {**record, "key": prefix + key, "stdout": "STALE-NIGHTLY-RESULT"},
                                   {**record, "key": record["key"].replace("2026-11-23", "2026-11-22"),
                                    "stdout": "STALE-SOURCE-RESULT"},
                                   {**record, "version": "Roc compiler version nightly-2026-11-23-abcdef0"}]))
        support.render(self.config, path)
        output = Path(manifest["output"]).read_text()
        self.assertIn("Source commit " + entry["source_commit"], output)
        self.assertIn("CURRENT-SOURCE-RESULT", output)
        self.assertNotIn("STALE-NIGHTLY-RESULT", output)
        self.assertNotIn("STALE-SOURCE-RESULT", output)
        self.assertNotIn("Declared only for the published binary", output)

    def test_explicit_expectations_match_only_exact_nightly_stage_and_case(self):
        app = {"expectations": support.validate_expectations([
            {"nightly": "nightly-2026-11-02-a123456", "stage": "run", "code": 1,
             "note": "Known diagnostic change"},
            {"nightly": "nightly-2026-11-02-a123456", "stage": "run", "case_name": "specific",
             "code": 2, "note": "Specific case"}])}
        record = {"version": "Roc compiler version nightly-2026-11-02-a123456", "stage": "run",
                  "case_name": "ordinary", "status": "exited", "code": 1, "expected_code": 0,
                  "stdout": "", "stderr": "", "expected_stdout": "", "expected_stderr": ""}
        expected = support.expected_record(record, app)
        self.assertEqual(expected["expected_code"], 1)
        self.assertIn("Known diagnostic change", expected["note"])
        self.assertEqual(support.record_result(expected), "pass")
        self.assertEqual(record["expected_code"], 0, "raw observations must remain unchanged")
        self.assertEqual(support.expected_record({**record, "case_name": "specific"}, app)["expected_code"], 2)
        self.assertEqual(support.expected_record({**record, "stage": "binary"}, app)["expected_code"], 0)
        self.assertEqual(support.expected_record({**record, "version": "nightly-2026-11-03-b123456"}, app)["expected_code"], 0)
        self.assertEqual(support.record_result({**expected, "stdout": "different"}), "stdout differs")

    def test_invalid_or_ambiguous_expectation_declarations_are_rejected(self):
        valid = {"nightly": "nightly-2026-11-02-a123456", "stage": "test", "code": 1, "note": "Known failure"}
        for values in ([{**valid, "nightly": "latest"}], [{**valid, "nightly": "nightly-2026-02-30-a123456"}],
                       [{**valid, "code": True}], [{**valid, "note": ""}], [valid, valid],
                       [{**valid, "case_name": "not-a-runtime-case"}]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                support.validate_expectations(values)

    def test_report_keeps_same_hash_paths_separate_and_excludes_unconfigured_compilers(self):
        manifest = self.prepare()
        prefix = manifest["corpus_hash"] + "|" + manifest["host"] + "|"
        records = [{"key": prefix + nightly["path"], "compiler_path": nightly["path"],
                    "compiler_hash": nightly["sha256"], "app_id": manifest["apps"][0]["id"],
                    "stage": "test", "status": "exited", "code": number, "expected_code": 0,
                    "version": "nightly-2026-11-02-a123456"}
                   for number, nightly in enumerate(manifest["nightlies"])]
        records.append({**records[0], "compiler_path": "/UNCONFIGURED", "stdout": "FOREIGN-COMPILER"})
        observations = self.root / "observations.json"
        observations.write_text(json.dumps(records))
        support.render(self.config, observations)
        result = Path(manifest["output"]).read_text()
        self.assertIn(manifest["nightlies"][0]["path"], result)
        self.assertIn(manifest["nightlies"][1]["path"], result)
        self.assertIn("exit 1", result)
        self.assertIn("pass", result)
        self.assertNotIn("FOREIGN-COMPILER", result)


if __name__ == "__main__":
    unittest.main()

class ReportCompletenessTests(unittest.TestCase):
    def test_partial_command_success_does_not_make_example_pass(self):
        import html
        compiler = ('/compiler', 'hash')
        record = {'compiler_path': compiler[0], 'compiler_hash': compiler[1],
                  'stage': 'check', 'status': 'exited', 'code': 0}
        result = ''.join(support.overview(
            ['example'], {'example': {'cases': [{'name': 'default'}]}},
            {'example': [(0, record)]}, {compiler: ('2026-11-18', 'nightly', '/compiler', '')},
            [compiler], html.escape))
        self.assertIn('incomplete observations', result)
        self.assertNotIn('<td class="pass">', result)
