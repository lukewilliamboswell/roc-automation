"""Native acceptance of the Roc sweep engine, without downloads.

Build main.roc with the latest nightly and set DOWNSTREAM_RUNNER to that binary.
"""
import datetime
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.environ.get('DOWNSTREAM_RUNNER'), 'set DOWNSTREAM_RUNNER to the compiled Roc runner')
class RunnerTests(unittest.TestCase):
    def exercise(self, failure, version='nightly-2026-11-18-abcdef0', source_build=False):
        with tempfile.TemporaryDirectory(prefix='downstream runner ') as temporary:
            root = Path(temporary)
            tool = root / 'tools/downstream-regressions'
            tool.mkdir(parents=True)
            cache = root / 'cache'
            cache.mkdir()
            source = root / 'released'
            source.mkdir()
            (source / 'main.roc').write_text('immutable source\n')
            stale = source / 'sweep-app'
            stale.write_text('#!/bin/sh\necho stale\n')
            stale.chmod(0o755)
            compiler = root / 'fake roc'
            compiler.write_text('''#!/usr/bin/env python3
import json, os, pathlib, signal, sys
stage = sys.argv[1]
with open(os.environ['COMMAND_LOG'], 'a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')
if stage == 'version': print('Roc compiler version ' + os.environ['COMPILER_VERSION']); sys.exit(0)
assert not os.environ.get('GH_TOKEN') and not os.environ.get('GITHUB_TOKEN')
if stage == 'check': sys.exit(1)
if stage == 'test' and os.environ.get('FAILURE') == 'signal': os.kill(os.getpid(), signal.SIGTERM)
if stage == 'test' and os.environ.get('FAILURE') == 'output': sys.stdout.write('x'*10000001); sys.stdout.flush()
if stage == 'build':
 if os.environ.get('FAILURE') == 'build': sys.exit(1)
 if os.environ.get('FAILURE') == 'no-output': sys.exit(0)
 output = pathlib.Path(next(a.split('=',1)[1] for a in sys.argv if a.startswith('--output=')))
 output.write_text('#!/usr/bin/env python3\\nprint("ok")\\n'); output.chmod(0o755)
if stage == 'run':
 assert sys.argv[-1] == '--flag=some value'
 assert sys.stdin.read() == 'input\\n'
 print('ok')
''')
            compiler.chmod(0o755)
            source_date = '2026-11-18' if source_build else ''
            source_key = f'source|{"a"*40}|{source_date}|' if source_build else ''
            manifest = {'cache':str(cache),'output':str(root/'report.html'),
                        'observations':str(cache/'observations.json'),'corpus_hash':'corpus','host':'test',
                        'nightlies':[{'path':str(compiler),'sha256':'compiler',
                                      'source_date':source_date,'source_key':source_key}],
                        'apps':[{'id':'fixture','repository':'test/repo','release':'1.0.0','date':'2026-11-01',
                                 'root':str(source),'path':'main.roc','cwd':'.','note':'','skip':'','expectations':[],
                                 'cases':[{'name':'default','args':['--flag=some value'],'stdin':'input\n',
                                           'stdout':'ok\n','stderr':'','exit_code':0,'skip':''}]}]}
            if failure == 'predates': manifest['apps'][0]['date'] = '2026-11-20'
            if failure == 'grace': manifest['apps'][0]['date'] = '2026-11-19'
            manifest['apps'][0]['earliest_nightly'] = (datetime.date.fromisoformat(manifest['apps'][0]['date']) - datetime.timedelta(days=1)).isoformat()
            (root/'manifest.json').write_text(json.dumps(manifest))
            (tool/'support.py').write_text('''import json, pathlib, shutil, sys
op = sys.argv[1]
if op == 'prepare': print(pathlib.Path('manifest.json').read_text())
if op == 'compiler-release-date':
 version = sys.stdin.read()
 assert version in ('Roc compiler version unrecognized', 'Roc compiler version 0.1.0_RC2')
 if version.endswith('_RC2'): print('2026-11-18')
if op == 'save': pathlib.Path(sys.argv[2]).write_text(sys.stdin.read())
if op == 'copy':
 p = pathlib.Path(sys.argv[3]); shutil.rmtree(p, ignore_errors=True); shutil.copytree(sys.argv[2], p)
if op == 'render': pass
''')
            log = root/'commands.jsonl'
            env = {**os.environ, 'COMMAND_LOG':str(log), 'FAILURE':failure, 'COMPILER_VERSION':version,
                   'GH_TOKEN':'must be cleared', 'GITHUB_TOKEN':'must be cleared'}
            runner = [os.environ['DOWNSTREAM_RUNNER'], 'config.json']
            if version == '0.1.0_RC2':
                unsupported = subprocess.run(runner, cwd=root, env={**env, 'COMPILER_VERSION':'unrecognized'},
                                             capture_output=True, text=True, timeout=30)
                self.assertEqual(unsupported.returncode,0,unsupported.stdout+unsupported.stderr)
                skipped = json.loads((cache/'observations.json').read_text())
                self.assertEqual([r['status'] for r in skipped],['skipped'])
                log.unlink()
            result = subprocess.run(runner, cwd=root,
                                    env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            if failure == 'predates':
                self.assertEqual([c[0] for c in commands], ['version'])
                records = json.loads((cache/'observations.json').read_text())
                self.assertEqual(len(records),1)
                self.assertEqual(records[0]['status'],'not-applicable')
                return
            self.assertEqual([c[0] for c in commands], ['version','check','test','build','run'])
            records = json.loads((cache/'observations.json').read_text())
            self.assertEqual([r['stage'] for r in records],['check','test','build','run','binary'])
            self.assertTrue(all(r['version'] == f'Roc compiler version {version}' and
                                r['nightly_date'] == '2026-11-18' for r in records))
            self.assertEqual(records[0]['code'],1)
            self.assertEqual(records[3]['stdout'],'ok\n')
            if failure == 'build': self.assertEqual(records[4]['status'],'blocked')
            elif failure == 'no-output': self.assertEqual(records[4]['status'],'io-error')
            else: self.assertEqual(records[4]['stdout'],'ok\n')
            if failure == 'signal': self.assertEqual(records[1]['status'],'signaled')
            if failure == 'output':
                self.assertEqual(records[1]['status'],'output-limit')
                self.assertGreater(len(records[1]['stdout']),0)
            self.assertEqual((source/'main.roc').read_text(),'immutable source\n')
            # Complete observations are reused without rebuilding discarded scratch files.
            again = subprocess.run(runner, cwd=root,
                                   env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(again.returncode,0,again.stdout+again.stderr)
            after = [json.loads(line)[0] for line in log.read_text().splitlines()]
            self.assertEqual(after, ['version','check','test','build','run','version'])
            if source_build:
                self.assertTrue(all(r['key'].startswith('corpus|test|' + source_key) for r in records))
                replacement = '2026-11-19'
                manifest['nightlies'][0]['source_date'] = replacement
                manifest['nightlies'][0]['source_key'] = source_key.replace(source_date, replacement)
                (root/'manifest.json').write_text(json.dumps(manifest))
                log.unlink()
                changed = subprocess.run(runner, cwd=root, env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(changed.returncode,0,changed.stdout+changed.stderr)
                repeated = [json.loads(line)[0] for line in log.read_text().splitlines()]
                self.assertEqual(repeated, ['version','check','test','build','run'])


    def test_failures_do_not_suppress_later_stages(self):
        self.exercise('')

    def test_failed_build_blocks_only_binary(self):
        self.exercise('build')

    def test_signal_is_observed_and_later_stages_run(self):
        self.exercise('signal')

    def test_output_limit_preserves_partial_output_and_continues(self):
        self.exercise('output')

    def test_success_without_output_cannot_execute_archived_stale_binary(self):
        self.exercise('no-output')

    def test_nightly_before_grace_window_is_not_applicable(self):
        self.exercise('predates')

    def test_previous_day_nightly_is_eligible_with_24_hour_grace(self):
        self.exercise('grace')

    def test_release_candidate_uses_publication_date_and_runs_the_suite(self):
        self.exercise('grace', version='0.1.0_RC2')

    def test_source_build_retains_version_and_uses_configured_commit_date(self):
        self.exercise('grace', version='ReleaseFast-aaaaaaa', source_build=True)
