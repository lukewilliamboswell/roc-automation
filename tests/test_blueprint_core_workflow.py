"""Exercise the pinned wrapper's fixed-Core prerequisite boundary as shell data."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "actions/release-compatibility/action.yml"


def validation_shell():
    source = ACTION.read_text().split("    - name: Validate native prerequisite support\n", 1)[1]
    source = source.split("      run: |\n", 1)[1].split("    # Build the controller", 1)[0]
    return textwrap.dedent(source)


def composition_shell():
    lines = ACTION.read_text().splitlines()
    start = lines.index('        if [[ -n "$RA_BLUEPRINT_CORE" ]]; then')
    end = lines.index("        fi", start) + 1
    return "\n".join(line[8:] for line in lines[start:end]) + "\n"


class BlueprintCoreWorkflowTests(unittest.TestCase):
    def test_fixed_core_is_test_only_and_cannot_replace_an_existing_hook(self):
        cases = [
            ("", "test", '["custom-hook"]', "Linux", False, True),
            ("0.12.0", "test", "[]", "Linux", False, True),
            ("0.12.0", "test", " [ \n ] ", "Windows", False, True),
            ("0.12.0", "test", "[]", "Windows", True, False),
            ("0.12.0", "select", "[]", "Linux", False, False),
            ("0.12.0", "aggregate", "[]", "Linux", False, False),
            ("0.12.0", "test", '["existing-hook"]', "Linux", False, False),
            ("0.12.0", "test", "{}", "Linux", False, False),
            ("0.13.0", "test", "[]", "Linux", False, False),
            ('0.12.0"; touch injected; #', "test", "[]", "Linux", False, False),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            for version, phase, setup, system, install, allowed in cases:
                with self.subTest(version=version, phase=phase, setup=setup, system=system):
                    result = subprocess.run(
                        ["bash", "-c", validation_shell()], cwd=temporary,
                        env={**os.environ, "CORE_VERSION": version, "CORE_PHASE": phase,
                             "CORE_SETUP": setup, "RUNNER_OS": system,
                             "INSTALL_BLUEPRINT": str(install).lower(), "INSTALL_NIX": "false"},
                        capture_output=True, text=True, timeout=10,
                    )
                    self.assertEqual(result.returncode == 0, allowed, result.stdout + result.stderr)
                    self.assertFalse((Path(temporary) / "injected").exists())

    def test_core_argv_uses_pinned_helper_and_actual_compiler_without_shell_expansion(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            action = root / "pinned action 'space' $(touch injected)" / "release-compatibility"
            action.mkdir(parents=True)
            compiler = "/trusted compiler/roc$(touch injected)"
            for version in ("", "0.12.0"):
                original = '["custom-hook","literal $(touch injected)"]'
                result = subprocess.run(
                    ["bash", "-c", composition_shell()
                     + '\n"$RA_PYTHON" -c \'import os; print(os.environ["RA_SETUP"])\'\n'],
                    cwd=root, env={**os.environ, "RA_BLUEPRINT_CORE": version,
                                   "RA_SETUP": original, "RA_PYTHON": sys.executable,
                                   "ACTION_PATH": str(action), "RA_COMPILER": compiler},
                    capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                if version:
                    expected = [sys.executable, str(action.parent / "setup-blueprint/core_sources.py"),
                                "--version", version, "--compiler", compiler]
                    self.assertEqual(json.loads(result.stdout), expected)
                else:
                    self.assertEqual(result.stdout.strip(), original)
                self.assertFalse((root / "injected").exists())

    def test_reusable_workflow_passes_core_only_into_read_only_test_phase(self):
        source = (ROOT / ".github/workflows/nightly-compatibility.yml").read_text()
        select = source.split("\n  select:\n", 1)[1].split("\n  test:\n", 1)[0]
        test = source.split("\n  test:\n", 1)[1].split("\n  aggregate:\n", 1)[0]
        aggregate = source.split("\n  aggregate:\n", 1)[1].split("\n  report:\n", 1)[0]
        report = source.split("\n  report:\n", 1)[1]
        self.assertIn("blueprint-core-version:", test)
        self.assertIn("contents: read", test)
        self.assertNotIn(": write", test)
        for other in (select, aggregate, report):
            self.assertNotIn("blueprint-core-version:", other)
        action = ACTION.read_text()
        self.assertLess(action.index("Validate native prerequisite support"),
                        action.index("uses: roc-lang/setup-roc@"))
        self.assertLess(action.index('if [[ -n "$RA_BLUEPRINT_CORE" ]]'),
                        action.index('"$RA_DIRECTORY/runner" "$RA_PHASE"'))


if __name__ == "__main__":
    unittest.main()
