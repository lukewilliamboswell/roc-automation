"""Exercise local expectation edits through the same native examples engine."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BINARY = os.environ.get("ROC_RELEASE_OPERATIONS_BINARY")


@unittest.skipUnless(BINARY, "set ROC_RELEASE_OPERATIONS_BINARY")
class ExamplesUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launcher = None
        if os.name == "nt":
            cls.launcher_directory = tempfile.TemporaryDirectory()
            cls.addClassCleanup(cls.launcher_directory.cleanup)
            cls.launcher = Path(cls.launcher_directory.name) / "launcher.exe"
            subprocess.run(["clang", str(ROOT / "tests/fixtures/compatibility_launcher.c"), "-o", str(cls.launcher)], check=True, capture_output=True)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "suite space é"
        (self.root / "examples").mkdir(parents=True)
        (self.root / "examples/main.roc").write_text("app [main!] {}\n")
        self.case = dict(name="hello", args=[], stdin="", stdout="stale\n", stderr="", exit_code=0)
        self.spec = dict(schema_version=1, release="working-tree", dependencies=[], apps=[dict(path="examples/main.roc", cases=[self.case])])
        self.path = self.root / "spec.json"
        compiler = self.root / ("compiler.exe" if os.name == "nt" else "compiler")
        source = r'''import os,sys,shutil
from pathlib import Path
assert not os.environ.get('GH_TOKEN') and not os.environ.get('GITHUB_TOKEN')
if sys.argv[1]=='version': print('Roc compiler version nightly-2026-10-23-18478ff')
else:
    if os.environ.get('FAIL_BUILD'): sys.exit(1)
    if sys.argv[1]=='build':
        output=Path(next(arg.split('=',1)[1] for arg in sys.argv if arg.startswith('--output=')))
        if os.name=='nt':
            shutil.copy(os.environ['FAKE_LAUNCHER'],output)
            output.with_suffix('.py').write_text(Path(os.environ['CASE_SOURCE']).read_text())
        else:
            output.write_text('#!'+sys.executable+'\n'+Path(os.environ['CASE_SOURCE']).read_text())
            output.chmod(0o755)
'''
        if os.name == "nt":
            shutil.copy(self.launcher, compiler)
            compiler.with_suffix(".py").write_text(source)
        else:
            compiler.write_text("#!" + sys.executable + "\n" + source)
            compiler.chmod(0o755)
        self.case_source = self.root / "case.py"
        self.case_source.write_text(r'''import os,sys,time
from pathlib import Path
if '--skip' in sys.argv: raise AssertionError('skipped case executed')
if os.environ.get('CHANGE_SPEC'): Path(os.environ['SPEC']).write_text('{"changed":true}\n')
if os.environ.get('SYMLINK_SPEC'):
    p=Path(os.environ['SPEC']); p.unlink(); p.symlink_to(os.environ['SYMLINK_SPEC'])
if os.environ.get('FLOOD'): sys.stdout.buffer.write(b'x'*11000000);sys.stdout.buffer.flush();time.sleep(2)
if os.environ.get('SLEEP'): time.sleep(30)
if os.environ.get('BUDGET_EXPECTATIONS'): sys.stdout.buffer.write(b'x'*1048577)
if os.environ.get('BUDGET_ENCODING'): sys.stdout.buffer.write(b'\x00'*200000)
if os.environ.get('INVALID_UTF8'): sys.stdout.buffer.write(b'\xff')
else: sys.stdout.buffer.write(os.environ.get('ACTUAL_STDOUT','hello\n').encode())
sys.stderr.buffer.write(os.environ.get('ACTUAL_STDERR','').encode())
sys.exit(int(os.environ.get('ACTUAL_EXIT','0')))
''')
        self.env = {**os.environ, "RO_COMPILER": str(compiler), "FAKE_PYTHON": sys.executable, "FAKE_LAUNCHER": str(self.launcher or ""), "CASE_SOURCE": str(self.case_source), "SPEC": str(self.path), "CI": "", "GITHUB_ACTIONS": "", "RA_ENVIRONMENT": "[]", "GH_TOKEN": "secret", "GITHUB_TOKEN": "secret"}

    def run_update(self, **extra):
        return subprocess.run([BINARY, "update", "spec.json", str(self.root)], env={**self.env, **extra}, capture_output=True, text=True, timeout=45)

    def write(self):
        self.path.write_text(json.dumps(self.spec, separators=(",", ":")))
        return self.path.read_bytes()

    def test_stale_outputs_update_and_noop_retains_original_bytes(self):
        skipped = dict(self.case, name="skip", args=["--skip"], stdout="keep exactly", skip=dict(reason="pending", issue="https://github.com/o/r/issues/1"))
        self.spec["apps"][0]["cases"].append(skipped)
        self.write()
        ran = self.run_update(ACTUAL_STDERR="warning\n")
        self.assertEqual(ran.returncode, 0, ran.stderr + ran.stdout)
        actual = json.loads(self.path.read_text())
        expected = json.loads(json.dumps(self.spec))
        expected["apps"][0]["cases"][0].update(stdout="hello\n", stderr="warning\n")
        self.assertEqual(actual, expected)
        self.assertTrue(self.path.read_bytes().endswith(b"\n"))
        # No-op also preserves an author's compact, unformatted representation.
        self.path.write_text(json.dumps(actual, separators=(",", ":")))
        before = self.path.read_bytes()
        ran = self.run_update(ACTUAL_STDERR="warning\n")
        self.assertEqual(ran.returncode, 0, ran.stderr + ran.stdout)
        self.assertEqual(self.path.read_bytes(), before)

    def test_exact_default_preserves_ansi_and_explicit_normalization_is_bounded(self):
        ansi = "\x1b[31mhello\x1b[0m\r\n"
        self.write()
        ran = self.run_update(ACTUAL_STDOUT=ansi)
        self.assertEqual(ran.returncode, 0, ran.stderr + ran.stdout)
        self.assertEqual(json.loads(self.path.read_text())["apps"][0]["cases"][0]["stdout"], ansi)
        self.case.update(stdout_match="strip-sgr-and-cr", stdout="stale")
        self.write()
        ran = self.run_update(ACTUAL_STDOUT=ansi)
        self.assertEqual(ran.returncode, 0, ran.stderr + ran.stdout)
        self.assertEqual(json.loads(self.path.read_text())["apps"][0]["cases"][0]["stdout"], "hello\n")
        self.case["stdout"] = ansi
        before = self.write()
        ran = self.run_update(ACTUAL_STDOUT="hello\n")
        self.assertEqual(ran.returncode, 0, ran.stderr + ran.stdout)
        self.assertEqual(self.path.read_bytes(), before)
        self.case["stdout"] = "stale"
        self.write()
        ran = self.run_update(ACTUAL_STDOUT="\x1b[2Jhello\x1b[31")
        self.assertEqual(ran.returncode, 0, ran.stderr + ran.stdout)
        self.assertEqual(json.loads(self.path.read_text())["apps"][0]["cases"][0]["stdout"], "\x1b[2Jhello\x1b[31")

    def test_no_write_on_status_build_crash_utf8_or_output_failure(self):
        for extra in (dict(ACTUAL_EXIT="1"), dict(FAIL_BUILD="1"), dict(INVALID_UTF8="1"), dict(FLOOD="1"), dict(ACTUAL_STDERR="[ROC CRASHED]"), dict(ACTUAL_STDOUT="\x1b[31m[roc cra\rshed]\x1b[m")):
            with self.subTest(extra=extra):
                before = self.write()
                ran = self.run_update(**extra)
                self.assertNotEqual(ran.returncode, 0, ran.stdout + ran.stderr)
                self.assertEqual(self.path.read_bytes(), before)

    def test_failed_later_case_does_not_partially_update_earlier_output(self):
        self.spec["apps"][0]["cases"].append(dict(self.case, name="wrong-exit", exit_code=1))
        before = self.write()
        self.assertNotEqual(self.run_update().returncode, 0)
        self.assertEqual(self.path.read_bytes(), before)

    def test_expected_nonzero_cannot_hide_roc_crash_but_admitted_fuzz77_can(self):
        self.case.update(exit_code=1, stderr_match="ignore")
        before = self.write()
        self.assertNotEqual(self.run_update(ACTUAL_EXIT="1", ACTUAL_STDERR="[roc crashed]").returncode, 0)
        self.assertEqual(self.path.read_bytes(), before)
        self.spec["apps"][0]["build_args"] = ["--fuzz"]
        self.case.update(exit_code=77)
        self.write()
        ran = self.run_update(ACTUAL_EXIT="77", ACTUAL_STDERR="[roc crashed]\nvariable libFuzzer stats")
        self.assertEqual(ran.returncode, 0, ran.stderr + ran.stdout)
        actual = json.loads(self.path.read_text())["apps"][0]["cases"][0]
        self.assertEqual(actual["stderr"], "")
        self.assertEqual(actual["stderr_match"], "ignore")
        self.assertEqual(actual["exit_code"], 77)

    def test_observations_and_encoded_spec_each_keep_the_input_budget(self):
        for name in ("BUDGET_EXPECTATIONS", "BUDGET_ENCODING"):
            with self.subTest(name=name):
                before = self.write()
                ran = self.run_update(**{name: "1"})
                self.assertNotEqual(ran.returncode, 0)
                self.assertIn("budget", ran.stderr)
                self.assertEqual(self.path.read_bytes(), before)

    def test_published_suite_marker_cannot_be_updated(self):
        self.spec["release"] = "1.2.3"
        before = self.write()
        self.assertNotEqual(self.run_update().returncode, 0)
        self.assertEqual(self.path.read_bytes(), before)

    def test_timeout_is_not_an_expected_exit_and_cannot_write(self):
        self.case["exit_code"] = 124
        before = self.write()
        ran = self.run_update(SLEEP="1")
        self.assertNotEqual(ran.returncode, 0)
        self.assertEqual(self.path.read_bytes(), before)

    def test_all_skipped_ci_unknown_fields_and_invalid_modes_refuse_update(self):
        for modify, extra in ((lambda: None, dict(CI="1")), (lambda: None, dict(GITHUB_ACTIONS="true")), (lambda: self.case.update(skip=dict(reason="pending", issue="https://github.com/o/r/issues/1")), {}), (lambda: self.case.update(future_metadata="keep"), {}), (lambda: self.case.update(stdout_match="strip-everything"), {})):
            with self.subTest(extra=extra):
                self.case.pop("skip", None); self.case.pop("future_metadata", None); self.case.pop("stdout_match", None)
                modify()
                before = self.write()
                self.assertNotEqual(self.run_update(**extra).returncode, 0)
                self.assertEqual(self.path.read_bytes(), before)

    def test_child_changes_spec_so_stale_observations_cannot_overwrite_it(self):
        self.write()
        ran = self.run_update(CHANGE_SPEC="1")
        self.assertNotEqual(ran.returncode, 0)
        self.assertEqual(self.path.read_text(), '{"changed":true}\n')
        if os.name != "nt":
            self.write()
            untouched = self.root / "other.json"
            untouched.write_text("untouched")
            ran = self.run_update(SYMLINK_SPEC=str(untouched))
            self.assertNotEqual(ran.returncode, 0)
            self.assertTrue(self.path.is_symlink())
            self.assertEqual(untouched.read_text(), "untouched")
