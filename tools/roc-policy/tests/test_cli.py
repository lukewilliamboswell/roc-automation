"""Exercise exact compiled artifacts; stdlib harness, no source/toolchain required."""
import json
import os
import shlex
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

    def test_embedded_dictionary_and_license_without_source_files(self):
        (self.root / "examples/main.roc").write_text(SOURCE + '# abandonned abbout\n')
        self.configure('{kind: Spelling, scope: "examples", channels: [Comments]}')
        output = self.invoke(expected=1).stdout
        self.assertIn("abandonned", output)
        self.assertIn("abbout", output)
        result = subprocess.run([str(BINARY), "--licenses"], cwd=self.root,
                                env={"PATH": "/nonexistent"}, capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Permission is hereby granted", result.stdout)
        self.assertIn("THE SOFTWARE IS PROVIDED", result.stdout)

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

    def test_manifest_is_static_discovery_only(self):
        report = self.prepare_tests()
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(len(report["files"][0]["tests"]), 2)
        self.assertNotIn("completed", report["files"][0])
        self.assertNotIn("status", report["files"][0]["tests"][0])

    def test_unused_receipt_protocol_is_not_exposed(self):
        self.invoke("verify-tests", expected=2)

    def test_no_test_inventory_is_error(self):
        self.configure('')
        self.invoke("manifest", expected=2)

    def fake_compiler(self, body):
        compiler = self.root / "runner.sh"
        compiler.write_text("#!/bin/sh\n" + body + "\n")
        compiler.chmod(0o755)
        return ("--compiler", str(compiler))

    def results_body(self, *locations, count=None, cached=False):
        count = len(locations) if count is None else count
        output = [f"PASS: {path}:{line}" for path, line in locations]
        output.append(f"All ({count}) tests passed in 0.1 ms." + (" (cached)" if cached else ""))
        return "printf '%s\\n' " + " ".join(shlex.quote(line) for line in output)

    def test_runner_uses_verbose_locations_and_normal_caching(self):
        self.prepare_tests()
        path = (self.root / "examples/main.roc").resolve()
        body = 'case "$*" in *--no-cache*) exit 91;; esac\n'
        body += 'case "$*" in *--verbose*) : ;; *) exit 92;; esac\n'
        body += self.results_body((path, 2), (path, 3), cached=True)
        output = self.invoke("run-tests", extra=self.fake_compiler(body)).stdout
        self.assertIn("cached results are accepted", output)

    def test_matching_total_cannot_hide_missing_or_duplicate_locations(self):
        self.prepare_tests()
        path = (self.root / "examples/main.roc").resolve()
        for locations in [((path, 2), (path, 2)), ((path, 2), (path.parent / "Imported.roc", 1))]:
            with self.subTest(locations=locations):
                runner = self.fake_compiler(self.results_body(*locations))
                self.assertIn("expected exactly one compiler PASS", self.invoke("run-tests", expected=1, extra=runner).stdout)

    def test_summary_and_result_records_must_agree(self):
        self.prepare_tests()
        path = (self.root / "examples/main.roc").resolve()
        runner = self.fake_compiler(self.results_body((path, 2), (path, 3), count=3))
        self.invoke("run-tests", expected=2, extra=runner)

    def test_successful_output_cannot_override_failed_compiler_exit(self):
        self.prepare_tests()
        path = (self.root / "examples/main.roc").resolve()
        runner = self.fake_compiler(self.results_body((path, 2), (path, 3)) + "\nexit 1")
        self.invoke("run-tests", expected=1, extra=runner)

    def test_inline_and_imported_results_do_not_inflate_static_file_count(self):
        path = (self.root / "examples/main.roc").resolve()
        path.write_text("helper = |_| {\nexpect True\nTrue\n}\nexpect helper({})\n")
        self.configure('')
        report = json.loads(self.invoke("manifest").stdout)
        self.assertEqual(len(report["files"][0]["tests"]), 1)
        runner = self.fake_compiler(self.results_body((path, 2), (path, 5), (path.parent / "Imported.roc", 1)))
        self.invoke("run-tests", extra=runner)

    def test_ambiguous_static_and_inline_sites_on_one_line_fail_explicitly(self):
        (self.root / "examples/main.roc").write_text("expect { expect True\nTrue }\n")
        self.configure('')
        self.assertIn("ambiguous", self.invoke("manifest", expected=2).stderr)

    def test_runner_source_mutation_is_not_a_pass(self):
        self.prepare_tests()
        path = (self.root / "examples/main.roc").resolve()
        runner = self.fake_compiler("printf '# changed\\n' >> examples/main.roc\n" + self.results_body((path, 2), (path, 3)))
        self.assertIn("source changed", self.invoke("run-tests", expected=2, extra=runner).stderr)

    def test_later_invocation_cannot_mutate_an_earlier_inventoried_file(self):
        (self.root / "examples/main.roc").unlink()
        for name in ["A.roc", "B.roc"]:
            (self.root / "examples" / name).write_text("expect True\n")
        self.configure('')
        body = 'case "$2" in */B.roc) printf "# changed\\n" >> examples/A.roc;; esac\n'
        body += 'printf "PASS: %s:1\\nAll (1) tests passed in 0.1 ms.\\n" "$2"'
        self.assertIn("source changed", self.invoke("run-tests", expected=2, extra=self.fake_compiler(body)).stderr)

    def compiler_environment(self):
        return {key: value for key, value in os.environ.items() if key != "PATH"} | {"ROC_CACHE_DIR": str(self.root / "cache")}

    @unittest.skipUnless(COMPILER, "set ROC_POLICY_TEST_COMPILER for actual Roc execution")
    def test_real_roc_imports_inline_expectations_and_cached_results(self):
        (self.root / "examples/B.roc").write_text("B :: [].{ value = 1 }\nexpect B.value == 1\n")
        (self.root / "examples/main.roc").write_text("import B\nhelper = |x| {\nexpect x > 0\nx\n}\nexpect helper(B.value) == 1\n")
        self.configure('')
        runner = ("--compiler", str(Path(COMPILER).resolve()))
        self.invoke("run-tests", extra=runner, env=self.compiler_environment())
        result = self.invoke("run-tests", extra=runner, env=self.compiler_environment())
        self.assertIn("cached results are accepted", result.stdout)

    @unittest.skipUnless(COMPILER, "set ROC_POLICY_TEST_COMPILER for actual Roc execution")
    def test_real_roc_main_argument_resolves_package_aliases(self):
        (self.root / "examples/main.roc").write_text('app [main!] { cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst" }\nmain! = |_| Ok({})\n')
        (self.root / "examples/Helper.roc").write_text("import cli.Stdout\nexpect True\n")
        self.configure('', ', test_roots: ["examples/Helper.roc"], test_main: "examples/main.roc"')
        self.invoke("run-tests", extra=("--compiler", str(Path(COMPILER).resolve())), env=self.compiler_environment())


if __name__ == "__main__":
    unittest.main()
