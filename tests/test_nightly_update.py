import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / 'actions/nightly/nightly_update.py'
spec = importlib.util.spec_from_file_location('nightly_update', MODULE)
n = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n)

class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        (root / '.github/workflows').mkdir(parents=True)
        for workflow in ['ci.yml', 'release.yml']:
            (root / '.github/workflows' / workflow).write_text('name: fixture\n')
        (root / '.github/roc-nightly.json').write_text(json.dumps({'workflows': ['ci.yml', 'release.yml']}))
        (root / '.roc-version').write_text('nightly-2026-09-04-c125b82\n')
        self.env = patch.dict(os.environ, GITHUB_REPOSITORY='owner/project', GITHUB_OUTPUT=str(root/'outputs'),
                              GITHUB_SERVER_URL='https://github.com', GITHUB_RUN_ID='100', DEFAULT_BRANCH='main',
                              CANDIDATE_SHA='candidate', NIGHTLY_TAG='nightly-2026-09-05-b195f5b', GH_TOKEN='test-token')
        self.env.start(); self.addCleanup(self.env.stop)
        self.root_patch = patch.object(n, 'ROOT', root)
        self.root_patch.start(); self.addCleanup(self.root_patch.stop)

    def test_config_check_is_read_only(self):
        with patch.object(n, 'api') as api, patch.object(n, 'run') as run:
            n.check()
        api.assert_not_called()
        run.assert_not_called()

    def test_config_rejects_malformed_missing_or_duplicate_workflows(self):
        invalid = [{}, [], {'workflows': []}, {'workflows': 'ci.yml'},
                   {'workflows': ['ci.yml', 'ci.yml']}, {'workflows': ['missing.yml']},
                   {'workflows': ['../ci.yml']}, {'workflows': [3]},
                   {'workflows': ['ci.yml'], 'unknown': True}]
        for config in invalid:
            with self.subTest(config=config):
                (n.ROOT / '.github/roc-nightly.json').write_text(json.dumps(config))
                with self.assertRaises(ValueError): n.load_workflows()

    def test_config_rejects_workflow_symlink_outside_repository(self):
        with tempfile.TemporaryDirectory() as external:
            target = Path(external) / 'ci.yml'
            target.write_text('name: outside')
            workflow = n.ROOT / '.github/workflows/ci.yml'
            workflow.unlink()
            workflow.symlink_to(target)
            with self.assertRaises(ValueError): n.load_workflows()

    def test_shared_controller_uses_callers_workspace(self):
        with patch.dict(os.environ, GITHUB_WORKSPACE=str(n.ROOT)):
            isolated = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(isolated)
        self.assertEqual(isolated.ROOT, n.ROOT)
        self.assertNotEqual(isolated.ROOT, MODULE.parent)

    def test_tag_rejects_injection_and_floating_versions(self):
        for value in ['nightly', 'nightly-2026-09-05-b195f5b\nother=x', 'nightly-$(whoami)', '../main']:
            with self.subTest(value=value), self.assertRaises(ValueError): n.tag(value)
        self.assertEqual(n.tag('nightly-2026-09-05-b195f5b'), 'nightly-2026-09-05-b195f5b')

    def test_outputs_reject_multiline(self):
        with self.assertRaises(ValueError): n.output('sha', 'a\nchanged=true')

    def test_signed_commit_only_changes_pin_and_checks_signature(self):
        with patch.object(n, 'api', side_effect=[{'data': {'createCommitOnBranch': {'commit': {'oid': 'signed'}}}},
                                                {'commit': {'verification': {'verified': True}}}]) as api:
            self.assertEqual(n.signed_pin('base', 'nightly-2026-09-05-b195f5b'), 'signed')
        payload = api.call_args_list[0].args[1]['variables']['input']
        self.assertEqual(payload['expectedHeadOid'], 'base')
        self.assertEqual([f['path'] for f in payload['fileChanges']['additions']], ['.roc-version'])

    def test_unverified_commit_is_rejected(self):
        with patch.object(n, 'api', side_effect=[{'data': {'createCommitOnBranch': {'commit': {'oid': 'signed'}}}},
                                                {'commit': {'verification': {'verified': False}}}]), self.assertRaises(ValueError):
            n.signed_pin('base', 'nightly-2026-09-05-b195f5b')

    def test_push_uses_exact_lease_without_token_in_arguments(self):
        with patch.object(n, 'run') as run: n.push_base('base', 'previous')
        args = run.call_args.args[0]
        self.assertIn('--force-with-lease=refs/heads/automation/roc-nightly:previous', args)
        self.assertNotIn('test-token', ' '.join(args))
        self.assertIn('GIT_CONFIG_VALUE_0', run.call_args.kwargs['env'])

    def test_prepare_noop_has_no_mutations(self):
        release = {'tag_name': 'nightly-2026-09-04-c125b82', 'draft': False, 'prerelease': False, 'assets': [{}]}
        with patch.object(n, 'run', return_value='base'), patch.object(n, 'api', return_value=release) as api:
            n.prepare()
        self.assertEqual(api.call_count, 1)
        self.assertIn('changed=false', Path(os.environ['GITHUB_OUTPUT']).read_text())

    def test_prepare_refuses_unrelated_branch_changes(self):
        release = {'tag_name': 'nightly-2026-09-05-b195f5b', 'draft': False, 'prerelease': False, 'assets': [{}]}
        refs = [{'ref': 'refs/heads/automation/roc-nightly', 'object': {'sha': 'old'}}]
        commit = {'parents': [{'sha': 'base'}], 'files': [{'filename': 'src/main.roc'}]}
        with patch.object(n, 'run', return_value='base'), patch.object(n, 'api', side_effect=[release, refs, commit]), patch.object(n, 'push_base') as push:
            with self.assertRaises(ValueError): n.prepare()
            push.assert_not_called()

    def response(self, conclusion='success', sha='candidate'):
        return {'head_sha': sha, 'head_branch': n.BRANCH, 'event': 'workflow_dispatch',
                'status': 'completed', 'conclusion': conclusion}

    def test_wrong_commit_branch_or_event_rejected(self):
        for key, value in [('head_sha', 'other'), ('head_branch', 'main'), ('event', 'push')]:
            item = self.response(); item[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): n.validate_run(item, 'candidate')

    def validate_api(self, conclusion='success'):
        def api(endpoint, data=None, method=None):
            if endpoint.endswith('/dispatches'):
                self.assertEqual(data, {'ref': n.BRANCH, 'inputs': {'nightly_validation': True}})
                return {'workflow_run_id': 7 if '/ci.yml/' in endpoint else 8, 'html_url': 'https://github.com/run'}
            self.assertIn(endpoint.rsplit('/', 1)[-1], ['7', '8'])
            return self.response(conclusion)
        return api

    def test_validate_uses_returned_ids_and_waits_for_all_workflows(self):
        with patch.object(n, 'head', return_value='candidate'), patch.object(n, 'api', side_effect=self.validate_api()) as api:
            n.validate()
        self.assertEqual(api.call_count, 4)
        self.assertIn('"id": 8', Path(os.environ['GITHUB_OUTPUT']).read_text())

    def test_failed_cancelled_or_skipped_validation_is_not_success(self):
        for status in ['failure', 'cancelled', 'skipped', 'timed_out', 'action_required']:
            with self.subTest(status=status), patch.object(n, 'head', return_value='candidate'), patch.object(n, 'api', side_effect=self.validate_api(status)), self.assertRaises(ValueError):
                n.validate()

    def test_stale_candidate_results_are_not_reported(self):
        with patch.object(n, 'head', return_value='other'), patch.object(n, 'save_pr') as save:
            with self.assertRaises(ValueError): n.report()
            save.assert_not_called()

    def test_report_requires_complete_success_evidence(self):
        for runs in [[], [{'workflow': 'ci.yml', 'conclusion': 'success'}]]:
            with patch.dict(os.environ, TEST_RESULT='success', VALIDATION_RUNS=json.dumps(runs)), patch.object(n, 'head', return_value='candidate'), patch.object(n, 'save_pr') as save:
                n.report()
                self.assertIn('Needs attention', save.call_args.args[2])

    def test_report_links_both_successful_runs(self):
        runs = [{'workflow': w, 'conclusion': 'success', 'html_url': 'https://github.com/run'} for w in ['ci.yml', 'release.yml']]
        with patch.dict(os.environ, TEST_RESULT='success', VALIDATION_RUNS=json.dumps(runs)), patch.object(n, 'head', return_value='candidate'), patch.object(n, 'save_pr') as save:
            n.report()
        self.assertIn('Passed', save.call_args.args[2])
        self.assertEqual(save.call_args.args[3], runs)


    def merge_fixture(self):
        (n.ROOT / '.github/roc-nightly.json').write_text(json.dumps({
            'workflows': ['ci.yml', 'release.yml'], 'auto_merge': True}))
        os.environ['VALIDATION_RUNS'] = json.dumps([
            {'workflow': 'ci.yml', 'id': 7}, {'workflow': 'release.yml', 'id': 8}])
        pr = {'number': 1, 'state': 'open', 'draft': False,
              'user': {'login': 'github-actions[bot]', 'type': 'Bot'},
              'head': {'repo': {'full_name': 'owner/project'}, 'ref': n.BRANCH, 'sha': 'candidate'},
              'base': {'repo': {'full_name': 'owner/project'}, 'ref': 'main', 'sha': 'base'},
              'commits': 1, 'changed_files': 1}
        commit = {'parents': [{'sha': 'base'}], 'commit': {'verification': {'verified': True}},
                  'author': {'login': 'github-actions[bot]'},
                  'files': [{'filename': '.roc-version', 'status': 'modified', 'additions': 1, 'deletions': 1}]}
        rules = [{'type': 'pull_request'}, {'type': 'required_status_checks', 'parameters': {
            'strict_required_status_checks_policy': True, 'required_status_checks': [{'context': 'test'}]}}]
        return {
            'repos/owner/project/pulls/1': pr,
            'repos/owner/project/commits/candidate': commit,
            'repos/roc-lang/nightlies/releases/tags/nightly-2026-09-05-b195f5b': {
                'tag_name': 'nightly-2026-09-05-b195f5b', 'draft': False, 'prerelease': False, 'assets': [{}]},
            'repos/owner/project/actions/runs/7': {**self.response(), 'path': '.github/workflows/ci.yml'},
            'repos/owner/project/actions/runs/8': {**self.response(), 'path': '.github/workflows/release.yml'},
            'repos/owner/project/rules/branches/main': rules,
            'repos/owner/project/git/ref/heads/main': {'object': {'sha': 'base'}},
        }

    def attempt_merge(self, responses, *, failure=False, merge_result=None):
        writes = []
        def api(endpoint, data=None, method=None):
            if data is not None or method is not None:
                writes.append((endpoint, data, method))
                self.assertEqual(endpoint, 'repos/owner/project/pulls/1/merge')
                return merge_result or {'merged': True, 'sha': 'merged'}
            return responses[endpoint]
        with patch.object(n, 'run', return_value='base'), patch.object(n, 'head', return_value='candidate'), \
             patch.object(n, 'pin_at', return_value=os.environ['NIGHTLY_TAG']), \
             patch.object(n, 'existing_pr', return_value={'number': 1}), patch.object(n, 'api', side_effect=api):
            if failure:
                with self.assertRaises(ValueError): n.merge()
            else:
                n.merge()
        return writes

    def test_merge_is_opt_in_and_disabled_without_any_api_access(self):
        for enabled in [None, False]:
            config = {'workflows': ['ci.yml']}
            if enabled is not None: config['auto_merge'] = enabled
            (n.ROOT / '.github/roc-nightly.json').write_text(json.dumps(config))
            with patch.object(n, 'api') as api, patch.object(n, 'run') as run:
                n.merge()
            api.assert_not_called()
            run.assert_not_called()

    def test_merge_policy_rejects_truthy_non_booleans(self):
        for value in ['true', 'false', 1, 0, None, {}, []]:
            (n.ROOT / '.github/roc-nightly.json').write_text(json.dumps({'workflows': ['ci.yml'], 'auto_merge': value}))
            with self.subTest(value=value), self.assertRaises(ValueError): n.check()

    def test_merge_rechecks_live_evidence_and_uses_exact_head_without_bypass(self):
        responses = self.merge_fixture()
        self.assertEqual(self.attempt_merge(responses), [
            ('repos/owner/project/pulls/1/merge', {'sha': 'candidate', 'merge_method': 'squash'}, 'PUT')])

    def test_merge_refuses_untrusted_prs_and_commits(self):
        original = self.merge_fixture()
        mutations = [
            ('pulls/1', ['state'], 'closed'), ('pulls/1', ['draft'], True),
            ('pulls/1', ['user', 'login'], 'human'), ('pulls/1', ['user', 'type'], 'User'),
            ('pulls/1', ['head', 'repo', 'full_name'], 'attacker/project'),
            ('pulls/1', ['head', 'sha'], 'other'), ('pulls/1', ['head', 'ref'], 'other'),
            ('pulls/1', ['base', 'sha'], 'new-base'), ('pulls/1', ['base', 'ref'], 'release'),
            ('pulls/1', ['commits'], 2), ('pulls/1', ['changed_files'], 2),
            ('commits/candidate', ['parents'], []),
            ('commits/candidate', ['parents'], [{'sha': 'old-base'}]),
            ('commits/candidate', ['commit', 'verification', 'verified'], False),
            ('commits/candidate', ['author'], None),
            ('commits/candidate', ['files', 0, 'filename'], 'source.roc'),
            ('commits/candidate', ['files', 0, 'status'], 'added'),
            ('commits/candidate', ['files', 0, 'additions'], 2),
        ]
        for endpoint, keys, value in mutations:
            responses = copy.deepcopy(original)
            target = responses['repos/owner/project/' + endpoint]
            for key in keys[:-1]: target = target[key]
            target[keys[-1]] = value
            with self.subTest(endpoint=endpoint, keys=keys):
                self.assertEqual(self.attempt_merge(responses, failure=True), [])

    def test_merge_refuses_failed_stale_or_wrong_workflow_evidence(self):
        original = self.merge_fixture()
        for key, value in [('conclusion', 'failure'), ('conclusion', 'skipped'),
                           ('conclusion', 'cancelled'), ('status', 'in_progress'),
                           ('head_sha', 'old'), ('head_branch', 'main'), ('event', 'push'),
                           ('path', '.github/workflows/unrelated.yml')]:
            responses = copy.deepcopy(original)
            responses['repos/owner/project/actions/runs/8'][key] = value
            with self.subTest(key=key, value=value):
                self.assertEqual(self.attempt_merge(responses, failure=True), [])

    def test_merge_refuses_missing_duplicate_or_invalid_run_ids(self):
        responses = self.merge_fixture()
        for runs in [[], [{'workflow': 'ci.yml', 'id': 7}],
                     [{'workflow': 'ci.yml', 'id': 7}, {'workflow': 'release.yml', 'id': 7}],
                     [{'workflow': 'ci.yml', 'id': '../7'}, {'workflow': 'release.yml', 'id': 8}]]:
            with patch.dict(os.environ, VALIDATION_RUNS=json.dumps(runs)):
                self.assertEqual(self.attempt_merge(responses, failure=True), [])

    def test_merge_refuses_unpublished_release_or_missing_protection(self):
        original = self.merge_fixture()
        for key, value in [('draft', True), ('prerelease', True), ('assets', [])]:
            responses = copy.deepcopy(original)
            responses['repos/roc-lang/nightlies/releases/tags/nightly-2026-09-05-b195f5b'][key] = value
            self.assertEqual(self.attempt_merge(responses, failure=True), [])
        for rules in [[], [{'type': 'pull_request'}], original['repos/owner/project/rules/branches/main'][1:],
                      [{'type': 'pull_request'}, {'type': 'required_status_checks', 'parameters': {
                          'strict_required_status_checks_policy': False, 'required_status_checks': [{'context': 'test'}]}}]]:
            responses = copy.deepcopy(original)
            responses['repos/owner/project/rules/branches/main'] = rules
            self.assertEqual(self.attempt_merge(responses, failure=True), [])

    def test_merge_refuses_base_movement_and_github_rejection(self):
        responses = self.merge_fixture()
        responses['repos/owner/project/git/ref/heads/main']['object']['sha'] = 'new-base'
        self.assertEqual(self.attempt_merge(responses, failure=True), [])
        responses = self.merge_fixture()
        self.assertEqual(len(self.attempt_merge(responses, failure=True, merge_result={'merged': False})), 1)

if __name__ == '__main__': unittest.main()
