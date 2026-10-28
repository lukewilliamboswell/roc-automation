"""Exercise working-tree baseline updates with the shared release utility."""
import json
import os
import subprocess
import unittest

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

