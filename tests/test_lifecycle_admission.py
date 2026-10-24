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
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.candidate = self.root / 'candidate'
        self.candidate.mkdir()
        self.topology = json.loads((FIXTURES / 'topology-package.json').read_text())
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
        for rule in self.plan['assets']:
            if rule['role'] not in ('spdx.json', 'cdx.json', 'sbom-manifest.json'):
                self.put(rule['path'].replace('{hash}', 'a'*44), b'exact tested bytes\n')
        for rule in self.plan['assets']:
            if rule['role'] in ('bundle', 'host', 'binary'):
                self.sbom(rule['path'].replace('{hash}', 'a'*44), rule['target'])
        composition_text = json.dumps(self.composition)
        self.trusted['compositions'] = [dict(path=mapping['composition'], text=composition_text,
                                            sha256=digest(composition_text.encode())) for mapping in self.topology['sboms']]
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
        for index, (key, obligations) in enumerate(groups.items(), 1):
            first = obligations[0]
            stage, runner = first['stage'], first['runner']
            display = 'Seal' if stage == 'seal' else ('SBOM ('+first['id'].removeprefix('sbom-')+')' if stage == 'sbom' else stage.title()+' ('+runner+')')
            self.jobs.append(dict(id=index, name=display, conclusion='success', run_id=10,
                                  run_attempt=1, head_sha=self.context['sha'], labels=[runner]))
            paths = [f['path'] for f in self.files] if stage == 'seal' else []
            self.artifacts.append(dict(id=index+100, digest='sha256:'+'e'*64, job_id=index,
                                       run_attempt=1, trace_sha256='f'*64,
                                       obligations=[item['id'] for item in obligations], files=paths))
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
                             artifacts=self.artifacts, files=self.files)

    def put(self, path, data):
        file = self.candidate/path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)

    def sbom(self, path, target):
        name = Path(path).name
        sha = digest((self.candidate/path).read_bytes())
        ns = 'https://github.com/'+self.context['repository']+'/sbom/1.0.0/'+self.context['sha']+'/'+name+'/revision-1'
        identity = ns+'#Subject'
        graph = [dict(type='CreationInfo', **{'@id':'_:creationinfo'}, specVersion='3.0.1', created='2026-10-24T00:00:00Z'),
                 dict(type='SpdxDocument', spdxId=ns+'#document', creationInfo='_:creationinfo', rootElement=[ns+'#sbom']),
                 dict(type='software_Sbom', spdxId=ns+'#sbom', creationInfo='_:creationinfo', rootElement=[identity]),
                 dict(type='software_Package', spdxId=identity, creationInfo='_:creationinfo', name=name,
                      software_packageVersion='1.0.0', comment=json.dumps(dict(target=target, digest_kind='final-deliverable-sha256')),
                      verifiedUsing=[dict(type='Hash', algorithm='sha256', hashValue=sha)])]
        spdx = {'@context':'https://spdx.org/rdf/3.0.1/spdx-context.jsonld', '@graph':graph}
        properties = [dict(name='blueprint:target', value=target), dict(name='blueprint:digest-kind', value='final-deliverable-sha256')]
        component = dict(type='file', **{'bom-ref':'Subject'}, name=name, version='1.0.0', hashes=[dict(alg='SHA-256', content=sha)], properties=properties)
        cdx = dict(bomFormat='CycloneDX', specVersion='1.7', serialNumber='urn:uuid:00000000-0000-0000-0000-000000000001', version=1,
                   metadata=dict(component=component, properties=[dict(name='blueprint:namespace', value=ns),
                       dict(name='blueprint:evidence', value=json.dumps(dict(profile='blueprint-spdx-3.0-v1', target=target, coverage=[], dispositions=[])))]),
                   components=[], dependencies=[dict(ref='Subject', dependsOn=[])])
        for suffix, value in [('.spdx.json', spdx), ('.cdx.json', cdx)]:
            self.put(path+suffix, json.dumps(value).encode())
        sidecars = [dict(filename=name+suffix, format=fmt, sha256=digest((self.candidate/(path+suffix)).read_bytes()),
                         size=(self.candidate/(path+suffix)).stat().st_size)
                    for suffix, fmt in [('.spdx.json','spdx-3.0.1'), ('.cdx.json','cyclonedx-1.7')]]
        manifest = dict(profile='blueprint-spdx-3.0-v1', subject=dict(filename=name, version='1.0.0', target=target, sha256=sha,
                         size=(self.candidate/path).stat().st_size), inventory_sha256='d'*64,
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

    def test_rehashed_spdx_wrong_subject_and_dangling_relationship_are_rejected(self):
        path = next(item['path'] for item in self.files if item['role'] == 'spdx.json')
        doc = json.loads((self.candidate/path).read_text())
        doc['@graph'].append(dict(type='Relationship', spdxId='urn:extra', creationInfo='_:creationinfo',
                                  **{'from':'urn:absent','to':['urn:absent']}, relationshipType='dependsOn'))
        self.put(path, json.dumps(doc).encode()); self.rebind(path)
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
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'SBOM_COVERAGE', result.stderr)
