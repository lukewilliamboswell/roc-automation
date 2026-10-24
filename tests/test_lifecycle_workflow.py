"""Check the actual shared graph and evaluate its aggregate transition predicate.

These are configuration regressions, not live GitHub execution evidence.
"""
import ast
import itertools
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/release-lifecycle.yml'


def evaluate(node):
    if isinstance(node, ast.Expression):
        return evaluate(node.body)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.BoolOp):
        values = [evaluate(value) for value in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
        return evaluate(node.left) == evaluate(node.comparators[0])
    raise AssertionError(f'unsupported predicate syntax: {ast.dump(node)}')


class LifecycleWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text()
        cls.jobs = dict(re.findall(r'(?ms)^  ([a-z][a-z-]*):\n(.*?)(?=^  [a-z][a-z-]*:\n|\Z)', cls.source.split('jobs:\n', 1)[1]))
        verdict = cls.jobs['verdict']
        cls.predicate = re.search(r'PASSED: >-\s*\$\{\{(.*?)\}\}', verdict, re.S).group(1)

    def passed(self, event, full, hosts, results):
        variables = {'github.event_name': event, 'needs.plan.outputs.full-rehearsal': str(full).lower(),
                     'needs.plan.outputs.has-host': str(hosts).lower()}
        variables.update({f'needs.{stage}.result': result for stage, result in results.items()})
        expression = self.predicate
        for key in sorted(variables, key=len, reverse=True):
            expression = expression.replace(key, repr(variables[key]))
        expression = ' '.join(expression.replace('&&', ' and ').replace('||', ' or ').split())
        return evaluate(ast.parse(expression, mode='eval'))

    def test_every_required_stage_failure_skip_or_cancellation_fails_the_actual_predicate(self):
        for event, full, hosts in itertools.product(('pull_request', 'workflow_dispatch'), (False, True), (False, True)):
            results = dict.fromkeys(('plan', 'validate', 'host', 'source', 'prepare', 'sbom', 'seal', 'candidate', 'admit'), 'success')
            if event == 'pull_request':
                results['validate'] = 'skipped'
            if not hosts:
                results['host'] = 'skipped'
            if not full:
                results.update(dict.fromkeys(('prepare', 'sbom', 'seal', 'candidate', 'admit'), 'skipped'))
            self.assertTrue(self.passed(event, full, hosts, results), (event, full, hosts))
            for stage, expected in results.items():
                for outcome in ('success', 'skipped', 'failure', 'cancelled'):
                    if outcome != expected:
                        with self.subTest(event=event, full=full, hosts=hosts, stage=stage, outcome=outcome):
                            self.assertFalse(self.passed(event, full, hosts, {**results, stage: outcome}))

    def test_aggregate_is_always_present_and_covers_every_validation_stage(self):
        verdict = self.jobs['verdict']
        self.assertIn('if: always()', verdict)
        dependencies = re.search(r'needs: \[([^]]+)\]', verdict).group(1).replace(' ', '').split(',')
        self.assertEqual(set(dependencies), {'plan', 'validate', 'host', 'source', 'prepare', 'sbom', 'seal', 'candidate', 'admit'})
        self.assertNotIn('paths:', self.source)
        self.assertIn('test "$PASSED" = true', verdict)

    def test_skipped_ancestors_cannot_implicitly_skip_selected_descendants(self):
        for name in ('host', 'source', 'prepare', 'sbom', 'seal', 'candidate', 'admit', 'publish', 'pages-restore', 'pages-deploy'):
            condition = re.search(r'(?ms)^    if: (.*?)(?=^    [a-z-]+:)', self.jobs[name]).group(1)
            self.assertRegex(condition, r'(always|cancelled)\(\)', name)

    def test_privileged_jobs_only_handle_inert_artifacts_with_pinned_code(self):
        for name, body in self.jobs.items():
            if re.search(r'(?:contents|pages|id-token|attestations): write', body):
                self.assertIn(name, ('publish', 'pages-deploy'))
                self.assertNotIn('actions/checkout@', body)
                self.assertNotIn('lifecycle-tool@', body)
                self.assertNotIn('setup-package-compiler@', body)
                self.assertNotIn('actions/cache@', body)
        publish = self.jobs['publish']
        self.assertIn('environment: release', publish)
        self.assertIn("github.event_name == 'workflow_dispatch'", publish)
        self.assertIn("github.event.repository.default_branch", publish)
        self.assertIn("require-admission: 'true'", publish)
        self.assertLess(publish.index('phase: collect'), publish.index('phase: verify-provenance'))
        self.assertLess(publish.index('phase: verify-provenance'), publish.index('publish-release@'))

    def test_all_shared_references_are_full_shas_and_bootstrap_precedes_checkout(self):
        for reference in re.findall(r'^\s*(?:-\s*)?uses: (\S+)', self.source, re.M):
            self.assertRegex(reference, r'^[^@\s]+@[0-9a-f]{40}$')
        self.assertNotIn('secrets: inherit', self.source)
        for name in ('validate', 'host', 'source', 'prepare', 'sbom', 'seal', 'candidate'):
            body = self.jobs[name]
            self.assertIn('contents: read', body)
            self.assertLess(body.index('lifecycle-tool@'), body.index('actions/checkout@'))
            self.assertLess(body.index('setup-package-compiler@'), body.index('actions/checkout@'))
        self.assertIn('lifecycle-native-inputs', self.jobs['source'])
        self.assertIn('lifecycle-native-inputs', self.jobs['prepare'])
