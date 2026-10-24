"""Exercise native independent admission with inert files and a GET-only API fake."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

BINARY = os.environ.get('ROC_RELEASE_ADMIT_BINARY')
PLANNER = os.environ.get('ROC_RELEASE_PLAN_BINARY')
FIXTURES = Path(__file__).parent / 'fixtures/lifecycle'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, separators=(',', ':')))


@unittest.skipUnless(BINARY and PLANNER, 'set ROC_RELEASE_ADMIT_BINARY and ROC_RELEASE_PLAN_BINARY')
class AdmissionTests(unittest.TestCase):
    topology_fixture = 'topology-package.json'
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.candidate = self.root / 'candidate'
        self.candidate.mkdir()
        self.topology = json.loads((FIXTURES / self.topology_fixture).read_text())
        self.context = dict(json.loads((FIXTURES / 'context-pr.json').read_text()),
                            event='workflow_dispatch', ref='refs/heads/main', version='1.0.0',
                            run_id='10', run_attempt='2')
        self.topology_bytes = json.dumps(self.topology).encode()
        self.context['topology_sha256'] = digest(self.topology_bytes)
        self.trusted = dict(context=self.context, changes=dict(complete=False, files=[]),
                            server_url='https://github.com', run_head_sha=self.context['sha'],
                            workflow_ref='lukewilliamboswell/roc-automation/.github/workflows/release-lifecycle.yml@' + 'c'*40,
                            workflow_sha='c'*40, publish=True, compositions=[])
        (self.root / 'topology.json').write_bytes(self.topology_bytes)
        write_json(self.root / 'context.json', self.context)
        write_json(self.root / 'changes.json', self.trusted['changes'])
        subprocess.run([PLANNER, 'plan', str(self.root/'context.json'), str(self.root/'topology.json'),
                        str(self.root/'changes.json'), str(self.root/'plan.json')], check=True, capture_output=True)
        self.plan = json.loads((self.root/'plan.json').read_text())
        self.composition = dict(profile='blueprint-spdx-3.0-v1', components=[], relationships=[],
                                coverage=[], inputs=[], unavailable_sboms=[])
        self.artifact_files = {}
        self.inventory_documents = {}
        for rule in self.plan['assets']:
            if rule['role'] not in ('spdx.json', 'cdx.json', 'sbom-manifest.json'):
                self.put(rule['path'].replace('{hash}', 'a'*44), b'exact tested bytes\n')
        for rule in self.plan['assets']:
            if rule['role'] in ('bundle', 'host', 'binary'):
                self.sbom(rule['path'].replace('{hash}', 'a'*44), rule['target'])
        composition_text = json.dumps(self.composition)
        self.trusted['compositions'] = []
        for mapping in self.topology['sboms']:
            for path, text in [(mapping['composition'], composition_text),
                               (mapping['supplier_imports'], json.dumps(dict(profile='roc-automation-sbom-imports-v1', imported_documents=[])))]:
                if path and not any(item['path'] == path for item in self.trusted['compositions']):
                    self.trusted['compositions'].append(dict(path=path, text=text, sha256=digest(text.encode())))
        self.files = [dict(path=rule['path'].replace('{hash}', 'a'*44), role=rule['role'],
                           subject=rule['subject'], target=rule['target'],
                           size=(self.candidate/rule['path'].replace('{hash}', 'a'*44)).stat().st_size,
                           sha256=digest((self.candidate/rule['path'].replace('{hash}', 'a'*44)).read_bytes()))
                      for rule in self.plan['assets']]
        groups = {}
        for item in self.plan['obligations']:
            key = item['id'] if item['stage'] == 'sbom' else item['stage'] + '-' + item['runner']
            groups.setdefault(key, []).append(item)
        self.jobs, self.artifacts, self.remotes, self.claims = [], [], [], []
        self.producer_evidence = []
        for index, (key, obligations) in enumerate(groups.items(), 1):
            first = obligations[0]
            stage, runner = first['stage'], first['runner']
            display = 'Validate release' if stage == 'validate' else ('Seal' if stage == 'seal' else ('SBOM ('+first['id'].removeprefix('sbom-')+')' if stage == 'sbom' else stage.title()+' ('+runner+')'))
            self.jobs.append(dict(id=index, name=display, conclusion='success', run_id=10,
                                  run_attempt=1, head_sha=self.context['sha'], labels=[runner]))
            paths = [f['path'] for f in self.files] if stage == 'seal' else []
            self.artifacts.append(dict(id=index+100, digest='sha256:'+'e'*64, job_id=index,
                                       run_attempt=1, trace_sha256='f'*64,
                                       obligations=[item['id'] for item in obligations], files=paths, metadata=[]))
            self.remotes.append(dict(id=index+100, name='lifecycle-'+key+'-1', digest='sha256:'+'e'*64,
                                     expired=False, workflow_run=dict(id=10, head_sha=self.context['sha'])))
            self.claims += [dict(id=item['id'], job_id=index, run_attempt=1) for item in obligations]
            directory = self.root/'traces'/('lifecycle-'+key+'-1')
            directory.mkdir(parents=True)
            trace = dict(schema_version=1, profile='roc-automation-lifecycle-trace-v1',
                         repository=self.context['repository'], source_sha=self.context['sha'], run_id='10',
                         run_attempt='1', stage=stage, runner=runner,
                         obligations=[dict(id=item['id'], outcome='passed') for item in obligations], files=paths)
            write_json(directory/(stage+'-trace.json'), trace)
            if stage == 'sbom':
                subject = next(file for file in self.files if file['subject'] == first['subject'] and file['target'] == first['target'] and file['role'] in ('bundle','host','binary'))
                sidecars = [file for file in self.files if file['subject'] == first['subject'] and file['target'] == first['target'] and file['role'] in ('spdx.json','cdx.json','sbom-manifest.json')]
                producer = dict(format='roc-automation-sbom-evidence-v1',
                    source=dict(server='https://github.com', repository=self.context['repository'], commit=self.context['sha']),
                    producer=dict(workflow=self.context['repository']+'/.github/workflows/release.yml@refs/heads/main', run_id='10', run_attempt='1'),
                    generator=dict(tool='https://github.com/lukewilliamboswell/roc-blueprint/releases/download/0.8.0/roc-blueprint-sbom-9FNPZfbSvEMXTo5QSkkPhScWzCpUnLFEgrmHm9Ev4u36.tar.zst',
                                   tool_sha256='', compiler='nightly-2026-10-21-5349569', generator='Tool: blueprint-sbom-0.8.0', blueprint='0.8.0', created='2026-10-24T00:00:00Z'),
                    subjects=[dict(name=Path(subject['path']).name, path='candidate/'+subject['path'],
                                   size=subject['size'], sha256=subject['sha256'], target=subject['target'], version='1.0.0',
                                   url='https://github.com/'+self.context['repository']+'/releases/download/1.0.0/'+Path(subject['path']).name,
                                   inventory_sha256=digest(self.inventory_documents[subject['path']].encode()),
                                   sidecars=[dict(name=Path(file['path']).name, path='candidate/'+file['path'], size=file['size'], sha256=file['sha256'],
                                                  format={'spdx.json':'SPDX-3.0.1','cdx.json':'CycloneDX-1.7','sbom-manifest.json':'manifest'}[file['role']]) for file in sidecars])],
                    dependencies=[], coverage=[])
                document = json.dumps(producer)
                inventory_document = self.inventory_documents[subject['path']]
                self.producer_evidence.append(dict(id=first['id'], sha256=digest(document.encode()), document=document,
                                                   inventory_document=inventory_document, inventory_sha256=digest(inventory_document.encode())))
                (directory/'diagnostics').mkdir()
                (directory/'diagnostics/sbom-evidence.json').write_text(document)
                (directory/'diagnostics/sbom-inventory.json').write_text(inventory_document)
                self.artifacts[-1]['metadata'].append(dict(path='diagnostics/sbom-inventory.json', size=len(inventory_document.encode()), sha256=digest(inventory_document.encode())))
                self.artifacts[-1]['metadata'].append(dict(path='diagnostics/sbom-evidence.json', size=len(document.encode()), sha256=digest(document.encode())))
            for path in paths:
                target = directory/path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.candidate/path, target)
        self.observations = dict(complete=True, jobs=self.jobs, artifacts=self.remotes)
        self.evidence = dict(schema_version=1, profile='roc-automation-release-evidence-v1',
                             repository=self.context['repository'], source_sha=self.context['sha'], run_id='10',
                             producing_attempt='1', compiler=self.context['compiler'], version='1.0.0',
                             topology_sha256=self.context['topology_sha256'], workflow_ref=self.trusted['workflow_ref'],
                             workflow_sha=self.trusted['workflow_sha'], obligations=self.claims,
                             artifacts=self.artifacts, files=self.files, producer_evidence=self.producer_evidence)

    def put(self, path, data):
        file = self.candidate/path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)

    def sbom(self, path, target):
        name = Path(path).name
        sha = digest((self.candidate/path).read_bytes())
        ns = 'https://github.com/'+self.context['repository']+'/sbom/1.0.0/'+self.context['sha']+'/'+name+'/revision-1'
        identity = ns+'#Subject'
        inventory_document = json.dumps(dict(target=target, components=[], relationships=[], coverage=[], inputs=[], inspected=[]))
        self.inventory_documents[path] = inventory_document
        semantic_document = json.dumps(dict(profile='blueprint-spdx-3.0-v1', revision='1', target=target,
                                             coverage=[], dispositions=[], inspected=[]))
        reviewed_author = self.topology.get('sbom_author', 'Organization: roc-automation contributors')
        author_kind, author_name = reviewed_author.split(': ', 1)
        graph = [dict(type='CreationInfo', **{'@id':'_:creationinfo'}, specVersion='3.0.1', created='2026-10-24T00:00:00Z', createdBy=[ns+'#author']),
                 dict(type=author_kind, spdxId=ns+'#author', name=author_name),
                 dict(type='SpdxDocument', spdxId=ns+'#spdx/document', creationInfo='_:creationinfo', rootElement=[ns+'#spdx/sbom']),
                 dict(type='software_Sbom', spdxId=ns+'#spdx/sbom', creationInfo='_:creationinfo', rootElement=[identity]),
                 dict(type='software_Package', spdxId=identity, creationInfo='_:creationinfo', name=name,
                      software_packageVersion='1.0.0', comment=json.dumps(dict(target=target, digest_kind='final-deliverable-sha256')),
                      verifiedUsing=[dict(type='Hash', algorithm='sha256', hashValue=sha)])]
        graph.append(dict(type='Annotation', spdxId=ns+'#spdx/annotation', creationInfo='_:creationinfo',
                          contentType='application/json', subject=ns+'#spdx/document', statement=semantic_document))
        spdx = {'@context':'https://spdx.org/rdf/3.0.1/spdx-context.jsonld', '@graph':graph}
        properties = [dict(name='blueprint:target', value=target), dict(name='blueprint:digest-kind', value='final-deliverable-sha256')]
        component = dict(type='file', **{'bom-ref':'Subject'}, name=name, version='1.0.0', hashes=[dict(alg='SHA-256', content=sha)], properties=properties)
        cdx = dict(bomFormat='CycloneDX', specVersion='1.7', serialNumber='urn:uuid:00000000-0000-0000-0000-000000000001', version=1,
                   metadata=dict(timestamp='2026-10-24T00:00:00Z', component=component, properties=[dict(name='blueprint:namespace', value=ns),
                       dict(name='blueprint:evidence', value=semantic_document)]),
                   components=[], dependencies=[dict(ref='Subject', dependsOn=[])])
        if author_kind == 'Person': cdx['metadata']['authors'] = [dict(name=author_name)]
        else: cdx['metadata']['manufacturer'] = dict(name=author_name)
        for suffix, value in [('.spdx.json', spdx), ('.cdx.json', cdx)]:
            self.put(path+suffix, json.dumps(value).encode())
        sidecars = [dict(filename=name+suffix, format=fmt, sha256=digest((self.candidate/(path+suffix)).read_bytes()),
                         size=(self.candidate/(path+suffix)).stat().st_size)
                    for suffix, fmt in [('.spdx.json','spdx-3.0.1'), ('.cdx.json','cyclonedx-1.7')]]
        manifest = dict(profile='blueprint-spdx-3.0-v1', subject=dict(filename=name, version='1.0.0', target=target, sha256=sha,
                         size=(self.candidate/path).stat().st_size), inventory_sha256=digest(inventory_document.encode()),
                        context=dict(namespace=ns, revision='1', created='2026-10-24T00:00:00Z', generator='Tool: blueprint-sbom-0.8.0'), sidecars=sidecars)
        self.put(path+'.sbom-manifest.json', json.dumps(manifest).encode())

    def invoke(self, *, evidence=None, observations=None, trusted=None, raw_evidence=None):
        write_json(self.root/'trusted.json', trusted or self.trusted)
        write_json(self.root/'jobs.json', observations or self.observations)
        write_json(self.root/'evidence.json', evidence or self.evidence)
        if raw_evidence is not None:
            (self.root/'evidence.json').write_text(raw_evidence)
        return subprocess.run([BINARY, 'admit', str(self.root/'trusted.json'), str(self.root/'topology.json'),
                               str(self.root/'jobs.json'), str(self.root/'evidence.json'), str(self.candidate), str(self.root/'report.json')],
                              capture_output=True, timeout=30)

    def test_exact_files_and_producing_attempt_survive_publish_only_retry(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((self.root/'report.json').read_text())
        self.assertEqual(report['producing_attempt'], '1')
        self.assertEqual(report['assets'], self.files)
        self.assertTrue(report['publish'])

    def test_wrong_source_workflow_run_attempt_and_compiler_fail(self):
        for key, value in [('source_sha','b'*40), ('workflow_sha','b'*40), ('run_id','99'),
                           ('producing_attempt','3'), ('compiler','nightly-other'), ('version','2.0.0')]:
            with self.subTest(key=key):
                result = self.invoke(evidence={**self.evidence, key:value})
                self.assertNotEqual(result.returncode, 0, result.stderr)

    def test_candidate_pass_flags_missing_actual_jobs_and_incomplete_api_cannot_authorize(self):
        for changes in [dict(jobs=self.jobs[:-1]), dict(complete=False),
                        dict(jobs=[{**job, 'conclusion':'skipped'} for job in self.jobs])]:
            result = self.invoke(observations={**self.observations, **changes})
            self.assertNotEqual(result.returncode, 0, result.stderr)

    def test_duplicate_keys_symlink_unsafe_path_extra_missing_and_swapped_bytes_fail(self):
        self.assertNotEqual(self.invoke(raw_evidence=json.dumps(self.evidence).replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1')).returncode, 0)
        target = self.candidate/self.files[0]['path']
        original = target.read_bytes()
        target.write_bytes(b'altered')
        self.assertNotEqual(self.invoke().returncode, 0)
        target.unlink(); target.symlink_to(self.candidate/self.files[1]['path'])
        self.assertNotEqual(self.invoke().returncode, 0)
        target.unlink(); target.write_bytes(original)
        self.put('undeclared', b'extra')
        self.assertNotEqual(self.invoke().returncode, 0)

    def test_expired_substituted_or_wrong_named_transport_fails(self):
        for change in [dict(expired=True), dict(digest='sha256:'+'a'*64), dict(name='other')]:
            result = self.invoke(observations={**self.observations, 'artifacts':[{**item, **change} for item in self.remotes]})
            self.assertNotEqual(result.returncode, 0)

    def rebind(self, path):
        """An adversary can update producer hashes; semantics must still reject."""
        data = (self.candidate/path).read_bytes()
        for item in self.files:
            if item['path'] == path:
                item.update(size=len(data), sha256=digest(data))
        if path.endswith(('.spdx.json', '.cdx.json')):
            base = path.removesuffix('.spdx.json').removesuffix('.cdx.json')
            manifest_path = base+'.sbom-manifest.json'
            manifest = json.loads((self.candidate/manifest_path).read_text())
            for item in manifest['sidecars']:
                if item['filename'] == Path(path).name:
                    item.update(size=len(data), sha256=digest(data))
            self.put(manifest_path, json.dumps(manifest).encode())
            self.rebind(manifest_path)

    def refresh_producers(self):
        for record in self.producer_evidence:
            producer = json.loads(record['document'])
            for subject in producer['subjects']:
                for sidecar in subject['sidecars']:
                    file = next(item for item in self.files if Path(item['path']).name == sidecar['name'])
                    sidecar.update(size=file['size'], sha256=file['sha256'])
            record['document'] = json.dumps(producer)
            record['sha256'] = digest(record['document'].encode())
            artifact = next(item for item in self.artifacts if record['id'] in item['obligations'])
            metadata = next(item for item in artifact['metadata'] if item['path'] == 'diagnostics/sbom-evidence.json')
            metadata.update(size=len(record['document'].encode()), sha256=record['sha256'])

    def test_rehashed_spdx_wrong_subject_and_dangling_relationship_are_rejected(self):
        path = next(item['path'] for item in self.files if item['role'] == 'spdx.json')
        doc = json.loads((self.candidate/path).read_text())
        doc['@graph'].append(dict(type='Relationship', spdxId='urn:extra', creationInfo='_:creationinfo',
                                  **{'from':'urn:absent','to':['urn:absent']}, relationshipType='dependsOn'))
        self.put(path, json.dumps(doc).encode()); self.rebind(path)
        self.refresh_producers()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'SBOM_SPDX', result.stderr)

    def test_rehashed_unreviewed_complete_coverage_is_rejected(self):
        path = next(item['path'] for item in self.files if item['role'] == 'cdx.json')
        doc = json.loads((self.candidate/path).read_text())
        record = next(item for item in doc['metadata']['properties'] if item['name'] == 'blueprint:evidence')
        evidence = json.loads(record['value'])
        evidence['coverage'] = [dict(scope='all-native-inputs', status='complete', reason='invented', contact='someone')]
        record['value'] = json.dumps(evidence)
        self.put(path, json.dumps(doc).encode()); self.rebind(path)
        self.refresh_producers()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'SBOM_COVERAGE', result.stderr)

    def fake_api(self):
        """Only trusted GET observations; there is no live API or candidate code."""
        workflow = self.trusted['workflow_ref']
        responses = {
            'repos/'+self.context['repository']: 'main',
            'repos/'+self.context['repository']+'/contents/release-topology.json?ref='+self.context['sha']: self.topology_bytes.decode(),
            'repos/'+self.context['repository']+'/actions/runs/10': dict(
                id=10, event='workflow_dispatch', head_sha=self.context['sha'],
                path='.github/workflows/release.yml', run_attempt=2,
                repository=dict(full_name=self.context['repository']),
                referenced_workflows=[dict(path=workflow, sha='c'*40)]),
            'repos/'+self.context['repository']+'/contents/.github/workflows/release.yml?ref='+'d'*40:
                'name: Release\njobs:\n  lifecycle:\n    uses: '+workflow+'\n',
            'repos/'+self.context['repository']+'/actions/runs/10/attempts/1/jobs?per_page=100&page=1':
                dict(total_count=len(self.jobs), jobs=self.jobs),
            'repos/'+self.context['repository']+'/actions/runs/10/attempts/2/jobs?per_page=100&page=1':
                dict(total_count=0, jobs=[]),
            'repos/'+self.context['repository']+'/actions/runs/10/artifacts?per_page=100&page=1':
                dict(total_count=len(self.remotes), artifacts=self.remotes),
        }
        for item in self.trusted['compositions']:
            responses['repos/'+self.context['repository']+'/contents/'+item['path']+'?ref='+self.context['sha']] = item['text']
        bindir = self.root/'bin'; bindir.mkdir(exist_ok=True)
        shim = bindir/'gh'
        import sys
        shim.write_text('#!'+sys.executable+'\n' + '''import json, os, sys
args = sys.argv[1:]
assert args[0] == 'api' and args[args.index('--method')+1] == 'GET', args
assert os.environ.get('GH_TOKEN') not in ' '.join(args)
path = next(arg for arg in args if arg.startswith('repos/'))
with open(os.environ['FAKE_GH_LOG'], 'a') as log: log.write(path+'\\n')
responses = json.load(open(os.environ['FAKE_GH_RESPONSES']))
if path not in responses: raise SystemExit('unexpected GET '+path)
value = responses[path]
sys.stdout.write(value if isinstance(value,str) else json.dumps(value))
''')
        shim.chmod(0o755)
        self.api_responses = responses
        self.api_env = dict(os.environ, PATH=str(bindir)+os.pathsep+os.environ['PATH'],
                            GITHUB_REPOSITORY=self.context['repository'], GITHUB_RUN_ID='10',
                            GITHUB_RUN_ATTEMPT='2', GITHUB_SHA=self.context['sha'],
                            GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REF='refs/heads/main',
                            GITHUB_SERVER_URL='https://github.com', GITHUB_WORKFLOW_SHA='d'*40,
                            GITHUB_WORKSPACE=str(self.root), GITHUB_OUTPUT=str(self.root/'github-output'),
                            GH_TOKEN='test-token-is-never-an-argument',
                            FAKE_GH_RESPONSES=str(self.root/'api.json'), FAKE_GH_LOG=str(self.root/'api.log'))
        (self.root/'github-output').write_text('')
        seed = {**self.trusted, 'workflow_ref':'', 'workflow_sha':'', 'compositions':[]}
        write_json(self.root/'seed.json', seed)

    def api_invoke(self, command='discover'):
        write_json(self.root/'api.json', self.api_responses)
        args = [BINARY, command, str(self.root/'seed.json'), str(self.root/'topology.json')]
        if command == 'collect': args += [str(self.root/'traces'), str(self.candidate)]
        return subprocess.run(args+[str(self.root/'admitted')], env=self.api_env,
                              capture_output=True, timeout=30)

    def test_discover_derives_workflow_and_collect_checks_uploaded_traces_and_each_copy(self):
        self.fake_api()
        result = self.api_invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        trusted = json.loads((self.root/'admitted/trusted.json').read_text())
        self.assertEqual(trusted['workflow_sha'], 'c'*40)
        self.assertEqual(trusted['workflow_ref'], self.trusted['workflow_ref'])
        transport = json.loads((self.root/'admitted/transport.json').read_text())
        self.assertEqual(transport['producing_attempt'], 1)
        self.assertEqual({item['id'] for item in transport['artifacts']}, {item['id'] for item in self.remotes})
        result = self.api_invoke('collect')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.root/'admitted/admission.json').read_text())['verdict'], 'admitted')
        self.assertIn('explicit-assets<<', (self.root/'github-output').read_text())
        # Alter a transported copy while leaving the final candidate unchanged.
        seal = next(directory for directory in (self.root/'traces').iterdir() if '-seal-' in directory.name)
        (seal/self.files[0]['path']).write_bytes(b'substituted in transport')
        self.assertNotEqual(self.api_invoke('collect').returncode, 0)

    def test_discover_rejects_wrong_caller_pin_default_branch_and_truncated_pagination(self):
        self.fake_api()
        path = 'repos/'+self.context['repository']+'/contents/.github/workflows/release.yml?ref='+'d'*40
        original = self.api_responses[path]
        self.api_responses[path] = original.replace('@'+'c'*40, '@main')
        self.assertNotEqual(self.api_invoke().returncode, 0)
        self.api_responses[path] = original
        self.api_responses['repos/'+self.context['repository']] = 'other'
        self.assertNotEqual(self.api_invoke().returncode, 0)
        self.api_responses['repos/'+self.context['repository']] = 'main'
        jobs = self.api_responses['repos/'+self.context['repository']+'/actions/runs/10/attempts/1/jobs?per_page=100&page=1']
        jobs['total_count'] += 1
        result = self.api_invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'incomplete actual job inventory', result.stderr)

    def test_new_candidate_generation_cannot_fall_back_to_old_success(self):
        self.fake_api()
        candidate_job = next(job for job in self.jobs if job['name'].startswith('Candidate'))
        self.api_responses['repos/'+self.context['repository']+'/actions/runs/10/attempts/2/jobs?per_page=100&page=1'] = dict(
            total_count=1, jobs=[{**candidate_job, 'id':900, 'run_attempt':2}])
        result = self.api_invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'producing job', result.stderr)

    def test_publish_only_retry_rebinds_observation_attempt_without_changing_producer(self):
        self.fake_api()
        seed = json.loads((self.root/'seed.json').read_text())
        seed['context']['run_attempt'] = '1'
        write_json(self.root/'seed.json', seed)
        result = self.api_invoke('collect')
        self.assertEqual(result.returncode, 0, result.stderr)
        trusted = json.loads((self.root/'admitted/trusted.json').read_text())
        report = json.loads((self.root/'admitted/admission.json').read_text())
        self.assertEqual(trusted['context']['run_attempt'], '2')
        self.assertEqual(report['producing_attempt'], '1')

    def test_only_bounded_diagnostics_and_prepare_bundle_map_are_allowed_as_metadata(self):
        self.fake_api()
        prepare = next(directory for directory in (self.root/'traces').iterdir() if '-prepare-' in directory.name)
        (prepare/'bundle-map.json').write_text('{}')
        (prepare/'diagnostics').mkdir(exist_ok=True)
        (prepare/'diagnostics/command.log').write_text('successful diagnostics')
        result = self.api_invoke('collect')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((self.root/'admitted/admission.json').read_text())
        self.assertTrue(any(item['path'] == 'bundle-map.json' for artifact in report['artifacts'] for item in artifact['metadata']))
        (prepare/'diagnostics/cache').mkdir()
        (prepare/'diagnostics/cache/executable').write_bytes(b'candidate executable cache')
        self.assertNotEqual(self.api_invoke('collect').returncode, 0)

    def test_discovery_reconstructs_topology_from_committed_source(self):
        self.fake_api()
        key = 'repos/'+self.context['repository']+'/contents/release-topology.json?ref='+self.context['sha']
        changed = {**self.topology, 'source_paths':['different']}
        self.api_responses[key] = json.dumps(changed)
        result = self.api_invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'committed event-source topology', result.stderr)

    def test_rehashed_producer_transfer_cannot_change_source_identity(self):
        record = self.producer_evidence[0]
        producer = json.loads(record['document'])
        producer['source']['commit'] = 'b'*40
        record['document'] = json.dumps(producer)
        self.refresh_producers()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'SBOM_PRODUCER', result.stderr)

    def test_rehashed_formats_cannot_describe_different_component_graphs(self):
        path = next(item['path'] for item in self.files if item['role'] == 'cdx.json')
        doc = json.loads((self.candidate/path).read_text())
        doc['components'].append(dict(type='library', name='extra', version='1.0.0', **{'bom-ref':'extra'}))
        doc['dependencies'].append(dict(ref='extra'))
        self.put(path, json.dumps(doc).encode()); self.rebind(path); self.refresh_producers()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'SBOM_FORMATS', result.stderr)

    def test_fully_rehashed_inventory_cannot_change_accounting(self):
        record = self.producer_evidence[0]
        inventory = json.loads(record['inventory_document'])
        inventory['inspected'].append('program:undeclared-input')
        record['inventory_document'] = json.dumps(inventory)
        record['inventory_sha256'] = digest(record['inventory_document'].encode())
        producer = json.loads(record['document'])
        producer['subjects'][0]['inventory_sha256'] = record['inventory_sha256']
        record['document'] = json.dumps(producer)
        artifact = next(item for item in self.artifacts if record['id'] in item['obligations'])
        metadata = next(item for item in artifact['metadata'] if item['path'] == 'diagnostics/sbom-inventory.json')
        metadata.update(size=len(record['inventory_document'].encode()), sha256=record['inventory_sha256'])
        name = producer['subjects'][0]['name']+'.sbom-manifest.json'
        path = next(item['path'] for item in self.files if Path(item['path']).name == name)
        manifest = json.loads((self.candidate/path).read_text())
        manifest['inventory_sha256'] = record['inventory_sha256']
        self.put(path, json.dumps(manifest).encode()); self.rebind(path); self.refresh_producers()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'SBOM_INVENTORY', result.stderr)

    def provenance_fake(self, *, reject=False, empty=False):
        import sys
        bindir = self.root/'verify-bin'; bindir.mkdir(exist_ok=True)
        executable = bindir/'gh'
        executable.write_text('#!'+sys.executable+'\n'+"""import json, os, sys
