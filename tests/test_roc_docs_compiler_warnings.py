"""Compiler status 2 must never hide a failed renderer test or local warning."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('roc_docs_tests', Path(__file__).resolve().parents[1]/'tools/roc-docs/tests/run_roc_tests.py')
runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)


class CompilerWarningsTests(unittest.TestCase):
    def test_only_passing_tests_with_pinned_dependency_warning_are_accepted(self):
        diagnostic = '── ● unreachable code ─ ../../.cache/roc/packages/'+next(iter(runner.PACKAGES))+'/Module.roc:1:1\n'
        passing = diagnostic+'All (34) tests passed in 100 ms.\n'
        self.assertTrue(runner.passed_with_known_warnings(2, passing))
        for status, output in [(1,passing),(2,diagnostic),(2,'All (34) tests passed'),(2,passing.replace('unreachable code','unused variable')),(2,passing.replace('../../.cache/roc/packages/','tools/roc-docs/'))]:
            with self.subTest(status=status,output=output):
                self.assertFalse(runner.passed_with_known_warnings(status,output))
