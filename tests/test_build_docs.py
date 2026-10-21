import subprocess
import os
import tempfile
import unittest
from pathlib import Path


ACTION = Path(__file__).resolve().parents[1] / "actions" / "build-docs" / "build_docs.sh"


class NativeActionShellTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.script = ACTION
        self.binary = self.root / "roc-docs"
        self.record = self.root / "arguments"
        self.outputs = self.root / "github output"
        self.binary.write_text(
            '#!/bin/bash\n'
            'printf "%s\\0" "$@" > "$RECORD"\n'
            'printf "site-directory=/absolute/site\\npdf-path=/absolute/manual.pdf\\n" >> "$GITHUB_OUTPUT"\n'
            'exit "${FAKE_STATUS:-0}"\n')
        self.binary.chmod(0o755)
        self.environment = {
            "PATH": str(self.root), "RECORD": str(self.record), "GITHUB_OUTPUT": str(self.outputs),
        }

    def invoke(self, *arguments, **environment):
        return subprocess.run(
            ["/bin/bash", str(self.script), *arguments], env={**self.environment, **environment},
            capture_output=True, text=True)

    def recorded(self):
        return self.record.read_bytes().decode().rstrip("\0").split("\0")

    def test_default_action_executes_native_build_without_python_or_docker(self):
        arguments = [
            "--workspace", "/workspace with spaces", "--docs-directory", "docs",
            "--entrypoint", "index.adoc", "--output-directory", ".docs-out",
            "--pdf-filename", "manual.pdf", "--docs-version", "v1",
            "--pdf-image-dpi", "144", "--title", "Title $(touch sentinel)",
            "--author", "Author", "--language", "en", "--api-entrypoint", "",
            "--roc-command", "roc", "--site-url", "https://example.com/manual/", "--markdown", "true",
        ]
        result = self.invoke(*arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), ["build", *arguments])
        self.assertEqual(self.outputs.read_text(),
                         "site-directory=/absolute/site\npdf-path=/absolute/manual.pdf\n")
        self.assertNotIn("python", result.stderr.lower())

    def test_disabled_markdown_uses_existing_binary_interface(self):
        result = self.invoke("--markdown", "false")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), ["build"])

    def test_explicit_renderer_wins_over_environment_and_path(self):
        result = self.invoke("--renderer-binary", str(self.binary), ROC_DOCS_BINARY="missing")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), ["build"])

    def test_empty_action_input_preserves_environment_renderer(self):
        result = self.invoke("--renderer-binary", "", ROC_DOCS_BINARY=str(self.binary))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_binary_fails_without_bootstrap_or_fallback(self):
        result = self.invoke("--renderer-binary", "not-installed")
        self.assertEqual(result.returncode, 2)
        self.assertIn("precompiled roc-docs not found", result.stderr)
        self.assertFalse(self.record.exists())

    def test_engine_failure_status_is_preserved(self):
        result = self.invoke(FAKE_STATUS="7")
        self.assertEqual(result.returncode, 7)

    def test_invalid_options_and_missing_values_do_not_execute_engine(self):
        for arguments in (("--backend", "legacy"), ("--backend", "roc"),
                          ("--theme-directory", "theme"), ("--renderer-binary",),
                          ("--workspace",), ("--unexpected", "x"), ("--markdown",),
                          ("--markdown", "yes"), ("--markdown", "true", "--markdown", "false")):
            with self.subTest(arguments=arguments):
                result = self.invoke(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(self.record.exists())


@unittest.skipUnless(os.environ.get("ROC_DOCS_ACTION_TEST_BINARY"), "set ROC_DOCS_ACTION_TEST_BINARY for native action integration")
class NativeActionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.docs = self.root / "docs"
        self.docs.mkdir()
        self.binary = str(Path(os.environ["ROC_DOCS_ACTION_TEST_BINARY"]).resolve())
        self.outputs = self.root / "github-output"

    def invoke(self, entrypoint, *extra, success=True):
        result = subprocess.run([
            "/bin/bash", str(ACTION),
            "--renderer-binary", self.binary, "--workspace", str(self.root),
            "--entrypoint", entrypoint, "--pdf-filename", "project.pdf", "--docs-version", "42",
            "--pdf-image-dpi", "144", "--title", "", "--author", "", "--language", "",
            "--api-entrypoint", "", "--roc-command", "roc", *extra],
            env={**os.environ, "PATH": "/nonexistent", "GITHUB_OUTPUT": str(self.outputs)},
            capture_output=True, text=True, timeout=90)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def snapshot(self):
        output = self.root / ".docs-out"
        return {str(p.relative_to(output)): p.read_bytes() for p in output.rglob("*") if p.is_file()}

    def test_native_asciidoc_action_outputs_and_rejection_preserve_publication(self):
        (self.docs / "index.adoc").write_text("= Project\n\ninclude::usage.adoc[leveloffset=+1]\n")
        (self.docs / "usage.adoc").write_text("= Usage\n\nVersion {docs-version}.\n\n[source,roc]\n----\napp [main!] {}\n----\n")
        self.invoke("index.adoc", "--markdown", "true")
        site = self.root / ".docs-out/site"
        pdf = self.root / ".docs-out/project.pdf"
        self.assertIn("Version 42", (site / "usage.html").read_text())
        self.assertIn("Version 42", (site / "usage.md").read_text())
        self.assertIn("usage.md", (site / "llms.txt").read_text())
        self.assertIn("syntax-keyword", (site / "usage.html").read_text())
        self.assertTrue(pdf.read_bytes().startswith(b"%PDF-2.0"))
        self.assertEqual(pdf.read_bytes(), (site / "project.pdf").read_bytes())
        expected_outputs = f"site-directory={site}\npdf-path={pdf}\n"
        self.assertEqual(self.outputs.read_text(), expected_outputs)
        before = self.snapshot()
        (self.docs / "index.adoc").write_text("= Project\n\ninclude::missing.adoc[]\n")
        self.invoke("index.adoc", success=False)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.outputs.read_text(), expected_outputs)

    def test_native_markdown_and_explicit_failed_api_preserve_publication(self):
        (self.docs / "index.md").write_text("# Project\n\n```roc\napp [main!] {}\n```\n")
        self.invoke("index.md", "--markdown", "false")
        before = self.snapshot()
        previous_outputs = self.outputs.read_text()
        (self.root / "main.roc").write_text("placeholder")
        compiler = self.root / "failing-compiler"
        compiler.write_text("#!/bin/bash\nexit 1\n")
        compiler.chmod(0o755)
        self.invoke("index.md", "--api-entrypoint", "main.roc", "--roc-command", str(compiler), success=False)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.outputs.read_text(), previous_outputs)


if __name__ == "__main__":
    unittest.main()
