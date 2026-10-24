"""Run the real action guards/relocation around inert compiler bytes."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "actions/setup-package-compiler/action.yml"
BLOCKS = [textwrap.dedent(match) for match in re.findall(r"(?m)^      run: \|\n((?:^        .*\n|^\n)+)", ACTION.read_text())]


class PackageCompilerActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launcher = None
        if os.name == "nt":
            cls.launcher_temp = tempfile.TemporaryDirectory()
            cls.addClassCleanup(cls.launcher_temp.cleanup)
            cls.launcher = Path(cls.launcher_temp.name) / "launcher.exe"
            subprocess.run(["clang", str(ROOT / "tests/fixtures/compatibility_launcher.c"), "-o", str(cls.launcher)],
                           check=True, capture_output=True, text=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="compiler action ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.runner = self.root / "runner temp"
        self.runner.mkdir()
        self.files = {}
        for name in ("outputs", "path", "env"):
            path = self.root / name
            path.write_text("")
            self.files[name] = path
        self.env = {**os.environ, "GITHUB_WORKSPACE": str(self.workspace), "RUNNER_TEMP": str(self.runner),
                    "GITHUB_OUTPUT": str(self.files["outputs"]), "GITHUB_PATH": str(self.files["path"]),
                    "GITHUB_ENV": str(self.files["env"]), "RUNNER_OS": "Windows" if os.name == "nt" else "Linux",
                    "NIGHTLY_TAG": "nightly-2026-10-23-18478ff", "FAKE_PYTHON": sys.executable}
        self.assertEqual(len(BLOCKS), 2)

    def invoke(self, index, **extra):
        return subprocess.run(["bash", "-c", BLOCKS[index]], cwd=self.workspace,
                              env={**self.env, **extra}, capture_output=True, text=True, timeout=15)

    def test_existing_workspace_entry_cannot_shadow_the_selected_download(self):
        for kind in ("file", "directory"):
            entry = self.workspace / "roc_nightly-candidate-controlled"
            if kind == "file":
                entry.write_text("candidate bytes")
            else:
                entry.mkdir()
            ran = self.invoke(0)
            self.assertNotEqual(ran.returncode, 0)
            self.assertIn("could shadow", ran.stderr)
            if kind == "file":
                self.assertEqual(entry.read_text(), "candidate bytes")
                entry.unlink()
            else:
                entry.rmdir()
        self.assertEqual(self.invoke(0).returncode, 0)

    def test_only_exact_safe_nightly_tags_reach_the_upstream_installer(self):
        for tag in ("latest", "nightly-new-compiler", "nightly-2026-10-23-18478ff;echo unsafe", "nightly-2026-10-23-18478ff\nextra"):
            with self.subTest(tag=tag):
                ran = self.invoke(0, NIGHTLY_TAG=tag)
                self.assertNotEqual(ran.returncode, 0)
                self.assertIn("exact resolved nightly", ran.stderr)

    def test_relocation_retains_native_absolute_compiler_and_matching_glue(self):
        installation = self.workspace / "roc_nightly-inert"
        installation.mkdir()
        script = installation / ("roc.py" if self.launcher else "roc")
        script.write_text("#!/usr/bin/env python3\nimport sys\nsys.stdout.write('Roc compiler version nightly-2026-10-23-18478ff')\n")
        script.chmod(0o755)
        if self.launcher:
            shutil.copy(self.launcher, installation / "roc.exe")
        glue = installation / "glue"
        glue.mkdir()
        (glue / "CGlue.roc").write_text("inert source")
        ran = self.invoke(1, PATH=str(installation)+os.pathsep+os.environ.get("PATH", ""))
        self.assertEqual(ran.returncode, 0, ran.stdout+ran.stderr)
        values = dict(line.split("=", 1) for line in self.files["outputs"].read_text().splitlines())
        compiler = Path(values["compiler-path"])
        self.assertTrue(compiler.is_absolute())
        self.assertTrue(compiler.is_file())
        self.assertFalse(installation.exists())
        self.assertEqual(values["version"], "Roc compiler version nightly-2026-10-23-18478ff")
        exported = self.files["env"].read_text()
        self.assertIn("ROC_C_GLUE="+str(compiler.parent).replace("\\", "/")+"/glue/CGlue.roc", exported.replace("\\", "/"))
        self.assertIn("ROC_RUST_GLUE=\n", exported)
        self.assertIn("ROC_ZIG_GLUE=\n", exported)

    def test_global_or_unexpected_compiler_directory_is_not_moved(self):
        global_bin = self.root / "global-bin"
        global_bin.mkdir()
        compiler = global_bin / "roc"
        compiler.write_text("#!/bin/sh\nexit 0\n")
        compiler.chmod(0o755)
        ran = self.invoke(1, PATH=str(global_bin)+os.pathsep+os.environ.get("PATH", ""))
        self.assertNotEqual(ran.returncode, 0)
        self.assertTrue(compiler.exists())
        self.assertEqual(self.files["outputs"].read_text(), "")


if __name__ == "__main__":
    unittest.main()
