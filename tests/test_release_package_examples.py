"""Exercise baseline updates and platform releases with the shared release utility."""
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import unittest
import zipfile

import test_release_compatibility as fixtures


@unittest.skipUnless(os.environ.get("ROC_RELEASE_PACKAGE_BINARY"), "set ROC_RELEASE_PACKAGE_BINARY to test baseline updates")
class ReleasePackageExamplesTests(unittest.TestCase):
    def test_baseline_update_preserves_build_and_stream_policies(self):
        fixtures.CompatibilityRunnerTests.setUpClass()
        self.addCleanup(fixtures.CompatibilityRunnerTests.doClassCleanups)
        fixture = fixtures.CompatibilityRunnerTests(methodName="test_success_preserves_nonfatal_warnings_and_both_streams")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        (fixture.root / "examples").mkdir()
        (fixture.root / "package").mkdir()
        (fixture.root / "tests").mkdir()
        (fixture.root / "package/main.roc").write_text("package []\n")
        (fixture.root / "examples/main.roc").write_text('app [main!] { lib: "../package/main.roc" }\n')
        fixture.spec.update(release="working-tree", dependencies=[])
        fixture.spec["apps"][0]["build_args"] = ["--fuzz"]
        fixture.case.update(stderr_match="ignore", stderr="old unstable statistics", stdout="old output")
        specification = fixture.root / "tests/spec.json"
        specification.write_text(json.dumps(fixture.spec))
        evidence = fixture.root / "update-evidence"
        ran = subprocess.run([os.environ["ROC_RELEASE_PACKAGE_BINARY"], "test-examples", "--update"],
                             cwd=fixture.root, env={**fixture.env, "RP_REPOSITORY": "o/pkg", "RP_NAME": "pkg",
                             "RP_COMPILER": fixture.env["RA_COMPILER"], "RP_DIRECTORY": str(evidence),
                             "XDG_CACHE_HOME": str(fixture.root / "update-cache"),
                             "FAKE_CASE_STDERR": "new unstable statistics"},
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        updated = json.loads(specification.read_text())
        self.assertEqual(updated["apps"][0]["build_args"], ["--fuzz"])
        case = updated["apps"][0]["cases"][0]
        self.assertEqual((case["stderr_match"], case["stderr"], case["stdout"]), ("ignore", "", "hello\n"))
        self.assertTrue(any("new unstable statistics" in path.read_text() for path in evidence.glob("*-stderr.log")))


PLATFORM_FIXTURE = fixtures.ROOT / "tests/fixtures/platform"
PLATFORM_URL = "https://github.com/o/pf/releases/download/0.0.1/Fixture1.tar.zst"


@unittest.skipUnless(os.environ.get("ROC_RELEASE_PACKAGE_BINARY"), "set ROC_RELEASE_PACKAGE_BINARY to test platform releases")
class ReleasePlatformTests(unittest.TestCase):
    """A platform repository: examples name platform/main.roc, and the
    repository's own bundle command writes the platform bundle."""

    def setUp(self):
        fixtures.CompatibilityRunnerTests.setUpClass()
        self.addCleanup(fixtures.CompatibilityRunnerTests.doClassCleanups)
        fixture = fixtures.CompatibilityRunnerTests(methodName="test_success_preserves_nonfatal_warnings_and_both_streams")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root = fixture.root
        shutil.copytree(PLATFORM_FIXTURE, self.root, dirs_exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "platform", "examples", "tests", "docs"], cwd=self.root, check=True)
        self.env = {**fixture.env, "RP_REPOSITORY": "o/pf", "RP_NAME": "pf",
                    "RP_COMPILER": fixture.env["RA_COMPILER"], "RP_DIRECTORY": str(self.root / "evidence"),
                    "XDG_CACHE_HOME": str(self.root / "cache"), "GH_TOKEN": "", "GITHUB_TOKEN": ""}

    def run_tool(self, *args):
        return subprocess.run([os.environ["ROC_RELEASE_PACKAGE_BINARY"], *args], cwd=self.root, env=self.env,
                              capture_output=True, text=True, timeout=60)

    def platform_bundle(self, path, modules=("main.roc", "Host.roc", "Stdout.roc")):
        """What a repository's bundle command writes: its modules and host outputs."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(path, "w") as bundle:
            for name in modules:
                bundle.add(self.root / "platform" / name, arcname=name)
            host = tarfile.TarInfo("targets/x64musl/libhost.a")
            host.size = len(b"host")
            bundle.addfile(host, io.BytesIO(b"host"))

    def test_examples_run_against_the_platform_by_path(self):
        ran = self.run_tool("test-examples")
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertIn("1 tested, 0 failed", ran.stdout)

    def test_a_local_platform_elsewhere_is_refused(self):
        app = self.root / "examples/hello/main.roc"
        app.write_text(app.read_text().replace("../../platform/main.roc", "../../other/main.roc"))
        ran = self.run_tool("test-examples")
        self.assertNotEqual(ran.returncode, 0)
        self.assertIn("a platform this repository releases lives at platform/main.roc", ran.stderr)

    @unittest.skipUnless(shutil.which("zip"), "building the archives needs zip")
    def test_build_names_the_platform_by_its_release_url(self):
        self.platform_bundle(self.root / "build/platform/Fixture1.tar.zst")
        ran = self.run_tool("build", "0.0.1", "--platform-bundle", "build/platform")
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        dist = self.root / "dist"
        self.assertTrue((dist / "Fixture1.tar.zst").is_file())
        with zipfile.ZipFile(dist / "pf-examples-0.0.1.zip") as archive:
            app = archive.read("pf-examples-0.0.1/examples/hello/main.roc").decode()
            spec = json.loads(archive.read("pf-examples-0.0.1/spec.json"))
        self.assertIn(f'pf: platform "{PLATFORM_URL}"', app)
        self.assertEqual(spec["dependencies"], [PLATFORM_URL])
        self.assertIn(f'platform "{PLATFORM_URL}"', (dist / "release-notes.md").read_text())
        ran = self.run_tool("test-archive")
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertIn("1 tested, 0 failed", ran.stdout)

    @unittest.skipUnless(shutil.which("zip"), "building the archives needs zip")
    def test_build_refuses_a_platform_bundle_it_cannot_release(self):
        given = self.root / "build/platform"
        given.mkdir(parents=True)
        self.platform_bundle(self.root / "build/two/A1.tar.zst")
        self.platform_bundle(self.root / "build/two/B2.tar.zst")
        self.platform_bundle(self.root / "build/partial/C3.tar.zst", modules=("main.roc", "Host.roc"))
        for args, message in [((), "--platform-bundle DIR"),
                              (("--platform-bundle", "build/platform"), "holds no .tar.zst bundle"),
                              (("--platform-bundle", "build/two"), "it must hold exactly one"),
                              (("--platform-bundle", "build/partial"), '["Stdout.roc"]')]:
            with self.subTest(args=args):
                ran = self.run_tool("build", "0.0.1", *args)
                self.assertNotEqual(ran.returncode, 0, ran.stdout)
                self.assertIn(message, ran.stderr)

    def test_archive_unpacks_and_runs_on_every_system(self):
        # The archived runtime consumes ancillary files before invoking the
        # compiled application. Every native test uses this same release path.
        dist = self.root / "dist"
        self.platform_bundle(dist / "Fixture1.tar.zst")
        spec = json.loads((self.root / "tests/spec.json").read_text())
        spec.update(release="0.0.1", dependencies=[PLATFORM_URL])
        spec["apps"][0]["run"] = [sys.executable, "{suite}/scripts/runtime.py", "{suite}", "{executable}"]
        app = (self.root / "examples/hello/main.roc").read_text().replace("../../platform/main.roc", PLATFORM_URL)
        runtime = """import json,subprocess,sys
