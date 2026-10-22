"""Regress public lock-report identity admission independently of Blueprint."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('adoption', Path(__file__).with_name('check_blueprint_adoption.py'))
adoption = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adoption)


class LockReportTests(unittest.TestCase):
    def setUp(self):
        self.entry = {'name': 'roc-docs', 'system': 'aarch64-darwin', 'sha256': 'a' * 64,
                      'content_identity': 'sha256:' + 'a' * 64, 'size': 123}
        self.report = {'format': 2, 'sources': {}, 'fetched': [self.entry]}

    def test_program_is_selected_by_name_and_system(self):
        self.report['fetched'].insert(0, {**self.entry, 'system': 'x86_64-linux', 'sha256': 'b' * 64,
                                          'content_identity': 'sha256:' + 'b' * 64})
        self.assertEqual(adoption.program_pin(self.report, 'roc-docs', 'aarch64-darwin'), ('a' * 64, 123))

    def test_old_future_and_boolean_format_versions_are_rejected(self):
        for version in (1, 3, True, '2', None):
            with self.subTest(version=version), self.assertRaisesRegex(AssertionError, 'format'):
                adoption.program_pin({**self.report, 'format': version}, 'roc-docs', 'aarch64-darwin')

    def test_missing_or_duplicate_target_never_selects_another_pin(self):
        for entries in ([], [{**self.entry, 'system': 'x86_64-linux'}], [self.entry, self.entry]):
            with self.subTest(entries=entries), self.assertRaisesRegex(AssertionError, 'one locked'):
                adoption.program_pin({**self.report, 'fetched': entries}, 'roc-docs', 'aarch64-darwin')

    def test_malformed_or_disagreeing_identity_is_rejected(self):
        for change in ({'sha256': 'A' * 64}, {'sha256': 'a' * 63}, {'sha256': None},
                       {'content_identity': 'sha256:' + 'b' * 64}, {'size': True}, {'size': 0},
                       {'size': -1}, {'size': 2**64}, {'size': '123'}):
            report = copy.deepcopy(self.report)
            report['fetched'][0].update(change)
            with self.subTest(change=change), self.assertRaises(AssertionError):
                adoption.program_pin(report, 'roc-docs', 'aarch64-darwin')

    def test_unexpected_report_shapes_are_rejected(self):
        for report in ([], None, {**self.report, 'fetched': {}}, {**self.report, 'fetched': [None]}):
            with self.subTest(report=report), self.assertRaises(AssertionError):
                adoption.program_pin(report, 'roc-docs', 'aarch64-darwin')

    def test_external_projection_is_checked_before_use(self):
        with patch.object(adoption.subprocess, 'run') as run:
            run.return_value.stdout = '{"format": 1}'
            with self.assertRaisesRegex(AssertionError, 'format'):
                adoption.locked_program('roc-docs', 'aarch64-darwin')
            self.assertEqual(run.call_args.args[0], ['blueprint', 'lock', 'show', '--json', 'roc-docs'])
            self.assertTrue(run.call_args.kwargs['check'])


if __name__ == '__main__':
    unittest.main()
