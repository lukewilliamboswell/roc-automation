"""Exercise the dashboard's read-only GitHub adapter with the native utility."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


BINARY = os.environ.get("ROC_DOWNSTREAM_STATUS_BINARY")


@unittest.skipUnless(BINARY and os.name != "nt", "set ROC_DOWNSTREAM_STATUS_BINARY to test the native inventory")
class DownstreamStatusTests(unittest.TestCase):
    def run_inventory(self, scenario):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            program = root / "gh"
            program.write_text("#!" + sys.executable + "\n" + r'''
import datetime,json,os,sys
args=sys.argv[1:]
assert args[:3] == ['api','--method','GET'], args
request=args[5]
with open(os.environ['FAKE_REQUESTS'], 'a') as log: log.write(request+'\n')
revision='a'*40
head='b'*40
target='lukewilliamboswell/roc-automation/.github/workflows/nightly-compatibility.yml'
file='.github/workflows/nightly.yml'
scenario=os.environ['FAKE_SCENARIO']
source='name: Nightly\non:\n  schedule:\n    - cron: "0 0 * * *"\npermissions: {}\njobs:\n  compatibility:\n    permissions:\n      contents: read\n      issues: write\n    uses: '+target+'@'+revision+'\n    with:\n      runners: \'["ubuntu-24.04"]\'\n'
if scenario == 'local_spoof':
    source+='  local:\n    name: compatibility / Compatibility verdict\n    runs-on: ubuntu-24.04\n    steps:\n      - run: echo ok\n'
if scenario in ['quoted_spoof', 'escaped_quote_spoof', 'doubled_quote_spoof', 'flow_spoof']:
    start, end = {
        'quoted_spoof': ('"[]', 'value"'),
        'escaped_quote_spoof': ('"[]\\\"', 'value"'),
        'doubled_quote_spoof': ("'[]''", "value'"),
        'flow_spoof': ('["[]",', 'value]'),
    }[scenario]
    source=source.split('    with:')[0]+'    with:\n      setup-command: '+start+'\nrun-name: hidden\n      end: '+end+'\n    if: false\n  local:\n    name: compatibility / Compatibility verdict\n    runs-on: ubuntu-24.04\n    steps:\n      - run: echo ok\n'
if request.endswith('/commits/main'):
    print(revision)
elif '/commits?per_page=1' in request:
    print(head)
elif '/git/trees/' in request:
    paths=[dict(path=file,type='blob',mode='100644')] if '/aoc/' in request else []
    print(json.dumps(dict(truncated=False,tree=paths)))
elif '/contents/' in request:
    print(source)
elif '/actions/workflows/' in request:
    resolved=[dict(path=target+'@'+revision,sha=revision)]
    if scenario == 'not_invoked': resolved=[]
    if scenario == 'wrong_revision': resolved[0]['sha']='c'*40
    print(json.dumps([dict(id=17,run_attempt=2,path=file,referenced_workflows=resolved,
        head_sha=head,head_branch='main',event='schedule',status='completed',conclusion='success',
        updated_at=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))]))
elif '/actions/runs/' in request:
    # The adapter must request exactly the selected attempt; the default jobs
    # endpoint can conflate a rerun with evidence from a different attempt.
    assert request.endswith('/actions/runs/17/attempts/2/jobs?per_page=100'),request
    attempt=1 if scenario == 'old_attempt' else 2
    jobs=[dict(id=71,run_id=17,run_attempt=attempt,head_sha=head,name='compatibility / Compatibility verdict',
        status='completed',conclusion='success',steps=[dict(name=name,conclusion='success')
        for name in ['Classify target evidence','All published examples passed']])]
    print(json.dumps(dict(total_count=1,jobs=jobs)))
else:
    assert request == 'repos/lukewilliamboswell/aoc',request
    print('main')
''')
            program.chmod(0o755)
            requests = root / "requests.txt"
            result = subprocess.run([BINARY, str(root / "site")], text=True, capture_output=True,
                env={**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"],
                     "FAKE_SCENARIO": scenario, "FAKE_REQUESTS": str(requests)}, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads((root / "site/downstream-status/aoc-compatibility.json").read_text())

    def test_canonical_shared_invocation_passes(self):
        badge = self.run_inventory("canonical")
        self.assertEqual(badge["color"], "brightgreen", badge)

    def test_spoofed_or_mismatched_origin_never_passes(self):
        for scenario in ["local_spoof", "not_invoked", "wrong_revision", "old_attempt",
                         "quoted_spoof", "escaped_quote_spoof", "doubled_quote_spoof", "flow_spoof"]:
            with self.subTest(scenario=scenario):
                badge = self.run_inventory(scenario)
                self.assertNotEqual(badge["color"], "brightgreen", badge)


if __name__ == "__main__":
    unittest.main()