from pathlib import Path
root=Path(sys.argv[1])
assert (root/'benchmarks/animation/data.txt').read_text() == 'fixture'
assert (root/'runtime-assets/abc.bin').read_bytes() == b'payload'
assert json.loads((root/'fixture-aliases.json').read_text()) == {'fixture': 'abc.bin'}
sys.exit(subprocess.run([sys.argv[2]]).returncode)
"""
        with zipfile.ZipFile(dist / "pf-examples-0.0.1.zip", "w") as archive:
            for name, content in [("README.md", "examples\n"), ("spec.json", json.dumps(spec)),
                                  ("examples/hello/main.roc", app), ("scripts/runtime.py", runtime),
                                  ("benchmarks/animation/data.txt", "fixture"),
                                  ("runtime-assets/abc.bin", b"payload"),
                                  ("fixture-aliases.json", json.dumps({"fixture": "abc.bin"}))]:
                archive.writestr("pf-examples-0.0.1/" + name, content)
        self.env["RP_CASE_TIMEOUT_MS"] = "120000"
        ran = self.run_tool("test-archive")
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertIn("SERVE", ran.stdout)
        self.assertIn("1 tested, 0 failed", ran.stdout)
        record = json.loads((self.root / "evidence/3-command.json").read_text())
        self.assertEqual(record["timeout_ms"], 120000)
        self.assertIn("scripts", record["argv"][1])

    def test_archive_refuses_unsafe_ancillary_paths_before_execution(self):
        dist = self.root / "dist"
        self.platform_bundle(dist / "Fixture1.tar.zst")
        for entry in ("../escape", "scripts/../escape", "scripts/CON.txt", "runtime-assets/trailing.",
                      "scripts/back\\slash.py", "scripts/C:drive.py", "/absolute/runtime.py"):
            with self.subTest(entry=entry):
                with zipfile.ZipFile(dist / "pf-examples-0.0.1.zip", "w") as archive:
                    archive.writestr("pf-examples-0.0.1/README.md", "examples")
                    archive.writestr("pf-examples-0.0.1/spec.json", "invalid until path checks pass")
                    archive.writestr("pf-examples-0.0.1/" + entry, "unsafe")
                ran = self.run_tool("test-archive")
                self.assertNotEqual(ran.returncode, 0, ran.stdout)
                self.assertIn("unexpected entry in the examples archive", ran.stderr)
                self.assertFalse(list((self.root / "evidence").glob("*-command.json")))

    def test_case_deadline_defaults_and_reaches_working_tree_runner(self):
        for supplied, expected in [("", 10000), ("1", 1), ("120000", 120000)]:
            with self.subTest(supplied=supplied):
                self.env["RP_CASE_TIMEOUT_MS"] = supplied
                ran = self.run_tool("test-examples")
                record = json.loads((self.root / "evidence/3-command.json").read_text())
                self.assertEqual(record["timeout_ms"], expected)
                if expected > 1:
                    self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_invalid_case_deadlines_are_refused_before_execution(self):
        for value in ("0", "120001", "-1", "1.5", "unbounded"):
            with self.subTest(value=value):
                self.env["RP_CASE_TIMEOUT_MS"] = value
                ran = self.run_tool("test-examples")
                self.assertNotEqual(ran.returncode, 0, ran.stdout)
                self.assertIn("RP_CASE_TIMEOUT_MS must be 1–120000 milliseconds", ran.stderr)
                self.assertFalse((self.root / "evidence").exists())

    def test_configured_case_deadline_interrupts_the_application(self):
        self.env.update(RP_CASE_TIMEOUT_MS="500", FAKE_CASE_SLEEP="1")
        ran = self.run_tool("test-examples")
        self.assertNotEqual(ran.returncode, 0, ran.stdout)
        record = json.loads((self.root / "evidence/3-command.json").read_text())
        self.assertEqual(record["timeout_ms"], 500)
        self.assertTrue(record["timed_out"])
        self.assertIn("hello", (self.root / "evidence/3-stdout.log").read_text())


@unittest.skipUnless(os.environ.get("ROC_RELEASE_PACKAGE_BINARY"), "set ROC_RELEASE_PACKAGE_BINARY to test version validation")
class ReleaseVersionTests(unittest.TestCase):
    setUp = ReleasePlatformTests.setUp
    run_tool = ReleasePlatformTests.run_tool

    def prepare(self, version="4.0.0", previous="3.0.0", assets=None):
        self.version = version
        (self.root / "docs/releases" / (version + ".adoc")).write_text("= Release\n\n{platform-url}\n")
        self.bin = self.root / "bin"
        self.launcher = fixtures.CompatibilityRunnerTests.launcher
        fixtures.CompatibilityRunnerTests.write_program(self, "gh", """import os,sys
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['FAKE_GH_LOG'], 'a') as log: log.write(__import__('json').dumps(args)+'\\n')
if args[:2] == ['release', 'download']:
    assert args == ['release', 'download', os.environ['FAKE_OLD_TAG'], '--repo', 'o/pf', '--pattern', os.environ['FAKE_OLD_NAME'], '--output', '-', '--allow-escape-sequences'], args
    if os.environ.get('FAKE_DOWNLOAD_FAIL'): sys.exit(1)
    sys.stdout.buffer.write(Path(os.environ['FAKE_OLD_BYTES']).read_bytes())
    sys.exit(0)
