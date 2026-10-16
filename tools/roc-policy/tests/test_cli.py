"""Behavioral tests of the compiled utility. No compiler is invoked here."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


BINARY = Path(os.environ.get("ROC_LINT_BINARY", "/tmp/roc-lint")).resolve()
PIN = "nightly-2026-10-09-258ab27"
SOURCE = 'app [main!] { cli: platform "https://example.com/cli.tar.zst", ansi: "../package/main.roc" }\nmain! = |_| Ok({})\n'
RULE = '{kind: Dependency, scope: "examples", name: "ansi", value: "../package/main.roc"}'


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "examples").mkdir()
        (self.root / "examples/main.roc").write_text(SOURCE)
        self.configure(RULE)

    def configure(self, rules, extra=""):
        (self.root / "lint.roc").write_text(
            'lint = {schema_version: 1, roots: ["examples"], rules: ['
            + rules + ']' + extra + '}\n'
        )

    def run_lint(self, expected=0):
        # A minimal PATH proves the executable does not start roc, Python, Git,
        # Nix, or any other external helper. The test harness itself uses Python.
        result = subprocess.run(
            [str(BINARY), str(self.root), "lint.roc"],
            capture_output=True, text=True, env={"PATH": "/nonexistent"},
            timeout=30,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def test_matching_dependency_and_no_external_helpers(self):
        self.assertIn("1 files, 0 findings", self.run_lint())

    def test_wrong_published_dependency(self):
        path = self.root / "examples/main.roc"
        path.write_text(SOURCE.replace("../package/main.roc", "https://example.com/old.tar.zst"))
        before = path.read_bytes()
        self.assertIn("project/dependency", self.run_lint(1))
        self.assertEqual(path.read_bytes(), before)

    def test_missing_dependency(self):
        (self.root / "examples/main.roc").write_text('app [main!] { cli: platform "https://example.com/cli.tar.zst" }\n')
        self.assertIn("found []", self.run_lint(1))

    def test_platform_dependency(self):
        self.configure('{kind: Dependency, scope: "examples", name: "cli", value: "https://example.com/cli.tar.zst"}')
        self.run_lint()

    def test_compiler_policies(self):
        self.configure('{kind: NoCompilerPin, scope: "examples"}')
        self.run_lint()
        path = self.root / "examples/main.roc"
        path.write_text(SOURCE.replace('ansi:', f'roc: "{PIN}", ansi:'))
        self.assertIn("compiler pin forbidden", self.run_lint(1))
        self.configure('{kind: CompilerPin, scope: "examples", value: "' + PIN + '"}')
        self.run_lint()

    def test_comments_and_strings_do_not_create_imports_or_pins(self):
        self.configure('{kind: ForbiddenImport, scope: "examples", name: "Internal"}, {kind: NoCompilerPin, scope: "examples"}')
        path = self.root / "examples/main.roc"
        path.write_text(SOURCE + '# import Internal.Secret; roc: "wrong"\ntext = "import Internal.Secret"\n')
        self.run_lint()
        path.write_text(path.read_text() + "import Internal.Secret as Secret\n")
        self.assertIn("architecture/import-boundary", self.run_lint(1))

    def test_scope_matches_path_segments(self):
        self.configure('{kind: ForbiddenImport, scope: "example", name: "Internal"}')
        (self.root / "examples/main.roc").write_text("import Internal.Secret\n")
        self.run_lint()

    def test_import_trivia_and_local_paths(self):
        path = self.root / "examples/main.roc"
        self.configure('{kind: ForbiddenImport, scope: "examples", name: "Internal"}')
        path.write_text("import Internal .Secret\n")
        self.assertIn("architecture/import-boundary", self.run_lint(1))
        self.configure('{kind: ForbiddenImport, scope: "examples", name: "support/Internal"}')
        path.write_text("import support/Internal/Secret\n")
        self.assertIn("architecture/import-boundary", self.run_lint(1))

    def test_unicode_columns(self):
        self.configure('{kind: ForbiddenImport, scope: "examples", name: "Internal"}')
        (self.root / "examples/main.roc").write_text('# é🙂\nimport Internal.Secret\n')
        self.assertIn("main.roc:2:8:", self.run_lint(1))

    def test_incomplete_and_invalid_utf8_are_findings(self):
        path = self.root / "examples/main.roc"
        for source in [b"x = [1,\n", b"# \xff\nx = 1\n"]:
            with self.subTest(source=source):
                path.write_bytes(source)
                self.assertIn("syntax/incomplete", self.run_lint(1))

    def test_reject_config_computation_and_unknown_or_duplicate_fields(self):
        for suffix in [", other: 1", ", schema_version: 1"]:
            self.configure(RULE, suffix)
            self.run_lint(2)
        for expression in ['make_config()', '{schema_version: 1 + 0, roots: [], rules: []}', '{schema_version: 1, roots: ["${crash "no"}"], rules: []}']:
            with self.subTest(expression=expression):
                (self.root / "lint.roc").write_text("lint = " + expression)
                self.run_lint(2)
        (self.root / "lint.roc").write_text('import Config\nlint = {}\n')
        self.run_lint(2)

    def test_reject_unknown_rule_and_irrelevant_options(self):
        self.configure('{kind: Unknown, scope: "examples"}')
        self.run_lint(2)
        self.configure('{kind: NoCompilerPin, scope: "examples", value: "ignored"}')
        self.run_lint(2)

    def test_empty_selection_is_error(self):
        (self.root / "examples/main.roc").unlink()
        self.assertIn("no Roc files", self.run_lint(2))

    def test_escaping_symlink_is_error(self):
        with tempfile.TemporaryDirectory() as outside:
            (Path(outside) / "outside.roc").write_text("x = 1")
            (self.root / "examples/escape").symlink_to(outside, target_is_directory=True)
            self.assertIn("escapes root", self.run_lint(2))

    def test_symlink_cycle_is_error(self):
        (self.root / "examples/cycle").symlink_to(self.root / "examples", target_is_directory=True)
        self.assertIn("symlink", self.run_lint(2))

    def test_special_file_is_error(self):
        os.mkfifo(self.root / "examples/input.roc")
        self.assertIn("regular file", self.run_lint(2))

    def test_nested_source_limit_is_incomplete_analysis(self):
        (self.root / "examples/main.roc").write_text("x = " + "[" * 150 + "1" + "]" * 150)
        self.assertIn("syntax/incomplete", self.run_lint(1))

    def test_duplicate_dependency_is_incomplete_analysis(self):
        (self.root / "examples/main.roc").write_text(SOURCE.replace('ansi:', 'ansi: "other", ansi:'))
        self.assertIn("syntax/incomplete", self.run_lint(1))


if __name__ == "__main__":
    unittest.main()
