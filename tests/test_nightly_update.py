import base64
import copy
import importlib.util
import json
import os
import subprocess
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
        (root / '.github/roc-nightly.json').write_text(json.dumps({
            'workflows': ['ci.yml', 'release.yml'], 'auto_merge': False}))
        (root / '.roc-version').write_text('nightly-2026-09-04-c125b82\n')
        self.env = patch.dict(os.environ, GITHUB_REPOSITORY='owner/project', GITHUB_OUTPUT=str(root/'outputs'),
                              GITHUB_SERVER_URL='https://github.com', GITHUB_RUN_ID='100', DEFAULT_BRANCH='main',
                              CANDIDATE_SHA='candidate', GITHUB_SHA='base', NIGHTLY_TAG='nightly-2026-09-05-b195f5b', GH_TOKEN='test-token')
        self.env.start(); self.addCleanup(self.env.stop)
        self.root_patch = patch.object(n, 'ROOT', root)
        self.root_patch.start(); self.addCleanup(self.root_patch.stop)

    def test_config_check_is_read_only(self):
        with patch.object(n, 'api') as api, patch.object(n, 'run') as run:
            n.check()
        api.assert_not_called()
        run.assert_not_called()

    def test_api_error_reports_only_structured_github_message(self):
        error = subprocess.CalledProcessError(1, ['gh', 'api'], output='{"message":"Required checks missing"}', stderr='private diagnostics')
        with patch.object(n, 'run', side_effect=error), self.assertRaisesRegex(ValueError, '^GitHub API rejected endpoint: Required checks missing$'):
            n.api('endpoint')

    def test_required_statuses_only_follow_real_successful_jobs(self):
        runs = [{'id': 7}]
        for jobs in [[], [{'name': 'test', 'status': 'completed', 'conclusion': 'skipped'}],
                     [{'name': 'test', 'status': 'completed', 'conclusion': 'failure'}],
                     [{'name': 'other', 'status': 'completed', 'conclusion': 'success'}],
                     [{'name': 'test', 'status': 'in_progress', 'conclusion': None}]]:
            with patch.object(n, 'api', return_value={'jobs': jobs, 'total_count': len(jobs)}):
                with self.assertRaises(ValueError): n.verify_required_jobs(runs, ['test'])
        job = {'name': 'test', 'status': 'completed', 'conclusion': 'success'}
        with patch.object(n, 'api', side_effect=[{'jobs': [job], 'total_count': 2},
                                               {'jobs': [dict(job, name='bundle')], 'total_count': 2}]) as api:
            n.verify_required_jobs(runs, ['test', 'bundle'])
        self.assertIn('page=2', api.call_args.args[0])

    def test_default_validation_publishes_pending_before_success(self):
        config = {'workflows': ['ci.yml', 'release.yml']}
        (n.ROOT / '.github/roc-nightly.json').write_text(json.dumps(config))
        for conclusion in ['success', 'failure']:
            with patch.object(n, 'head', return_value='candidate'), patch.object(n, 'api', side_effect=self.validate_api(conclusion)), \
                 patch.object(n, 'required_contexts', return_value=['test']), patch.object(n, 'verify_required_jobs') as verify, \
                 patch.object(n, 'publish_statuses') as publish:
                n.validate()
            self.assertEqual([call.args[2] for call in publish.call_args_list],
                             ['pending', 'success'] if conclusion == 'success' else ['pending', 'failure'])
            self.assertEqual(verify.call_count, 1 if conclusion == 'success' else 0)
            outputs = (n.ROOT / 'outputs').read_text().splitlines()
            self.assertIn(f"passed={str(conclusion == 'success').lower()}", outputs)

    def test_status_publication_targets_exact_candidate_and_required_names(self):
        with patch.object(n, 'api') as api:
            n.publish_statuses('candidate', ['test'], 'pending')
        self.assertEqual(api.call_args.args[0], 'repos/owner/project/statuses/candidate')
        self.assertEqual(api.call_args.args[1]['context'], 'test')
        self.assertEqual(api.call_args.args[1]['state'], 'pending')

    def test_privileged_controller_rejects_pr_events_branches_and_tags(self):
        for event, ref in [('pull_request', 'refs/heads/main'),
                           ('pull_request_target', 'refs/heads/main'),
                           ('workflow_run', 'refs/heads/main'),
                           ('workflow_dispatch', 'refs/heads/other'),
                           ('workflow_dispatch', 'refs/tags/main')]:
            with self.subTest(event=event, ref=ref), patch.dict(os.environ, GITHUB_EVENT_NAME=event, GITHUB_REF=ref):
                with self.assertRaises(ValueError): n.require_trusted_context()
        for event in ['schedule', 'workflow_dispatch']:
            with patch.dict(os.environ, GITHUB_EVENT_NAME=event, GITHUB_REF='refs/heads/main', GITHUB_SHA='base'), patch.object(n, 'run', return_value='base'):
                n.require_trusted_context()
        with patch.dict(os.environ, GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REF='refs/heads/main', GITHUB_SHA='base'), patch.object(n, 'run', return_value='new-base'):
            with self.assertRaises(ValueError): n.require_trusted_context()

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
        with patch.object(n, 'run', return_value='base'), \
             patch.object(n, 'api', side_effect=[release, refs, commit]), \
             patch.object(n, 'config_at', return_value={'workflows': ['ci.yml']}), \
             patch.object(n, 'pin_at', return_value='nightly-2026-09-04-c125b82'), \
             patch.object(n, 'verify_pin_candidate', side_effect=ValueError(
                 'Candidate changes files outside the compiler pins')), \
             patch.object(n, 'push_base') as push:
            with self.assertRaises(ValueError): n.prepare()
            push.assert_not_called()

    def test_stale_candidate_is_verified_with_its_own_base_config(self):
        (n.ROOT / '.github/roc-nightly.json').write_text(json.dumps({
            'workflows': ['ci.yml'],
            'compiler_roots': ['package/main.roc', 'package/new.roc'],
        }))
        (n.ROOT / 'package').mkdir()
        (n.ROOT / 'package/main.roc').write_text('app [main] { roc: "nightly-2026-09-04-c125b82" }')
        (n.ROOT / 'package/new.roc').write_text('app [main] { roc: "nightly-2026-09-04-c125b82" }')
        release = {'tag_name': 'nightly-2026-09-05-b195f5b', 'draft': False, 'prerelease': False, 'assets': [{}]}
        refs = [{'ref': 'refs/heads/automation/roc-nightly', 'object': {'sha': 'old'}}]
        commit = {'parents': [{'sha': 'previous-base'}],
                  'files': [{'filename': 'package/main.roc', 'status': 'modified'}]}
        previous_config = {'workflows': ['ci.yml'], 'compiler_roots': ['package/main.roc']}
        with patch.object(n, 'run', return_value='base'), patch.object(n, 'api', side_effect=[release, refs, commit]), \
             patch.object(n, 'config_at', return_value=previous_config) as config_at, \
             patch.object(n, 'pin_at', return_value='nightly-2026-09-04-c125b82'), \
             patch.object(n, 'verify_pin_candidate') as verify, patch.object(n, 'existing_pr', return_value=None), \
             patch.object(n, 'push_base') as push, patch.object(n, 'signed_pin', return_value='signed'), \
             patch.object(n, 'save_pr'):
            n.prepare()
        config_at.assert_called_once_with('previous-base')
        verify.assert_called_once_with('previous-base', 'old', commit['files'],
                                       'nightly-2026-09-04-c125b82', previous_config)
        push.assert_called_once_with('base', 'old')

    def test_stale_legacy_candidate_survives_migration_to_header_pins(self):
        (n.ROOT / '.github/roc-nightly.json').write_text(json.dumps({
            'workflows': ['ci.yml'], 'compiler_roots': ['package/main.roc'],
        }))
        (n.ROOT / 'package').mkdir()
        (n.ROOT / 'package/main.roc').write_text(
            'app [main] { roc: "nightly-2026-09-04-c125b82" }')
        release = {'tag_name': 'nightly-2026-09-05-b195f5b', 'draft': False,
                   'prerelease': False, 'assets': [{}]}
        refs = [{'ref': 'refs/heads/automation/roc-nightly',
                 'object': {'sha': 'legacy-candidate'}}]
        commit = {'parents': [{'sha': 'legacy-base'}],
                  'files': [{'filename': '.roc-version', 'status': 'modified'}]}
        legacy_config = {'workflows': ['ci.yml']}
        with patch.object(n, 'run', return_value='base'), \
             patch.object(n, 'api', side_effect=[release, refs, commit]), \
             patch.object(n, 'config_at', return_value=legacy_config), \
             patch.object(n, 'pin_at', return_value='nightly-2026-09-04-c125b82'), \
             patch.object(n, 'verify_pin_candidate') as verify, \
             patch.object(n, 'existing_pr', return_value=None), \
             patch.object(n, 'push_base') as push, \
             patch.object(n, 'signed_pin', return_value='signed'), \
             patch.object(n, 'save_pr'):
            n.prepare()
        verify.assert_called_once_with(
            'legacy-base', 'legacy-candidate', commit['files'],
            'nightly-2026-09-04-c125b82', legacy_config)
        push.assert_called_once_with('base', 'legacy-candidate')

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
            with self.subTest(status=status), patch.object(n, 'head', return_value='candidate'), patch.object(n, 'api', side_effect=self.validate_api(status)):
                if status == 'failure':
                    n.validate()
                    self.assertIn('passed=false', (n.ROOT / 'outputs').read_text().splitlines())
                else:
                    with self.assertRaises(ValueError):
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
        with patch.dict(os.environ, TEST_RESULT='success', TEST_PASSED='true', VALIDATION_RUNS=json.dumps(runs)), patch.object(n, 'head', return_value='candidate'), patch.object(n, 'save_pr') as save:
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
            'repos/owner/project/contents/.github/roc-nightly.json?ref=base': {
                'content': base64.b64encode((n.ROOT / '.github/roc-nightly.json').read_bytes()).decode()},
            'repos/owner/project/pulls/1': pr,
            'repos/owner/project/commits/candidate': commit,
            'repos/owner/project/contents/.roc-version?ref=base': {
                'type': 'file', 'content': base64.b64encode(
                    b'nightly-2026-09-04-c125b82\n').decode()},
            'repos/owner/project/contents/.roc-version?ref=candidate': {
                'type': 'file', 'content': base64.b64encode(
                    b'nightly-2026-09-05-b195f5b\n').decode()},
            'repos/roc-lang/nightlies/releases/tags/nightly-2026-09-05-b195f5b': {
                'tag_name': 'nightly-2026-09-05-b195f5b', 'draft': False, 'prerelease': False, 'assets': [{}]},
            'repos/owner/project/actions/runs/7': {**self.response(), 'path': '.github/workflows/ci.yml'},
            'repos/owner/project/actions/runs/8': {**self.response(), 'path': '.github/workflows/release.yml'},
            'repos/owner/project/actions/runs/7/jobs?filter=latest&per_page=100&page=1': {
                'jobs': [{'name': 'test', 'status': 'completed', 'conclusion': 'success'}], 'total_count': 1},
            'repos/owner/project/actions/runs/8/jobs?filter=latest&per_page=100&page=1': {'jobs': [], 'total_count': 0},
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
        with patch.object(n, 'run') as run, patch.object(n, 'head', return_value='candidate'), \
             patch.object(n, 'pin_at', return_value=os.environ['NIGHTLY_TAG']), \
             patch.object(n, 'existing_pr', return_value={'number': 1}), patch.object(n, 'api', side_effect=api):
            if failure:
                with self.assertRaises(ValueError): n.merge()
            else:
                n.merge()
            run.assert_not_called()
        return writes

    def test_merge_reads_opt_out_only_at_trusted_base_and_disabled_has_no_writes(self):
        config = {'workflows': ['ci.yml'], 'auto_merge': False}
        contents = {'content': base64.b64encode(json.dumps(config).encode()).decode()}
        with patch.object(n, 'api', return_value=contents) as api, patch.object(n, 'run') as run:
            n.merge()
        api.assert_called_once_with('repos/owner/project/contents/.github/roc-nightly.json?ref=base')
        run.assert_not_called()

    def test_merge_needs_no_consumer_checkout_or_local_configuration(self):
        responses = self.merge_fixture()
        (n.ROOT / '.github/roc-nightly.json').unlink()
        self.assertEqual(len(self.attempt_merge(responses)), 1)

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


    def test_header_commit_preserves_source_and_unselected_examples(self):
        config = {'workflows': ['ci.yml'], 'compiler_roots': ['package/main.roc', 'tzdb/main.roc']}
        (n.ROOT / '.github/roc-nightly.json').write_text(json.dumps(config))
        original = 'package [] { roc: "nightly-2026-09-04-c125b82" }\n# untouched\n'
        for path in config['compiler_roots'] + ['examples/main.roc']:
            target = n.ROOT / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(original)
        with patch.object(n, 'api', side_effect=[{'data': {'createCommitOnBranch': {'commit': {'oid': 'signed'}}}},
                                                {'commit': {'verification': {'verified': True}}}]) as api:
            n.signed_pin('base', 'nightly-2026-09-05-b195f5b')
        changes = api.call_args_list[0].args[1]['variables']['input']['fileChanges']['additions']
        self.assertEqual([item['path'] for item in changes], config['compiler_roots'])
        for item in changes:
            self.assertEqual(base64.b64decode(item['contents']).decode(), original.replace('nightly-2026-09-04-c125b82', 'nightly-2026-09-05-b195f5b'))
        self.assertEqual((n.ROOT / 'examples/main.roc').read_text(), original)

    def test_selected_application_preserves_released_dependencies(self):
        path = 'examples/hello/main.roc'
        config = {'workflows': ['ci.yml'], 'compiler_roots': [path]}
        (n.ROOT / '.github/roc-nightly.json').write_text(json.dumps(config))
        source = ('app [main] { roc: "nightly-2026-09-04-c125b82", '
                  'pf: platform "https://example.com/releases/1.0/platform.tar.zst", '
                  'lib: "https://example.com/releases/2.0/package.tar.zst" }\nmain = 1\n')
        target = n.ROOT / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
        with patch.object(n, 'api', side_effect=[{'data': {'createCommitOnBranch': {'commit': {'oid': 'signed'}}}},
                                                {'commit': {'verification': {'verified': True}}}]) as api:
            n.signed_pin('base', 'nightly-2026-09-05-b195f5b')
        changes = api.call_args_list[0].args[1]['variables']['input']['fileChanges']['additions']
        self.assertEqual(len(changes), 1)
        after = base64.b64decode(changes[0]['contents']).decode()
        self.assertEqual(after, source.replace('nightly-2026-09-04-c125b82', 'nightly-2026-09-05-b195f5b'))
        files = [{'filename': path, 'status': 'modified'}]
        with patch.object(n, 'sources_at', side_effect=[{path: source}, {path: after}]):
            n.verify_pin_candidate('base', 'head', files, 'nightly-2026-09-05-b195f5b', config)
        for changed in [after.replace('/1.0/', '/1.1/'), after.replace('/2.0/', '/2.1/')]:
            with patch.object(n, 'sources_at', side_effect=[{path: source}, {path: changed}]), self.assertRaises(ValueError):
                n.verify_pin_candidate('base', 'head', files, 'nightly-2026-09-05-b195f5b', config)

    def test_header_candidate_rejects_body_changes_and_extra_files(self):
        config = {'compiler_roots': ['package/main.roc']}
        old = {'package/main.roc': 'package [] {roc: "nightly-2026-09-04-c125b82"}\n# original'}
        new = {'package/main.roc': old['package/main.roc'].replace('nightly-2026-09-04-c125b82', 'nightly-2026-09-05-b195f5b')}
        files = [{'filename': 'package/main.roc', 'status': 'modified'}]
        with patch.object(n, 'sources_at', side_effect=[old, new]):
            n.verify_pin_candidate('base', 'head', files, 'nightly-2026-09-05-b195f5b', config)
        with patch.object(n, 'sources_at', side_effect=[old, {**new, 'package/main.roc': new['package/main.roc'] + '\nmalicious = 1'}]), self.assertRaises(ValueError):
            n.verify_pin_candidate('base', 'head', files, 'nightly-2026-09-05-b195f5b', config)
        with patch.object(n, 'sources_at', return_value=old), self.assertRaises(ValueError):
            n.verify_pin_candidate('base', 'head', files + [{'filename': 'examples/main.roc', 'status': 'modified'}], 'nightly-2026-09-05-b195f5b', config)


    def test_merge_checks_header_blobs_before_authorizing_merge(self):
        responses = self.merge_fixture()
        config = {'workflows': ['ci.yml', 'release.yml'], 'auto_merge': True,
                  'compiler_roots': ['package/main.roc', 'tzdb/main.roc']}
        responses['repos/owner/project/contents/.github/roc-nightly.json?ref=base']['content'] = base64.b64encode(json.dumps(config).encode()).decode()
        responses['repos/owner/project/pulls/1']['changed_files'] = 2
        responses['repos/owner/project/commits/candidate']['files'] = [
            {'filename': path, 'status': 'modified'} for path in config['compiler_roots']]
        before = 'package [] {roc: "nightly-2026-09-04-c125b82"}\n# original'
        after = before.replace('nightly-2026-09-04-c125b82', 'nightly-2026-09-05-b195f5b')
        for path in config['compiler_roots']:
            for sha, text in [('base', before), ('candidate', after)]:
                responses[f'repos/owner/project/contents/{path}?ref={sha}'] = {'type': 'file', 'content': base64.b64encode(text.encode()).decode()}
        self.assertEqual(len(self.attempt_merge(responses)), 1)
        responses['repos/owner/project/contents/tzdb/main.roc?ref=candidate']['content'] = base64.b64encode((after + '\nchanged = 1').encode()).decode()
        self.assertEqual(self.attempt_merge(responses, failure=True), [])


    def test_read_only_config_check_accepts_stable_header_roots(self):
        (n.ROOT / '.github/roc-nightly.json').write_text(json.dumps({'workflows': ['ci.yml'], 'compiler_roots': ['main.roc']}))
        (n.ROOT / 'main.roc').write_text('package [] {roc: "0.1.2"}')
        with patch.object(n, 'api') as api, patch.object(n, 'run') as run:
            n.check()
        api.assert_not_called()
        run.assert_not_called()

if __name__ == '__main__': unittest.main()
