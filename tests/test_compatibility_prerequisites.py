"""Exercise the composite action's Windows prerequisite admission step."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("bash"), "requires the action's bash shell")
class CompatibilityPrerequisiteTests(unittest.TestCase):
    def test_windows_accepts_blueprint_but_rejects_nix(self):
        source = (ROOT / "actions/release-compatibility/action.yml").read_text()
        step = source.split("    - name: Validate native prerequisite support\n", 1)[1].split("    # Build", 1)[0]
        commands = step.split("      run: |\n", 1)[1]
        commands = "\n".join(line[8:] for line in commands.splitlines())
        for blueprint in ("true", "false"):
            for nix in ("true", "false"):
                with self.subTest(blueprint=blueprint, nix=nix):
                    ran = subprocess.run([shutil.which("bash"), "-euc", commands], env={
                        **os.environ, "RUNNER_OS": "Windows", "INSTALL_BLUEPRINT": blueprint,
                        "INSTALL_NIX": nix}, capture_output=True, text=True)
                    self.assertEqual(ran.returncode, 1 if nix == "true" else 0, ran.stdout + ran.stderr)