args=sys.argv[1:]
with open(os.environ['FAKE_VERIFY_LOG'],'a') as output: output.write(json.dumps(args)+'\\n')
assert args[:2] == ['attestation','verify'], args
expected={'--repo':os.environ['GITHUB_REPOSITORY'],'--signer-workflow':'lukewilliamboswell/roc-automation/.github/workflows/release-lifecycle.yml','--signer-digest':'c'*40,'--source-digest':os.environ['GITHUB_SHA'],'--source-ref':os.environ['GITHUB_REF'],'--predicate-type':'https://slsa.dev/provenance/v1','--format':'json'}
for key,value in expected.items():
    if args[args.index(key)+1] != value: raise SystemExit(1)
assert '--deny-self-hosted-runners' in args
if os.environ.get('FAKE_VERIFY_REJECT') == '1': raise SystemExit(1)
print('[]' if os.environ.get('FAKE_VERIFY_EMPTY') == '1' else '[{"verified":true}]')
""")
        executable.chmod(0o755)
        self.verify_env = dict(os.environ, PATH=str(bindir)+os.pathsep+os.environ['PATH'],
            GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REF='refs/heads/main',
            GITHUB_SHA=self.context['sha'], GITHUB_REPOSITORY=self.context['repository'], GITHUB_RUN_ID='10',
            FAKE_VERIFY_LOG=str(self.root/'verify.log'), FAKE_VERIFY_REJECT='1' if reject else '0',
            FAKE_VERIFY_EMPTY='1' if empty else '0')
        (self.root/'attestation.jsonl').write_text('{}\n')

    def verify(self):
        return subprocess.run([BINARY,'verify-provenance',str(self.root/'report.json'),
            str(self.root/'attestation.jsonl'),str(self.candidate),str(self.root/'receipts')],
            capture_output=True, env=self.verify_env, timeout=30)

    def test_provenance_constrains_every_exact_subject_source_and_signer_and_retains_receipts(self):
        self.assertEqual(self.invoke().returncode, 0)
        self.provenance_fake()
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in (self.root/'verify.log').read_text().splitlines()]
        self.assertEqual(len(calls), len(self.files))
        receipts = json.loads((self.root/'receipts/provenance.json').read_text())['receipts']
        self.assertEqual({item['path'] for item in receipts}, {item['path'] for item in self.files})
        self.assertTrue(all((self.root/'receipts'/item['receipt']).is_file() for item in receipts))

    def test_provenance_rejects_changed_bytes_before_any_request(self):
        self.assertEqual(self.invoke().returncode, 0)
        self.provenance_fake()
        self.put(self.files[0]['path'], b'different bytes after admission')
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root/'verify.log').exists())

    def test_provenance_rejects_unknown_empty_and_wrong_source_workflow_policy(self):
        self.assertEqual(self.invoke().returncode, 0)
        self.provenance_fake(reject=True)
        self.assertNotEqual(self.verify().returncode, 0)
        shutil.rmtree(self.root/'receipts')
        self.provenance_fake(empty=True)
        self.assertNotEqual(self.verify().returncode, 0)
        shutil.rmtree(self.root/'receipts')
        self.provenance_fake()
        report = json.loads((self.root/'report.json').read_text())
        report['workflow_sha'] = 'b'*40
        report['workflow_ref'] = report['workflow_ref'].replace('c'*40,'b'*40)
        write_json(self.root/'report.json',report)
        self.assertNotEqual(self.verify().returncode, 0)


@unittest.skipUnless(BINARY and PLANNER, 'set native admission and planner binaries')
class NativeAdmissionTests(AdmissionTests):
    """The same independent checks cover a multi-bundle/native target matrix."""
    topology_fixture = 'topology-platform.json'
