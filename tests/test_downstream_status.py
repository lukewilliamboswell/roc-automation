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
source='name: Nightly\non:\n  schedule:\n    - cron: "0 0 * * *"\npermissions: {}\njobs:\n  compatibility:\n    permissions:\n      contents: read\n      issues: write\n    uses: '+target+'@'+revision+' # reviewed pin\n    with:\n      runners: \'["ubuntu-24.04"]\'\n'
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
    assert request.endswith('/runs?per_page=50'), request
    resolved=[dict(path=target+'@'+revision,sha=revision)]
    if scenario == 'not_invoked': resolved=[]
    if scenario == 'wrong_revision': resolved[0]['sha']='c'*40
    def run(number=17,event='schedule',branch='main'):
        return dict(id=number,run_attempt=2,path=file,referenced_workflows=resolved,
            head_sha=head,head_branch=branch,event=event,status='completed',
            conclusion='failure' if scenario == 'manual_incompatible' else 'success',
            updated_at=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
    runs=[run(event='workflow_dispatch' if scenario.startswith('manual_') else 'schedule')]
    if scenario == 'manual_history_then_default': runs=[run(19,'workflow_dispatch'),run(17,'workflow_dispatch'),run(15)]
    if scenario == 'manual_history_then_schedule': runs=[run(19,'workflow_dispatch'),run(15)]
    if scenario == 'manual_wrong_branch': runs=[run(19,'workflow_dispatch','feature'),run(17,'workflow_dispatch')]
    if scenario == 'manual_other_event': runs=[run(19,'push'),run(17,'workflow_dispatch')]
    if scenario == 'manual_only_history': runs=[run(number,'workflow_dispatch') for number in range(100,50,-1)]
    print(json.dumps(runs))
elif '/actions/runs/' in request:
    import re
    matched=re.fullmatch(r'repos/lukewilliamboswell/aoc/actions/runs/([0-9]+)/attempts/2/jobs\?per_page=100',request)
    assert matched,request
    number=int(matched.group(1))
    attempt=1 if scenario in ['old_attempt','manual_old_attempt'] else 2
    failed=scenario == 'manual_incompatible'
    steps=[dict(name='Published examples failed',conclusion='failure')] if failed else [dict(name=name,conclusion='success')
        for name in ['Classify target evidence','All published examples passed']]
    jobs=[dict(id=71,run_id=number,run_attempt=attempt,head_sha=head,name='compatibility / Compatibility verdict',
        status='completed',conclusion='failure' if failed else 'success',steps=steps)]
    if scenario.startswith('manual_') and number != 15:
        history=scenario == 'manual_only_history' or (scenario.startswith('manual_history_then_') and number == 19)
        conclusion='skipped' if history or scenario == 'manual_skipped_report' else 'failure' if scenario == 'manual_failed_report' else 'success'
        report=dict(id=72,run_id=number,run_attempt=attempt,head_sha=head,name='compatibility / Track release compatibility',status='completed',conclusion=conclusion,steps=[])
        if scenario != 'manual_missing_report': jobs.append(report)
        if scenario == 'manual_duplicate_report': jobs.append(dict(report,id=73))
    if scenario == 'manual_bad_json': print('{'); sys.exit(0)
    print(json.dumps(dict(total_count=len(jobs)+(1 if scenario == 'manual_incomplete_inventory' else 0),jobs=jobs)))
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
            self.last_requests = requests.read_text()
            return json.loads((root / "site/downstream-status/aoc-compatibility.json").read_text())

    def test_canonical_shared_invocation_passes(self):
        badge = self.run_inventory("canonical")
        self.assertEqual(badge["color"], "brightgreen", badge)

    def test_default_selector_manual_run_passes(self):
        self.assertEqual(self.run_inventory("manual_default")["color"], "brightgreen")

    def test_historical_probe_does_not_hide_eligible_manual_or_schedule(self):
        for scenario, selected, ignored in [("manual_history_then_default", 17, 15),
                                            ("manual_history_then_schedule", 15, 17)]:
            with self.subTest(scenario=scenario):
                self.assertEqual(self.run_inventory(scenario)["color"], "brightgreen")
                self.assertIn(f"/runs/{selected}/attempts/2/jobs", self.last_requests)
                self.assertNotIn(f"/runs/{ignored}/attempts/2/jobs", self.last_requests)

    def test_other_events_and_nondefault_branches_are_not_evidence(self):
        for scenario in ("manual_wrong_branch", "manual_other_event"):
            with self.subTest(scenario=scenario):
                self.assertEqual(self.run_inventory(scenario)["color"], "brightgreen")
                self.assertNotIn("/runs/19/attempts/2/jobs", self.last_requests)

    def test_eligible_manual_failure_stays_red(self):
        self.assertEqual(self.run_inventory("manual_incompatible")["color"], "red")

    def test_unproven_manual_runs_cannot_pass(self):
        for scenario in ("manual_missing_report", "manual_skipped_report", "manual_failed_report",
                         "manual_duplicate_report", "manual_old_attempt", "manual_incomplete_inventory", "manual_bad_json"):
            with self.subTest(scenario=scenario):
                self.assertNotEqual(self.run_inventory(scenario)["color"], "brightgreen")

    def test_exhausted_candidate_window_stays_unknown(self):
        badge = self.run_inventory("manual_only_history")
        self.assertEqual(badge["color"], "lightgrey", badge)
        self.assertEqual(self.last_requests.count("/attempts/2/jobs"), 50)

    def test_spoofed_or_mismatched_origin_never_passes(self):
        for scenario in ["local_spoof", "not_invoked", "wrong_revision", "old_attempt",
                         "quoted_spoof", "escaped_quote_spoof", "doubled_quote_spoof", "flow_spoof"]:
            with self.subTest(scenario=scenario):
                badge = self.run_inventory(scenario)
                self.assertNotEqual(badge["color"], "brightgreen", badge)


if __name__ == "__main__":
    unittest.main()