assert args[:2] == ['release', 'view']
if os.environ.get('FAKE_NO_RELEASE'):
    print('release not found', file=sys.stderr)
    sys.exit(1)
if os.environ.get('FAKE_GH_FAIL'): sys.exit(1)
print(os.environ['FAKE_BASELINE'])
""")
        fixtures.CompatibilityRunnerTests.write_program(self, "roc", """import json,os,sys
from pathlib import Path
with open(os.environ['FAKE_COMPILER_LOG'], 'a') as log: log.write(json.dumps(sys.argv[1:])+'\\n')
if sys.argv[1]=='bump':
    failing_root=os.environ.get('FAKE_BUMP_FAIL_ROOT')
    sys.exit(int(os.environ.get('FAKE_BUMP_FAIL','0')) if not failing_root or sys.argv[-1]==failing_root else 0)
if sys.argv[1]=='bundle':
    import hashlib,io,tarfile
    if os.environ.get('FAKE_COMPILER_FAIL'): sys.exit(1)
    args=sys.argv[2:]
    content=io.BytesIO()
    with tarfile.open(fileobj=content,mode='w') as bundle:
        for name in args[:args.index('--output-dir')]:
            data=Path(name).read_bytes()
            member=tarfile.TarInfo(name)
            member.size=len(data)
            bundle.addfile(member,io.BytesIO(data))
        if os.environ.get('FAKE_BUNDLE_EXTRA_MEMBER'):
            bundle.addfile(tarfile.TarInfo('untracked.bin'),io.BytesIO(b''))
    data=content.getvalue()
    name=os.environ.get('FAKE_BUNDLE_NAME') or hashlib.sha256(data).hexdigest()+'.tar.zst'
    (Path(args[args.index('--output-dir')+1])/name).write_bytes(data)
    sys.exit(0)
