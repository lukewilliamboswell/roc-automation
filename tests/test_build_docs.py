import importlib.util
import argparse
import subprocess
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ACTION = Path(__file__).resolve().parents[1] / "actions" / "build-docs" / "build_docs.py"
SPEC = importlib.util.spec_from_file_location("build_docs", ACTION)
build_docs = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(build_docs)


class BuildDocsTests(unittest.TestCase):
    def test_paths_must_be_relative_and_contained(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "docs").mkdir()
            self.assertEqual(build_docs.contained_path(root, "docs", "docs"), root / "docs")
            for value in ("", "/tmp", "../outside"):
                with self.subTest(value=value), self.assertRaises(SystemExit):
                    build_docs.contained_path(root, value, "docs")

    def test_api_generation_uses_an_argument_vector_and_requires_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary).resolve()
            source = workspace / "platform" / "main.roc"
            source.parent.mkdir()
            source.write_text("platform \"\"\n", encoding="utf-8")
            site = workspace / "site"
            site.mkdir()
            args = type("Args", (), {
                "api_entrypoint": "platform/main.roc",
                "roc_command": "roc",
            })()

            def generate(*command, cwd):
                output = Path(next(part.split("=", 1)[1] for part in command if part.startswith("--output=")))
                output.mkdir()
                (output / "index.html").write_text("api", encoding="utf-8")

            with patch.object(build_docs.shutil, "which", return_value="/bin/roc"), \
                 patch.object(build_docs, "run", side_effect=generate) as invoked:
                build_docs.build_api(args, workspace, site)
            self.assertEqual(invoked.call_args.args[:3], ("/bin/roc", "docs", str(source)))

    def test_github_outputs_are_absolute_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            output = root / "github-output"
            site, pdf = root / "site", root / "manual.pdf"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
                build_docs.write_outputs(site, pdf)
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                f"site-directory={site}\npdf-path={pdf}\n",
            )


class LegacyBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / "docs").mkdir()
        (self.root / "docs/index.adoc").write_text("= Manual\n")
        self.output = self.root / ".docs-out"
        self.output.mkdir()
        (self.output / "old-output").write_text("keep until success")
        self.args = argparse.Namespace(
            docs_directory="docs", entrypoint="index.adoc", output_directory=".docs-out",
            pdf_filename="book.pdf", docs_version="v42", pdf_image_dpi=144,
            api_entrypoint="", roc_command="roc", theme_directory="", backend="legacy",
            title="", author="", language="")
        self.commands = []
        self.fail_at = ""

    def invoke(self, *command, cwd):
        self.commands.append(command)
        stage = command[1]
        if stage == "run":
            destination = self.root / command[command.index("--output-directory") + 1]
            (destination / "site").mkdir()
            (destination / "site/index.html").write_text("rendered legacy fixture")
            (destination / "book.pdf").write_bytes(b"%PDF-fixture")
            (destination / "site/book.pdf").write_bytes(b"%PDF-fixture")
        if self.fail_at == stage:
            raise subprocess.CalledProcessError(1, command)

    def build(self):
        with patch.object(build_docs.shutil, "which", return_value="/reviewed/docker"), \
             patch.object(build_docs, "run", side_effect=self.invoke):
            return build_docs.build(self.args, self.root)

    def assert_preserved(self):
        self.assertEqual((self.output / "old-output").read_text(), "keep until success")
        self.assertEqual(list(self.root.glob(".roc-docs-*")), [])

    def test_legacy_build_publishes_only_completed_site_and_pdf(self):
        site, pdf = self.build()
        self.assertEqual(site, self.output / "site")
        self.assertEqual(pdf, self.output / "book.pdf")
        self.assertEqual(sorted(p.name for p in self.output.iterdir()), ["book.pdf", "site"])
        self.assertEqual((site / "book.pdf").read_bytes(), pdf.read_bytes())
        self.assertEqual([command[:2] for command in self.commands], [("docker", "build"), ("docker", "run")])

    def test_failed_image_build_and_render_preserve_existing_output(self):
        for stage in ("build", "run"):
            with self.subTest(stage=stage):
                self.fail_at = stage
                with self.assertRaises(subprocess.CalledProcessError):
                    self.build()
                self.assert_preserved()

    def test_failed_api_preserves_existing_output(self):
        with patch.object(build_docs, "build_api", side_effect=SystemExit("API failed")):
            with self.assertRaisesRegex(SystemExit, "API failed"):
                self.build()
        self.assert_preserved()

    def test_python_adapter_cannot_run_native_builds(self):
        for backend in (None, "roc"):
            self.args.backend = backend
            with self.subTest(backend=backend), self.assertRaisesRegex(SystemExit, "only --backend legacy"):
                self.build()
        self.assertEqual(self.commands, [])
        self.assert_preserved()

    def test_missing_docker_has_no_fallback(self):
        with patch.object(build_docs.shutil, "which", return_value=None), patch.object(build_docs, "run") as run:
            with self.assertRaisesRegex(SystemExit, "explicit legacy"):
                build_docs.build(self.args, self.root)
        run.assert_not_called()
        self.assert_preserved()

    def test_source_symlinks_are_rejected(self):
        (self.root / "secret").write_text("outside docs")
        (self.root / "docs/secret").symlink_to(self.root / "secret")
        with self.assertRaisesRegex(SystemExit, "symlink"):
            self.build()
        self.assert_preserved()

    def test_symlinked_path_components_are_rejected(self):
        (self.root / "alias").symlink_to(self.root / "docs", target_is_directory=True)
        self.args.docs_directory = "alias"
        with self.assertRaisesRegex(SystemExit, "symlink"):
            self.build()
        self.assert_preserved()

    def test_output_cannot_overwrite_sources_or_workspace(self):
        for output in (".", "docs", "docs/output", "../outside", ".git/output"):
            with self.subTest(output=output):
                self.args.output_directory = output
                with self.assertRaises(SystemExit):
                    self.build()
        self.assert_preserved()

    def test_output_cannot_overwrite_api_sources(self):
        (self.root / "package").mkdir()
        (self.root / "package/main.roc").write_text("package [] {}")
        self.args.api_entrypoint = "package/main.roc"
        self.args.output_directory = "package"
        with self.assertRaisesRegex(SystemExit, "overlap"):
            self.build()
        self.assertTrue((self.root / "package/main.roc").is_file())
        self.assert_preserved()

    def test_invalid_pdf_filename_rejected_before_any_stage(self):
        for name in ("../manual.pdf", "bad\n.pdf", "manual.txt", "/tmp/manual.pdf"):
            self.args.pdf_filename = name
            with self.subTest(name=name), self.assertRaisesRegex(SystemExit, "pdf-filename"):
                self.build()
        self.assertEqual(self.commands, [])
        self.assert_preserved()

    def test_failed_publish_restores_previous_directory(self):
        staged = self.root / "staged"
        staged.mkdir()
        original = Path.rename
        def rename(source, destination):
            if source == staged:
                raise OSError("rename failed")
            return original(source, destination)
        with patch.object(Path, "rename", rename):
            with self.assertRaisesRegex(OSError, "rename failed"):
                build_docs.publish(staged, self.output)
        self.assert_preserved()


class NativeActionShellTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.script = ACTION.with_name("build_docs.sh")
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
            "--roc-command", "roc",
        ]
        result = self.invoke(*arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), ["build", *arguments])
        self.assertEqual(self.outputs.read_text(),
                         "site-directory=/absolute/site\npdf-path=/absolute/manual.pdf\n")
        self.assertNotIn("python", result.stderr.lower())

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

    def test_native_theme_migration_is_explicit(self):
        result = self.invoke("--theme-directory", "theme")
        self.assertEqual(result.returncode, 2)
        self.assertIn("backend legacy", result.stderr)
        self.assertFalse(self.record.exists())

    def test_invalid_options_and_missing_values_do_not_execute_engine(self):
        for arguments in (("--backend", "other"), ("--workspace",), ("--unexpected", "x")):
            with self.subTest(arguments=arguments):
                result = self.invoke(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(self.record.exists())

    def test_explicit_legacy_backend_calls_compatibility_adapter(self):
        python = self.root / "python3"
        python.write_bytes(self.binary.read_bytes())
        python.chmod(0o755)
        result = self.invoke("--backend", "legacy", "--workspace", "/workspace", "--theme-directory", "theme")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), [str(ACTION), "--backend", "legacy", "--theme-directory", "theme",
                                           "--workspace", "/workspace"])

    def test_only_explicit_legacy_backend_requires_python(self):
        result = self.invoke("--backend", "legacy")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Python 3 is required for the explicit legacy backend", result.stderr)
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
            "/bin/bash", str(ACTION.with_name("build_docs.sh")),
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
        self.invoke("index.adoc")
        site = self.root / ".docs-out/site"
        pdf = self.root / ".docs-out/project.pdf"
        self.assertIn("Version 42", (site / "usage.html").read_text())
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
        self.invoke("index.md")
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
