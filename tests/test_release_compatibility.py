"""Exercise the real native runner with inert GitHub/compiler substitutes."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
import tempfile
import unittest
import zipfile


BINARY = os.environ.get("ROC_COMPATIBILITY_BINARY")
ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_HELPER = Path(os.environ.get("ROC_COMPATIBILITY_ARCHIVE_HELPER", ROOT / "actions/release-compatibility/archive.py"))


@unittest.skipUnless(BINARY, "set ROC_COMPATIBILITY_BINARY to test the native runner")
class CompatibilityRunnerTests(unittest.TestCase):
    directory_name = "suite space é"
    temporary_parent = None

    @classmethod
    def setUpClass(cls):
        cls.launcher = None
        if os.name == "nt":
            cls.launcher_directory = tempfile.TemporaryDirectory()
            cls.addClassCleanup(cls.launcher_directory.cleanup)
            source = ROOT / "tests/fixtures/compatibility_launcher.c"
            cls.launcher = Path(cls.launcher_directory.name) / "launcher.exe"
            subprocess.run(["clang", str(source), "-o", str(cls.launcher)], check=True,
                           capture_output=True, text=True)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=self.temporary_parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / self.directory_name
        self.root.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.nightly = "nightly-2026-10-20-abc"
        self.dependency = "https://github.com/o/pkg/releases/download/1.0.0/hash.tar.zst"
        self.case = dict(name="hello", args=[], stdin="", stdout="hello\n", stderr="", exit_code=0)
        self.spec = dict(schema_version=1, release="1.0.0", dependencies=[self.dependency],
                         apps=[dict(path="examples/main.roc", cases=[self.case])])
        self.archive = self.root / "suite.zip"
        self.make_archive()
        self.write_program("gh", """import os,sys
from pathlib import Path
args=sys.argv[1:]
assert args[:3] == ['api','--method','GET'], args
if any('/releases/assets/' in a for a in args):
    sys.stdout.buffer.write(Path(os.environ['FAKE_ARCHIVE']).read_bytes())
elif any('roc-lang/nightlies/releases/latest' in a for a in args):
    print(os.environ['FAKE_NIGHTLY'])
else:
    print(Path(os.environ['FAKE_RELEASES']).read_text())
""")
        self.write_program("roc", """import os,sys
from pathlib import Path
assert not os.environ.get('GH_TOKEN') and not os.environ.get('GITHUB_TOKEN')
if os.name == 'nt':
    assert '/' not in os.environ['ROC_CACHE_DIR'], 'compiler cache root must use native Windows separators'
    assert os.environ['XDG_CACHE_HOME'] == os.environ['ROC_CACHE_DIR']
if sys.argv[1]=='version':
    print('Roc compiler version '+os.environ['FAKE_NIGHTLY'])
else:
    print('compiler stdout detail')
    print('warning: successful compilation diagnostic ::error::not an annotation',file=sys.stderr)
    if os.environ.get('FAKE_COMPILER_FAIL'): sys.exit(1)
    if sys.argv[1]=='build':
        output=next(a.split('=',1)[1] for a in sys.argv if a.startswith('--output='))
        if os.name == 'nt': assert '/' not in output, 'compiler output must use native Windows separators'
        out=Path(output)
        code = Path(os.environ['FAKE_EXAMPLE_SOURCE']).read_text()
        if os.name == 'nt' and out.suffix == '.exe':
            import shutil
            shutil.copy(os.environ['FAKE_LAUNCHER'],out)
            out.with_suffix('.py').write_text(code)
        else:
            out.write_text('#!'+sys.executable+'\\n'+code)
        out.chmod(0o755)