if sys.argv[1]=='version': print('Roc compiler version '+os.environ['FAKE_NIGHTLY'])
else: sys.exit(int(os.environ.get('FAKE_COMPILER_FAIL','0')))
""")
        self.log = self.root / "compiler.jsonl"
        self.env.update(FAKE_BASELINE=json.dumps(dict(tag=previous, assets=assets if assets is not None else ["Old1.tar.zst"])),
                        FAKE_COMPILER_LOG=str(self.log), FAKE_BUMP_FAIL="1", FAKE_GH_LOG=str(self.root / "gh.jsonl"))

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_new_major_proves_sufficiency_without_api_extraction(self):
        self.prepare()
        ran = self.run_tool("check-version", self.version)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertIn("no API comparison is required", ran.stdout)
        self.assertEqual(self.calls(), [])

    def test_new_major_prerelease_uses_the_same_proof(self):
        self.prepare("4.0.0-rc.1")
        ran = self.run_tool("check-version", self.version)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertEqual(self.calls(), [])

    def test_pre_one_minor_proves_sufficiency_without_api_extraction(self):
        for version in ("0.12.0", "0.12.0-rc.1"):
            with self.subTest(version=version):
                self.prepare(version, previous="0.11.9")
                ran = self.run_tool("check-version", version)
                self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
                self.assertIn("crosses a breaking-version boundary", ran.stdout)
                self.assertEqual(self.calls(), [])

    def test_pre_one_patch_and_downgrade_still_require_comparison(self):
        for version in ("0.11.1", "0.11.1-rc.1", "0.10.9"):
            with self.subTest(version=version):
                self.prepare(version, previous="0.11.0")
                self.log.unlink(missing_ok=True)
                ran = self.run_tool("check-version", version)
                self.assertNotEqual(ran.returncode, 0)
                self.assertEqual(self.calls(), [["bump", "--old", "https://github.com/o/pf/releases/download/0.11.0/Old1.tar.zst", "--expect", version.split("-")[0], "platform/main.roc"]])

    def test_pre_one_minor_does_not_accept_ambiguous_bundles(self):
        self.prepare("0.12.0", previous="0.11.0", assets=["Old1.tar.zst", "Other2.tar.zst"])
        self.assertNotEqual(self.run_tool("check-version", self.version).returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_pre_one_minor_does_not_override_current_compilation_failure(self):
        self.prepare("0.12.0", previous="0.11.0")
        self.assertEqual(self.run_tool("check-version", self.version).returncode, 0)
        self.env["FAKE_COMPILER_FAIL"] = "1"
        ran = self.run_tool("test-examples")
        self.assertNotEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertTrue(any(call[0] == "check" for call in self.calls()))

    def test_same_major_requires_successful_api_comparison(self):
        for version in ("3.0.1", "3.1.0", "3.1.0-rc.1"):
            with self.subTest(version=version):
                self.prepare(version)
                self.log.unlink(missing_ok=True)
                ran = self.run_tool("check-version", version)
                self.assertNotEqual(ran.returncode, 0)
                self.assertEqual(self.calls(), [["bump", "--old", "https://github.com/o/pf/releases/download/3.0.0/Old1.tar.zst", "--expect", version.split("-")[0], "platform/main.roc"]])

    def test_same_major_success_still_executes_the_comparison(self):
        self.prepare("3.1.0")
        self.env["FAKE_BUMP_FAIL"] = "0"
        ran = self.run_tool("check-version", self.version)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertEqual(len(self.calls()), 1)

    def test_invalid_or_overflowing_baselines_cannot_prove_a_major(self):
        for previous in ("", "garbage", "v3.0.0", "03.0.0", "3.0.0-rc.1", "18446744073709551616.0.0", "0.18446744073709551616.0", "0.11.18446744073709551616"):
            with self.subTest(previous=previous):
                self.prepare(previous=previous, assets=[])
                ran = self.run_tool("check-version", self.version)
                self.assertNotEqual(ran.returncode, 0)
                self.assertEqual(self.calls(), [])

    def test_pre_one_requested_metadata_cannot_prove_an_invalid_boundary(self):
        for version in ("0.12.0-01", "0.12.0-rc..1", "0.012.0", "0.18446744073709551616.0", "0.12.18446744073709551616"):
            with self.subTest(version=version):
                self.prepare(version, previous="0.11.0")
                ran = self.run_tool("check-version", version)
                self.assertNotEqual(ran.returncode, 0)
                self.assertEqual(self.calls(), [])

    def test_inaccessible_or_unreadable_baselines_still_fail(self):
        self.prepare()
        for extra in ({"FAKE_GH_FAIL": "1"}, {"FAKE_BASELINE": "{}"}, {"FAKE_BASELINE": "not JSON"}):
            with self.subTest(extra=extra):
                saved = dict(self.env)
                self.env.update(extra)
                self.assertNotEqual(self.run_tool("check-version", self.version).returncode, 0)
                self.env = saved
        self.assertEqual(self.calls(), [])

    def test_new_major_does_not_accept_ambiguous_bundles(self):
        self.prepare(assets=["Old1.tar.zst", "Other2.tar.zst"])
        self.assertNotEqual(self.run_tool("check-version", self.version).returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_new_major_does_not_override_current_example_compilation_failure(self):
        self.prepare()
        self.assertEqual(self.run_tool("check-version", self.version).returncode, 0)
        self.env["FAKE_COMPILER_FAIL"] = "1"
        ran = self.run_tool("test-examples")
        self.assertNotEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertTrue(any(call[0] == "check" for call in self.calls()))

    def test_dry_run_keeps_its_nonpublishing_comparison_behavior(self):
        self.prepare("0.0.1-dryrun")
        ran = self.run_tool("check-version", self.version)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertIn("roc bump could not compare", ran.stdout)
        self.assertEqual(self.calls()[0][0], "bump")
        self.assertNotIn("--expect", self.calls()[0])

    def test_first_release_has_no_api_baseline(self):
        self.prepare()
        self.env["FAKE_NO_RELEASE"] = "1"
        ran = self.run_tool("check-version", self.version)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertIn("first", ran.stdout)
        self.assertEqual(self.calls(), [])

    def test_same_major_requires_success_for_every_bundle(self):
        self.prepare("3.1.0", assets=["pf-Old1.tar.zst", "pf-package-Old2.tar.zst"])
        (self.root / "package").mkdir()
        (self.root / "package/main.roc").write_text("package []\n")
        subprocess.run(["git", "add", "package"], cwd=self.root, check=True)
        app = self.root / "examples/hello/main.roc"
        app.write_text(app.read_text().replace('platform "../../platform/main.roc"',
                       'platform "../../platform/main.roc", lib: "../../package/main.roc"'))
        self.env["FAKE_BUMP_FAIL_ROOT"] = "package/main.roc"
        ran = self.run_tool("check-version", self.version)
        self.assertNotEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertEqual([call[-1] for call in self.calls() if call[0] == "bump"], ["platform/main.roc", "package/main.roc"])


@unittest.skipUnless(os.environ.get("ROC_RELEASE_PACKAGE_BINARY"), "set ROC_RELEASE_PACKAGE_BINARY to test package identity proofs")
class ReleasePackageIdentityTests(unittest.TestCase):
    setUp = ReleasePlatformTests.setUp
    run_tool = ReleasePlatformTests.run_tool
    calls = ReleaseVersionTests.calls

    def prepare(self, version="0.5.1"):
        ReleaseVersionTests.prepare(self, version, previous="0.5.0")
        (self.root / "package").mkdir()
        (self.root / "package/main.roc").write_text("package [value]\nvalue = 1\n")
        # Invalid UTF-8 and terminal escape bytes are ordinary bundle data.
        (self.root / "package/data.bin").write_bytes(b'\xff\x1b[31m::error::inert\x00')
        (self.root / "examples/hello/main.roc").write_text(
            'app [main!] { pf: platform "https://example.com/Remote.tar.zst", lib: "../../package/main.roc" }\n')
        subprocess.run(["git", "add", "package", "examples"], cwd=self.root, check=True)
        previous = self.root / "old-bundle"
        previous.mkdir()
        subprocess.run([self.env["RP_COMPILER"], "bundle", "main.roc", "data.bin", "--output-dir", str(previous)],
                       cwd=self.root / "package", env=self.env, check=True, capture_output=True)
        old = next(previous.iterdir())
        self.old_name = old.name
        self.old_bytes = old.read_bytes()
        self.env.update(FAKE_OLD_TAG="0.5.0", FAKE_OLD_NAME=old.name, FAKE_OLD_BYTES=str(old),
                        FAKE_BASELINE=json.dumps(dict(tag="0.5.0", assets=[old.name])))
        self.log.unlink()

    def gh_calls(self):
        path = self.root / "gh.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_identical_patch_proves_no_api_difference_despite_failed_extraction(self):
        self.prepare()
        ran = self.run_tool("check-version", self.version)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertIn("byte-identical", ran.stdout)
        self.assertIn("no API difference", ran.stdout)
        self.assertEqual([call[0] for call in self.calls()], ["bundle"])
        self.assertEqual(self.gh_calls()[-1][2], "0.5.0")
        self.assertNotIn("::error::inert", ran.stdout + ran.stderr)

    def test_changed_candidate_compares_despite_stale_matching_dist_and_cache(self):
        self.prepare()
        (self.root / "dist").mkdir()
        (self.root / "dist" / self.old_name).write_bytes(self.old_bytes)
        (self.root / "cache" / self.old_name).parent.mkdir(exist_ok=True)
        (self.root / "cache" / self.old_name).write_bytes(self.old_bytes)
        (self.root / "package/main.roc").write_text("package [value]\nvalue = 2\n")
        ran = self.run_tool("check-version", self.version)
        self.assertNotEqual(ran.returncode, 0)
        self.assertEqual([call[0] for call in self.calls()], ["bundle", "bump"])
        self.assertEqual(self.calls()[-1][-3:], ["--expect", "0.5.1", "package/main.roc"])
        self.assertEqual(len(self.gh_calls()), 1)

    def test_same_name_different_short_or_oversized_bytes_fail_closed(self):
        self.prepare()
        old = self.root / "old-download"
        self.env["FAKE_OLD_BYTES"] = str(old)
        for content in (self.old_bytes[:-1], b'x' * len(self.old_bytes), self.old_bytes + b'x' * 4096):
            with self.subTest(length=len(content)):
                old.write_bytes(content)
                ran = self.run_tool("check-version", self.version)
                self.assertNotEqual(ran.returncode, 0)
                self.assertNotIn("byte-identical", ran.stdout)
        self.assertTrue(all(call[0] == "bundle" for call in self.calls()))

    def test_failed_download_does_not_prove_identity(self):
        self.prepare()
        self.env["FAKE_DOWNLOAD_FAIL"] = "1"
        ran = self.run_tool("check-version", self.version)
        self.assertNotEqual(ran.returncode, 0)
        self.assertIn("cannot download", ran.stderr)
        self.assertEqual([call[0] for call in self.calls()], ["bundle"])

    def test_ambiguous_or_invalid_metadata_cannot_prove_identity(self):
        self.prepare()
        for baseline in (dict(tag="0.5.0", assets=[self.old_name, self.old_name]),
                         dict(tag="0.5.0", assets=[self.old_name, "Other1.tar.zst"]),
                         dict(tag="0.5.0-rc.1", assets=[self.old_name]),
                         dict(tag="v0.5.0", assets=[self.old_name])):
            with self.subTest(baseline=baseline):
                self.env["FAKE_BASELINE"] = json.dumps(baseline)
                ran = self.run_tool("check-version", self.version)
                self.assertNotEqual(ran.returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertTrue(all(call[:2] == ["release", "view"] for call in self.gh_calls()))

    def test_wrong_bundle_prefix_uses_normal_missing_bundle_behavior(self):
        self.prepare()
        self.env["FAKE_BASELINE"] = json.dumps(dict(tag="0.5.0", assets=["other-" + self.old_name]))
        ran = self.run_tool("check-version", self.version)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertIn("has no bundle", ran.stdout)
        self.assertNotIn("byte-identical", ran.stdout)
        self.assertEqual(self.calls(), [])
        self.assertEqual(len(self.gh_calls()), 1)

    def test_selected_different_release_filename_still_requires_api_comparison(self):
        self.prepare()
        self.env["FAKE_BASELINE"] = json.dumps(dict(tag="0.5.0", assets=["pf-" + self.old_name]))
        ran = self.run_tool("check-version", self.version)
        self.assertNotEqual(ran.returncode, 0)
        self.assertEqual([call[0] for call in self.calls()], ["bundle", "bump"])
        self.assertEqual(len(self.gh_calls()), 1)

    def test_multiple_packages_use_the_canonical_prefixed_release_filename(self):
        self.prepare()
        extra = self.root / "extras/package"
        extra.mkdir(parents=True)
        (extra / "main.roc").write_text("package []\n")
        app = self.root / "examples/hello/main.roc"
        app.write_text(app.read_text().replace('lib: "../../package/main.roc"',
                       'lib: "../../package/main.roc", extra: "../../extras/package/main.roc"'))
        subprocess.run(["git", "add", "extras", "examples"], cwd=self.root, check=True)
        name = "pf-" + self.old_name
        self.env.update(FAKE_OLD_NAME=name, FAKE_BASELINE=json.dumps(dict(tag="0.5.0", assets=[name])))
        ran = self.run_tool("check-version", self.version)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertIn("byte-identical", ran.stdout)
        self.assertIn("has no bundle of extras/package/main.roc", ran.stdout)
        self.assertEqual([call[0] for call in self.calls()], ["bundle"])
        self.assertEqual(self.gh_calls()[-1][5:7], ["--pattern", name])

    def test_same_version_prerelease_and_downgrade_still_compare(self):
        self.prepare()
        for version in ("0.5.0", "0.5.0-rc.1", "0.4.9"):
            with self.subTest(version=version):
                (self.root / "docs/releases" / (version + ".adoc")).write_text("= Release\n")
                ran = self.run_tool("check-version", version)
                self.assertNotEqual(ran.returncode, 0)
        self.assertEqual([call[0] for call in self.calls()], ["bump"] * 3)

    def test_untracked_files_are_not_bundled_and_unexpected_members_fail(self):
        self.prepare()
        (self.root / "package/untracked.bin").write_bytes(b'excluded')
        ran = self.run_tool("check-version", self.version)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertNotIn("untracked.bin", self.calls()[0])
        self.env["FAKE_BUNDLE_EXTRA_MEMBER"] = "1"
        ran = self.run_tool("check-version", self.version)
        self.assertNotEqual(ran.returncode, 0)
        self.assertIn("not the tracked files", ran.stderr)

    def test_identical_package_does_not_override_candidate_compilation_failure(self):
        self.prepare()
        self.assertEqual(self.run_tool("check-version", self.version).returncode, 0)
        self.env["FAKE_COMPILER_FAIL"] = "1"
        ran = self.run_tool("test-examples")
        self.assertNotEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertTrue(any(call[0] == "check" for call in self.calls()))
        self.assertNotEqual(self.run_tool("check-version", self.version).returncode, 0)
