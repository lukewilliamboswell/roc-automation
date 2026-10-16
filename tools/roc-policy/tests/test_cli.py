"""Exercise exact compiled artifacts; stdlib harness, no source/toolchain required."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

BINARY = Path(os.environ.get("ROC_POLICY_BINARY", "/tmp/roc-policy")).resolve()
COMPILER = os.environ.get("ROC_POLICY_TEST_COMPILER")
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

    def configure(self, rules, extra="", prefix=""):
        (self.root / "Blueprint.roc").write_text(prefix +
            'repository_policy = {schema_version: 1, roots: ["examples"], rules: ['
            + rules + ']' + extra + '}\n')

    def invoke(self, command="check", expected=0, extra=(), env=None):
        result = subprocess.run(
            [str(BINARY), command, "--root", str(self.root), *extra],
            capture_output=True, text=True,
            env={"PATH": "/nonexistent", **(env or {})}, timeout=90,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def test_matching_dependency_without_helpers(self):
        self.assertIn("1 files, 0 findings", self.invoke().stdout)

    def test_wrong_dependency_is_read_only_finding(self):
        path = self.root / "examples/main.roc"
        path.write_text(SOURCE.replace("../package/main.roc", "https://example.com/old.tar.zst"))
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertIn("project/dependency", self.invoke(expected=1).stdout)
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_missing_dependency_and_platform_dependency(self):
        self.configure('{kind: Dependency, scope: "examples", name: "cli", value: "https://example.com/cli.tar.zst"}')
        self.invoke()
        self.configure('{kind: Dependency, scope: "examples", name: "missing", value: "../package/main.roc"}')
        self.assertIn("found []", self.invoke(expected=1).stdout)

    def test_compiler_pin_policies(self):
        self.configure('{kind: NoCompilerPin, scope: "examples"}')
        self.invoke()
        (self.root / "examples/main.roc").write_text(SOURCE.replace('ansi:', f'roc: "{PIN}", ansi:'))
        self.invoke(expected=1)
        self.configure('{kind: CompilerPin, scope: "examples", value: "' + PIN + '"}')
        self.invoke()

    def test_imports_ignore_comments_strings_and_handle_alias_trivia(self):
        self.configure('{kind: ForbiddenImport, scope: "examples", name: "Internal"}')
        path = self.root / "examples/main.roc"
        path.write_text(SOURCE + '# import Internal.Secret\ntext = "import Internal.Secret"\n')
        self.invoke()
        path.write_text(path.read_text() + "import Internal .Secret as Secret\n")
        self.assertIn("architecture/import-boundary", self.invoke(expected=1).stdout)

    def test_local_import_prefix(self):
        self.configure('{kind: ForbiddenImport, scope: "examples", name: "support/Internal"}')
        (self.root / "examples/main.roc").write_text("import support/Internal/Secret\n")
        self.invoke(expected=1)

    def test_no_vacuous_pass_when_scopes_match_nothing(self):
        self.configure('{kind: ForbiddenImport, scope: "example", name: "Internal"}')
        self.invoke(expected=2)

    def test_unicode_locations_and_json_spans(self):
        self.configure('{kind: ForbiddenImport, scope: "examples", name: "Internal"}')
        (self.root / "examples/main.roc").write_text('# é🙂\nimport Internal.Secret\n')
        report = json.loads(self.invoke(expected=1, extra=("--format", "json")).stdout)
        self.assertTrue(report["complete"])
        issue = report["issues"][0]
        self.assertEqual((issue["line"], issue["column"]), (2, 8))
        self.assertGreater(issue["start"], 8)

    def test_incomplete_and_invalid_utf8_are_analysis_errors(self):
        path = self.root / "examples/main.roc"
        for data in [b"x = [1,\n", b"# \xff\nx = 1\n", b"x = " + b"[" * 150 + b"1" + b"]" * 150]:
            with self.subTest(data=data[:30]):
                path.write_bytes(data)
                report = json.loads(self.invoke(expected=2, extra=("--format", "json")).stdout)
                self.assertFalse(report["complete"])

    def test_config_references_field_access_puns_and_unrelated_code(self):
        prefix = 'shared = {scope: "examples", entry: "../package/main.roc"}\nrule_scope = shared.scope\nunused = |x| x + 1\n'
        (self.root / "Blueprint.roc").write_text(prefix + 'repository_policy = {schema_version: 1, roots: [rule_scope], rules: [{kind: Dependency, scope: rule_scope, name: "ansi", value: shared.entry}]}\n')
        self.invoke()

    def test_policy_inside_complete_blueprint_uses_shared_compiler_fact(self):
        (self.root / "examples/main.roc").write_text(SOURCE.replace('ansi:', f'roc: "{PIN}", ansi:'))
        self.configure('{kind: CompilerPin, scope: "examples/main.roc", value: shared.compiler}', prefix='app [config] { pf: platform "https://example.com/blueprint.tar.zst" }\nimport pf.Recipes\nshared = {compiler: "' + PIN + '"}\nconfig = [Environment("dev", [Programs([Fetch("roc-stable", Recipes.roc_nightly(shared.compiler))])])]\n')
        self.invoke()

    def test_config_unknown_duplicate_cycle_and_execution_rejected(self):
        for suffix in [", other: 1", ", schema_version: 1"]:
            self.configure(RULE, suffix)
            self.invoke(expected=2)
        for expression in ['make_config()', '{schema_version: 1 + 0, roots: [], rules: []}', 'later']:
            with self.subTest(expression=expression):
                (self.root / "Blueprint.roc").write_text('later = repository_policy\nrepository_policy = ' + expression)
                self.invoke(expected=2)

    def test_config_rejects_unknown_rule_and_irrelevant_options(self):
        for rule in ['{kind: Unknown, scope: "examples"}', '{kind: NoCompilerPin, scope: "examples", value: "ignored"}']:
            self.configure(rule)
            self.invoke(expected=2)

    def test_cli_rejects_unknown_duplicate_or_irrelevant_flags(self):
        for extra in [("--wat", "x"), ("--root", "."), ("--report", "x"), ("--compiler", "/x"), ("--format", "yaml")]:
            self.invoke(expected=2, extra=extra)

    def test_explicit_config_and_definition(self):
        (self.root / "Blueprint.roc").rename(self.root / "policy.roc")
        p = self.root / "policy.roc"
        p.write_text(p.read_text().replace("repository_policy =", "settings ="))
        self.invoke(extra=("--config", "policy.roc", "--definition", "settings"))

    def test_empty_selection_missing_file_and_symlinks_fail(self):
        (self.root / "examples/main.roc").unlink()
        self.invoke(expected=2)
        (self.root / "examples/cycle").symlink_to(self.root / "examples", target_is_directory=True)
        self.assertIn("symlink", self.invoke(expected=2).stderr)

    def test_escaping_symlink_and_special_file_fail(self):
        with tempfile.TemporaryDirectory() as outside:
            (self.root / "examples/escape").symlink_to(outside, target_is_directory=True)
            self.invoke(expected=2)
        (self.root / "examples/escape").unlink()
        os.mkfifo(self.root / "examples/input.roc")
        self.assertIn("regular file", self.invoke(expected=2).stderr)

    def test_spelling_channels_glossary_and_custom_words(self):
        path = self.root / "examples/main.roc"
        path.write_text(SOURCE + '# teh frobnciate\ntext = "teh"\n')
        self.configure('{kind: Spelling, scope: "examples", channels: [Comments], words: [{word: "frobnciate", replacement: "frobnicate"}]}')
        output = self.invoke(expected=1).stdout
        self.assertIn("teh", output)
        self.assertIn("frobnciate", output)
        self.configure('{kind: Spelling, scope: "examples", channels: [Comments], allow: ["teh", "frobnciate"]}')
        self.invoke()

    def test_phrases_across_doc_lines_and_prose(self):
        (self.root / "examples/main.roc").write_text('## It is worth\n## noting this.\nx = 1\n')
        self.configure('{kind: Phrase, scope: "examples", phrase: "worth noting", replacement: "state the fact", channels: [Comments, Prose]}')
        self.invoke(expected=1)
        (self.root / "examples/main.roc").unlink()
        (self.root / "examples/usage.adoc").write_text("It is worth\nnoting this.")
        self.invoke(expected=1)

    def test_public_docs_modern_module(self):
        (self.root / "examples/main.roc").unlink()
        path = self.root / "examples/Thing.roc"
        path.write_text('## A thing.\nThing := [].{\n  value = 1\n}\n')
        self.configure('{kind: PublicDocs, scope: "examples"}')
        self.assertIn("documentation/public", self.invoke(expected=1).stdout)
        path.write_text('## A thing.\nThing := [].{\n  ## Its value.\n  value = 1\n}\n')
        self.invoke()

    def test_required_exact_entrypoint_header_cannot_disappear(self):
        self.configure('{kind: CompilerPin, scope: "examples/main.roc", value: "' + PIN + '"}')
        (self.root / "examples/main.roc").write_text("x = 1\n")
        self.assertIn("project/header", self.invoke(expected=1).stdout)

    def test_structural_rules_cannot_silently_check_only_prose(self):
        (self.root / "examples/main.roc").unlink()
        (self.root / "examples/usage.md").write_text("Hello")
        self.configure('{kind: PublicDocs, scope: "examples"}')
        self.invoke(expected=2)

    def test_prose_channel_cannot_silently_check_only_roc(self):
        self.configure('{kind: Spelling, scope: "examples", channels: [Prose]}')
        self.invoke(expected=2)

    def test_unmatchable_text_patterns_are_config_errors(self):
        self.configure('{kind: Phrase, scope: "examples", phrase: "!!!"}')
        self.invoke(expected=2)
        self.configure('{kind: Spelling, scope: "examples", words: [{word: "two words", replacement: "word"}]}')
        self.invoke(expected=2)

    def test_unresolved_public_surface_is_incomplete_not_clean(self):
        self.configure('{kind: PublicDocs, scope: "examples"}')
        (self.root / "examples/main.roc").write_text("module [Thing.*]\nimport Thing\n")
        report = json.loads(self.invoke(expected=2, extra=("--format", "json")).stdout)
        self.assertFalse(report["complete"])
        self.assertEqual(report["issues"][0]["rule"], "documentation/incomplete")

    def prepare_tests(self):
        (self.root / "examples/main.roc").write_text('add = |a, b| a + b\nexpect add(1, 2) == 3\nexpect add(2, 2) == 4\n')
        self.configure('')
        return json.loads(self.invoke("manifest").stdout)

    def receipt(self, report):
        (self.root / "receipt.json").write_text(json.dumps(report))
        return ("--report", "receipt.json")

    def completed_report(self):
        report = self.prepare_tests()
        for file in report["files"]:
            file["completed"] = True
            for test in file["tests"]:
                test["status"] = "passed"
        return report

    def test_manifest_and_exact_completed_receipt(self):
        report = self.completed_report()
        self.assertEqual(len(report["files"][0]["tests"]), 2)
        self.invoke("verify-tests", extra=self.receipt(report))

    def test_manifest_is_not_execution_evidence(self):
        report = self.prepare_tests()
        self.invoke("verify-tests", expected=2, extra=self.receipt(report))

    def test_receipts_detect_missing_duplicate_stale_failed_and_skipped(self):
        for mutation, expected in [
            (lambda r: r["files"][0]["tests"].pop(), 1),
            (lambda r: r["files"][0]["tests"].append(r["files"][0]["tests"][0].copy()), 2),
            (lambda r: r["files"][0].update(source=r["files"][0]["source"] + "# stale\n"), 1),
            (lambda r: r["files"][0]["tests"][0].update(status="failed"), 1),
            (lambda r: r["files"][0]["tests"][0].update(status="skipped"), 1),
        ]:
            report = self.completed_report()
            mutation(report)
            self.invoke("verify-tests", expected=expected, extra=self.receipt(report))

    def test_equal_counts_do_not_replace_identity_matching(self):
        report = self.completed_report()
        report["files"][0]["tests"][0]["start"] += 1
        output = self.invoke("verify-tests", expected=1, extra=self.receipt(report)).stdout
        self.assertIn("unexpected test identity", output)
        self.assertIn("expected test missing", output)

    def test_receipt_duplicate_json_keys_rejected(self):
        report = self.completed_report()
        p = self.root / "receipt.json"
        p.write_text(json.dumps(report).replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'))
        self.invoke("verify-tests", expected=2, extra=("--report", "receipt.json"))

    def test_no_test_inventory_is_error(self):
        self.configure('')
        self.invoke("manifest", expected=2)

    def fake_compiler(self, body):
        compiler = self.root / "runner.sh"
        compiler.write_text("#!/bin/sh\n" + body + "\n")
        compiler.chmod(0o755)
        return ("--compiler", str(compiler))

    def test_runner_count_gate_requires_fresh_successful_matching_summary(self):
        self.prepare_tests()
        runner = self.fake_compiler("printf 'All (2) tests passed in 0.1 ms.\\n'")
        self.assertIn("does not prove individual", self.invoke("run-tests", extra=runner).stdout)
        runner = self.fake_compiler("printf 'All (1) tests passed in 0.1 ms.\\n'")
        self.invoke("run-tests", expected=1, extra=runner)
        runner = self.fake_compiler("printf 'All (2) tests passed in 0.1 ms. (cached)\\n'")
        self.invoke("run-tests", expected=2, extra=runner)
        runner = self.fake_compiler("exit 1")
        self.invoke("run-tests", expected=1, extra=runner)

    def test_runner_source_mutation_is_not_a_pass(self):
        self.prepare_tests()
        runner = self.fake_compiler("printf '# changed\\n' >> examples/main.roc\nprintf 'All (2) tests passed in 0.1 ms.\\n'")
        self.assertIn("source changed", self.invoke("run-tests", expected=2, extra=runner).stderr)

    @unittest.skipUnless(COMPILER, "set ROC_POLICY_TEST_COMPILER for actual Roc execution")
    def test_real_roc_count_gate(self):
        self.prepare_tests()
        env = {key: value for key, value in os.environ.items() if key not in {"PATH"}}
        self.invoke("run-tests", extra=("--compiler", str(Path(COMPILER).resolve())), env=env)


if __name__ == "__main__":
    unittest.main()
