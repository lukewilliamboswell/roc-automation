"""Exercise baseline updates and platform releases with the shared release utility."""
import io
import json
import os
import shutil
import subprocess
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
        # The archive as build writes it, made here so that a system without
        # zip, such as Windows, still tests unpacking it and running it.
        dist = self.root / "dist"
        self.platform_bundle(dist / "Fixture1.tar.zst")
        spec = json.loads((self.root / "tests/spec.json").read_text())
        spec.update(release="0.0.1", dependencies=[PLATFORM_URL])
        app = (self.root / "examples/hello/main.roc").read_text().replace("../../platform/main.roc", PLATFORM_URL)
        with zipfile.ZipFile(dist / "pf-examples-0.0.1.zip", "w") as archive:
            for name, content in [("README.md", "examples\n"), ("spec.json", json.dumps(spec)),
                                  ("examples/hello/main.roc", app)]:
                archive.writestr("pf-examples-0.0.1/" + name, content)
        ran = self.run_tool("test-archive")
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertIn("SERVE", ran.stdout)
        self.assertIn("1 tested, 0 failed", ran.stdout)
