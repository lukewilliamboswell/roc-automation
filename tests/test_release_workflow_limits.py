"""Execute the source workflow's timeout validation before candidate code."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ReleaseWorkflowLimitsTests(unittest.TestCase):
    def test_default_override_and_rejected_values_use_the_actual_guard(self):
        source = (ROOT / ".github/workflows/release-package.yml").read_text()
        self.assertRegex(source, r"build-timeout-minutes:[\s\S]*?type: number\n        default: 30")
        build = source.split("  build:\n", 1)[1].split("  test:\n", 1)[0]
        self.assertIn("timeout-minutes: ${{ inputs.build-timeout-minutes }}", build)
        request = build.split("      - id: request\n", 1)[1].split("      - uses:", 1)[0]
        self.assertIn("BUILD_TIMEOUT: ${{ inputs.build-timeout-minutes }}", request)
        guard = textwrap.dedent(request.split("        run: |\n", 1)[1])
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("the workflow's request guard runs in bash")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            for value in ("1", "30", "75", "360", "0", "-1", "30.5", "361", "1000", "", "garbage", "99999999999999999999", "030"):
                with self.subTest(value=value):
                    output.unlink(missing_ok=True)
                    env = dict(os.environ, VERSION="", DEFAULT_BRANCH="main", BUILD_TIMEOUT=value, GITHUB_OUTPUT=str(output))
                    ran = subprocess.run([bash, "-euc", guard], env=env, capture_output=True, text=True)
                    accepted = value in ("1", "30", "75", "360")
                    self.assertEqual(ran.returncode == 0, accepted, ran.stdout + ran.stderr)
                    self.assertEqual(output.exists(), accepted)
        self.assertLess(build.index("id: request"), build.index("name: Check the package"))
        publish = source.split("  publish:\n", 1)[1]
        self.assertIn("needs: [build, test]", publish)
        self.assertNotIn("always()", publish.split("steps:", 1)[0])