""")
        self.releases = self.root / "releases.json"
        self.releases.write_text(json.dumps([dict(id=1, tag_name="1.0.0", draft=False,
            prerelease=False, published_at="2026-10-20", assets=[self.asset()])]))
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "FAKE_ARCHIVE": str(self.archive), "FAKE_RELEASES": str(self.releases),
            "FAKE_NIGHTLY": self.nightly, "GH_TOKEN": "read-only-test-token",
            "GITHUB_TOKEN": "read-only-test-token", "GITHUB_REPOSITORY": "o/pkg",
            "GITHUB_WORKSPACE": str(self.root), "GITHUB_RUN_ID": "10", "GITHUB_RUN_ATTEMPT": "1",
            "RA_NIGHTLY": "", "RA_RELEASE": "", "RA_PACKAGE": "pkg",
            "RA_RUNNERS": '["ubuntu-24.04"]', "RA_RUNNER": "ubuntu-24.04",
            "FAKE_PYTHON": sys.executable, "FAKE_EXAMPLE_SOURCE": str(ROOT / "tests/fixtures/compatibility_example.py"), "FAKE_LAUNCHER": str(self.launcher or ""),
            "RA_PYTHON": sys.executable, "RA_ARCHIVE_HELPER": str(ARCHIVE_HELPER),
            "RA_SETUP": "[]", "RA_COMPILER": str(self.bin / ("roc.exe" if os.name == "nt" else "roc")), "RA_CASE_TIMEOUT_MS": "10000"}
        self.env["RA_RUNNER"] = "windows-2025" if os.name == "nt" else "ubuntu-24.04"
        self.env["RA_RUNNERS"] = json.dumps([self.env["RA_RUNNER"]])
        self.env["RA_TEST_RESULT"] = "success"
        self.env["RA_ENVIRONMENT"] = "[]"
        self.selection = self.root / "selection.json"
        self.selection.write_text(json.dumps(dict(schema_version=1, repository="o/pkg",
            nightly=self.nightly, release="1.0.0", release_id=1, asset=self.asset(), runners=[self.env["RA_RUNNER"]])))
        self.env["RA_SELECTION"] = str(self.selection)

    def write_program(self, name, source):
        target = self.bin / name
        source = "import sys\nsys.stdout.reconfigure(newline='\\n')\nsys.stderr.reconfigure(newline='\\n')\n" + source
        if os.name == "nt":
            shutil.copy(self.launcher, target.with_suffix(".exe"))
            target.with_suffix(".py").write_text(source)
        else:
            target.write_text("#!" + sys.executable + "\n" + source)
            target.chmod(0o755)

    def make_archive(self, extra=None):
        prefix = "pkg-examples-1.0.0/"
        files = {prefix + "spec.json": json.dumps(self.spec),
                 prefix + "examples/main.roc": 'app [main!] { pf: platform "' + self.dependency + '" }\n'}
        with zipfile.ZipFile(self.archive, "w") as archive:
            for name, content in files.items():
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, content)
            if extra:
                archive.writestr(*extra)

    def asset(self):
        content = self.archive.read_bytes()
        return dict(id=2, name="pkg-examples-1.0.0.zip", size=len(content),
                    digest="sha256:" + hashlib.sha256(content).hexdigest())

    def refresh_selection(self):
        selected = json.loads(self.selection.read_text())
        selected["asset"] = self.asset()
        self.selection.write_text(json.dumps(selected))

    def run_phase(self, phase, phase_timeout=20, **extra):
        directory = self.root / (phase + "-output")
        directory.mkdir(exist_ok=True)
        outputs = self.root / (phase + "-outputs")
        outputs.write_text("")
        summary = self.root / "summary.md"
        summary.write_text("")
        env = {**self.env, "RA_DIRECTORY": str(directory), "GITHUB_OUTPUT": str(outputs),
               "GITHUB_STEP_SUMMARY": str(summary), **extra}
        ran = subprocess.run([BINARY, phase], env=env, capture_output=True, text=True, timeout=phase_timeout)
        return ran, directory, outputs.read_text()

    def test_success_preserves_nonfatal_warnings_and_both_streams(self):
        ran, directory, _ = self.run_phase("test")
        self.assertEqual(ran.returncode, 0, ran.stderr + ran.stdout)
        result = json.loads((directory / "result.json").read_text())
        self.assertEqual((result["outcome"], result["tested"], result["failed"]), ("compatible", 1, 0))
        self.assertIn("warning: successful", ran.stdout)
        self.assertNotIn("::error::", ran.stdout)
        self.assertIn("::error::", (directory / "2-stderr.log").read_text())
        self.assertIn("compiler stdout detail", (directory / "2-stdout.log").read_text())

    def test_compiler_failure_is_incompatible_and_keeps_diagnostics(self):
        ran, directory, _ = self.run_phase("test", FAKE_COMPILER_FAIL="1")
        self.assertNotEqual(ran.returncode, 0)
        self.assertEqual(json.loads((directory / "result.json").read_text())["outcome"], "incompatible")
        self.assertIn("warning: successful", ran.stdout)

    def test_output_mismatch_has_expected_and_actual_evidence(self):
        self.case["stdout"] = "different\n"
        self.make_archive()
        self.refresh_selection()
        ran, directory, _ = self.run_phase("test")
        self.assertNotEqual(ran.returncode, 0)
        self.assertTrue(list(directory.glob("*-expected.json")))
        self.assertEqual(json.loads((directory / "result.json").read_text())["failed"], 1)

    def test_timeout_keeps_partial_output_and_is_not_a_compatibility_verdict(self):
        ran, directory, _ = self.run_phase("test", FAKE_CASE_SLEEP="1", RA_CASE_TIMEOUT_MS="1000")
        self.assertNotEqual(ran.returncode, 0)
        self.assertEqual(json.loads((directory / "result.json").read_text())["outcome"], "test-timeout")
        self.assertIn("hello", (directory / "4-stdout.log").read_text())

    def test_output_limit_preserves_partial_bytes_and_never_passes(self):
        ran, directory, _ = self.run_phase("test", FAKE_CASE_FLOOD="1")
        self.assertNotEqual(ran.returncode, 0)
        self.assertEqual(json.loads((directory / "result.json").read_text())["outcome"], "output-limit")
        self.assertGreater((directory / "4-stdout.log").stat().st_size, 0)
        self.assertLessEqual((directory / "4-stdout.log").stat().st_size, 10_000_000)

    def test_timeout_terminates_descendants(self):
        marker = self.root / "escaped-child"
        ran, directory, _ = self.run_phase("test", FAKE_CASE_SLEEP="1", FAKE_CHILD_MARKER=str(marker),
                                            RA_CASE_TIMEOUT_MS="500")
        self.assertNotEqual(ran.returncode, 0)
        self.assertEqual(json.loads((directory / "result.json").read_text())["outcome"], "test-timeout")
        time.sleep(2)
        self.assertFalse(marker.exists(), "timed-out command left a live descendant")

    def test_windows_case_insensitive_environment_is_reserved(self):
        for name in ("Path", "Gh_Token", "Github_Token", "SystemRoot", "Ra_Compiler", "Pathext"):
            with self.subTest(name=name):
                ran, directory, _ = self.run_phase("test", RA_ENVIRONMENT=json.dumps([dict(name=name, value="bad")]))
                self.assertNotEqual(ran.returncode, 0)
                self.assertEqual(json.loads((directory / "result.json").read_text())["outcome"], "setup-failed")

    def test_skipped_cases_are_never_a_green_pass(self):
        self.case["skip"] = dict(reason="requires a display", issue="https://github.com/o/pkg/issues/1")
        self.make_archive()
        self.refresh_selection()
        ran, directory, _ = self.run_phase("test")
        self.assertNotEqual(ran.returncode, 0)
        self.assertEqual(json.loads((directory / "result.json").read_text())["outcome"], "not-tested")

    def test_digest_change_is_setup_failure_before_compiler_runs(self):
        self.archive.write_bytes(b"different bytes")
        ran, directory, _ = self.run_phase("test")
        self.assertNotEqual(ran.returncode, 0)
        self.assertEqual(json.loads((directory / "result.json").read_text())["outcome"], "setup-failed")
        self.assertFalse(list(directory.glob("*-command.json")))

    def test_symlink_is_rejected_before_extraction(self):
        info = zipfile.ZipInfo("pkg-examples-1.0.0/examples/link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.make_archive((info, "../../escape"))
        self.refresh_selection()
        ran, directory, _ = self.run_phase("test")
        self.assertNotEqual(ran.returncode, 0)
        self.assertFalse((directory / "unpacked").exists())

    def test_traversal_is_rejected_before_extraction(self):
        info = zipfile.ZipInfo("pkg-examples-1.0.0/../../escape")
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        self.make_archive((info, "bad"))
        self.refresh_selection()
        ran, directory, _ = self.run_phase("test")
        self.assertNotEqual(ran.returncode, 0)
        self.assertFalse((directory / "unpacked").exists())
        self.assertFalse((self.root / "escape").exists())

    def test_cross_target_runtime_receives_the_built_artifact(self):
        self.spec["apps"][0].update(target="wasm32", run=[sys.executable, "{executable}"])
        self.make_archive()
        self.refresh_selection()
        ran, directory, _ = self.run_phase("test")
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        build = json.loads((directory / "3-command.json").read_text())
        self.assertIn("--target=wasm32", build["argv"])
        actual = json.loads((directory / "4-command.json").read_text())
        self.assertEqual(actual["argv"][0], sys.executable)
        self.assertTrue(actual["argv"][1].endswith(".wasm"))

    def test_zip_summary_text_in_an_unsafe_name_cannot_bypass_validation(self):
        info = zipfile.ZipInfo("pkg-examples-1.0.0/../../escape bytes uncompressed, bad")
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        self.make_archive((info, "bad"))
        self.refresh_selection()
        ran, directory, _ = self.run_phase("test")
        self.assertNotEqual(ran.returncode, 0)
        self.assertFalse((directory / "unpacked").exists())

    def test_newline_in_a_zip_name_cannot_create_fake_metadata(self):
        info = zipfile.ZipInfo("pkg-examples-1.0.0/extra\n1 file, 10 bytes uncompressed, 10 bytes compressed: 0%")
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        self.make_archive((info, "bad"))
        self.refresh_selection()
        ran, directory, _ = self.run_phase("test")
        self.assertNotEqual(ran.returncode, 0)
        self.assertFalse((directory / "unpacked").exists())

    def test_reserved_environment_cannot_replace_compiler_or_cache(self):
        ran, directory, _ = self.run_phase("test", RA_ENVIRONMENT='[{"name":"PATH","value":"/tmp"}]')
        self.assertNotEqual(ran.returncode, 0)
        self.assertEqual(json.loads((directory / "result.json").read_text())["outcome"], "setup-failed")

    def test_empty_suite_cannot_pass(self):
        self.spec["apps"] = []
        self.make_archive()
        self.refresh_selection()
        ran, directory, _ = self.run_phase("test")
        self.assertNotEqual(ran.returncode, 0)
        self.assertNotEqual(json.loads((directory / "result.json").read_text())["outcome"], "compatible")

    def test_setup_failure_is_separate_and_logged(self):
        ran, directory, _ = self.run_phase("test", RA_SETUP=json.dumps([sys.executable, "-c", "raise SystemExit(1)"]))
        self.assertNotEqual(ran.returncode, 0)
        self.assertEqual(json.loads((directory / "result.json").read_text())["outcome"], "setup-failed")
        self.assertTrue((directory / "0-command.json").exists())

    def test_selection_pins_one_nightly_and_asset(self):
        ran, directory, outputs = self.run_phase("select")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertEqual(json.loads((directory / "selection.json").read_text())["asset"], self.asset())
        self.assertIn("ready=true", outputs)

    def test_absent_final_release_fails_without_ready_output(self):
        self.releases.write_text("[]")
        ran, _, outputs = self.run_phase("select")
        self.assertNotEqual(ran.returncode, 0)
        self.assertNotIn("ready=true", outputs)

    def test_aggregate_requires_every_current_attempt_target(self):
        ran, directory, _ = self.run_phase("test")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        artifacts = self.root / "artifacts"
        target = artifacts / ("compatibility-" + self.env["RA_RUNNER"])
        target.mkdir(parents=True)
        shutil.copy(directory / "result.json", target / "result.json")
        ran, _, outputs = self.run_phase("aggregate", RA_ARTIFACTS=str(artifacts))
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("report=success", outputs)
        ran, _, outputs = self.run_phase("aggregate", RA_ARTIFACTS=str(artifacts), RA_TEST_RESULT="cancelled")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("report=none", outputs)

        ran, _, outputs = self.run_phase("aggregate", RA_ARTIFACTS=str(artifacts), GITHUB_RUN_ATTEMPT="2")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("outcome=setup-failed", outputs)
        self.assertIn("report=none", outputs)




if __name__ == "__main__":
    unittest.main()
