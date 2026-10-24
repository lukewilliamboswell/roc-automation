"""Run the actual release-workflow harness in an isolated artifact-only tree."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BINARY = os.environ.get("ROC_COMPATIBILITY_BINARY")


@unittest.skipUnless(BINARY, "set ROC_COMPATIBILITY_BINARY to verify standalone artifact packaging")
class CompatibilityPackagingTests(unittest.TestCase):
    def test_published_binary_and_companion_helper_work_without_source_checkout(self):
        source = (ROOT / ".github/workflows/release-tools.yml").read_text()
        step = source.split("      - name: Include the compatibility utility standalone regression harness\n", 1)[1].split("      - name:", 1)[0]
        commands = step.split("        run: |\n", 1)[1]
        commands = "\n".join(line[10:] for line in commands.splitlines())
        with tempfile.TemporaryDirectory() as temporary:
            staging = Path(temporary) / "staging"
            staging.mkdir()
            for relative in ("tests/test_release_compatibility.py", "tests/test_compatibility_archive.py",
                             "tests/fixtures/compatibility_example.py", "tests/fixtures/compatibility_launcher.c"):
                destination = staging / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(ROOT / relative, destination)
            (staging / "dist").mkdir()
            shutil.copy(ROOT / "actions/release-compatibility/archive.py", staging / "dist/release-compatibility-archive.py")
            bash = shutil.which("bash")
            if os.name == "nt":
                # Actions' bash shell is Git Bash. A bare subprocess lookup can
                # select System32\\bash.exe (the unrelated WSL launcher).
                git = Path(shutil.which("git")).resolve()
                candidates = [git.parent / "bash.exe", git.parent.parent / "bin/bash.exe",
                              git.parent.parent.parent / "bin/bash.exe"]
                bash = next((str(candidate) for candidate in candidates if candidate.is_file()), None)
            self.assertIsNotNone(bash, "the release packaging simulation requires Git Bash")
            packaged = subprocess.run([bash, "-euc", commands], cwd=staging, capture_output=True, text=True)
            self.assertEqual(packaged.returncode, 0, packaged.stdout + packaged.stderr)
            artifact = Path(temporary) / "downloaded-artifact"
            artifact.mkdir()
            shutil.move(staging / "compatibility-tests", artifact / "compatibility-tests")
            shutil.move(staging / "dist", artifact / "dist")
            executable = artifact / "dist" / ("release-compatibility.exe" if os.name == "nt" else "release-compatibility")
            shutil.copy(BINARY, executable)
            executable.chmod(0o755)
            shutil.rmtree(staging)
            env = {**os.environ, "ROC_COMPATIBILITY_BINARY": str(executable),
                   "ROC_COMPATIBILITY_ARCHIVE_HELPER": str(artifact / "dist/release-compatibility-archive.py")}
            ran = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s",
                                  str(artifact / "compatibility-tests/tests"), "-v"],
                                 cwd=artifact, env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            discovered = unittest.TestSuite(unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=pattern)
                                            for pattern in ("test_release_compatibility.py", "test_compatibility_archive.py"))
            self.assertIn(f"Ran {discovered.countTestCases()} tests", ran.stderr)
            # Track the suite's declared platform skips rather than freezing a
            # count that goes stale when new portable regressions are added.
            # Runtime SkipTest calls remain unexpected and fail this comparison.
            def cases(suite):
                for case in suite:
                    if isinstance(case, unittest.TestSuite):
                        yield from cases(case)
                    else:
                        yield case
            expected_skips = []
            for case in cases(discovered):
                method = getattr(case, case._testMethodName)
                skipped = type(case) if getattr(type(case), "__unittest_skip__", False) else method
                if getattr(skipped, "__unittest_skip__", False):
                    expected_skips.append((case, skipped.__unittest_skip_why__))
            if expected_skips:
                self.assertIn(f"OK (skipped={len(expected_skips)})", ran.stderr)
                for case, reason in expected_skips:
                    self.assertIn(f"{case.id()}) ... skipped {reason!r}", ran.stderr)
            else:
                self.assertNotRegex(ran.stderr, r"\.\.\. skipped|\(skipped=")
            self.assertFalse((artifact / "actions").exists())
            self.assertFalse((artifact / "scripts").exists())
            self.assertFalse((artifact / ".github").exists())


if __name__ == "__main__":
    unittest.main()
