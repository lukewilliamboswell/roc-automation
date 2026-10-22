"""Exercise the published Blueprint and shared handoff in a read-only runner.

The cache action itself runs in the workflow. This checks the bytes and task
behavior after restore; it never treats a cache-hit flag as verification.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tarfile

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT/'examples/pure-package'


def program_pin(report, name, system):
    """Read only the versioned public projection, never private lock hints."""
    assert isinstance(report, dict) and type(report.get('format')) is int and report['format'] == 2, 'unsupported lock report format'
    entries = report.get('fetched')
    assert isinstance(entries, list), 'lock report fetched must be an array'
    assert all(isinstance(entry, dict) for entry in entries), 'invalid fetched entry'
    selected = [entry for entry in entries if entry.get('name') == name and entry.get('system') == system]
    assert len(selected) == 1, f'expected one locked {name} on {system}'
    entry = selected[0]
    digest, size = entry.get('sha256'), entry.get('size')
    assert isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest), 'invalid program digest'
    assert entry.get('content_identity') == 'sha256:' + digest, 'program content identity disagrees'
    assert type(size) is int and 0 < size <= 2**64 - 1, 'invalid program size'
    return digest, size


def locked_program(name, system):
    result = subprocess.run(['blueprint', 'lock', 'show', '--json', name], cwd=ROOT,
                            capture_output=True, text=True, check=True, timeout=30)
    assert len(result.stdout.encode('utf-8')) <= 1024**2, 'oversized lock report'
    return program_pin(json.loads(result.stdout), name, system)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workload', choices=['docs','fixed-tools'], required=True)
    parser.add_argument('--warm', action='store_true')
    args = parser.parse_args()
    temporary = Path(tempfile.mkdtemp(prefix='blueprint-adoption-', dir=os.environ['RUNNER_TEMP']))
    task = 'docs-path' if args.workload == 'docs' else 'tooling-path'
    output = temporary/'outputs'
    env = {**os.environ, 'INPUT_TASK':task, 'INPUT_MEASURE':'true', 'INPUT_LABEL':args.workload, 'INPUT_OUTPUT_DIRECTORY':'examples/pure-package/.docs-out' if args.workload == 'docs' else '', 'GITHUB_OUTPUT':str(output)}
    env.pop('ROC', None)
    result = subprocess.run([sys.executable,str(ROOT/'actions/blueprint-tool/blueprint_tool.py')],env=env,cwd=ROOT,capture_output=True,text=True)
    print(result.stdout,end=''); print(result.stderr,end='',file=sys.stderr)
    assert result.returncode == 0
    values = dict(line.split('=',1) for line in output.read_text().splitlines())
    system = {'Linux':{'X64':'x86_64-linux','ARM64':'aarch64-linux'},'macOS':{'ARM64':'aarch64-darwin'}}[env['RUNNER_OS']][env['RUNNER_ARCH']]
    name = 'roc-docs' if args.workload == 'docs' else 'roc-stable'
    digest, size = locked_program(name, system)
    actual = Path(values['executable-path'])
    assert values['sha256'] == hashlib.sha256(actual.read_bytes()).hexdigest()
    assert int(values['bytes']) == actual.stat().st_size
    store = Path(env.get('XDG_CACHE_HOME', str(Path.home()/'.cache')))/'blueprint/v1/artifacts/sha256'
    artifacts = list(store.glob(digest+'*'))
    assert len(artifacts) == 1, artifacts
    artifact = artifacts[0]
    assert artifact.stat().st_size == size
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == digest
    if args.workload == 'docs':
        assert values['sha256'] == digest and int(values['bytes']) == size
    else:
        # A lock pins the archive, not its larger unpacked executable. Read the
        # released member independently rather than comparing unlike digests.
        with tarfile.open(artifact, 'r:gz') as archive:
            members = [member for member in archive.getmembers() if member.isfile() and member.name.endswith('/roc')]
            assert len(members) == 1
            assert hashlib.sha256(archive.extractfile(members[0]).read()).hexdigest() == values['sha256']
    assert '.blueprint/run/' not in str(actual)
    if args.warm:
        assert not re.search(r'blueprint: fetching (?:roc|roc-docs|roc-stable) (?:from|for)',result.stderr),result.stderr
    command = [values['binary'],'build','--workspace',str(FIXTURE),'--pdf-filename','example.pdf'] if args.workload == 'docs' else [values['binary'],'version']
    subprocess.run(command,env=env,check=True)
    metrics = json.loads(Path(values['metrics-path']).read_text())
    assert metrics['exit-code'] == 0 and metrics['max-rss-kib'] > 0
    if args.workload == 'docs':
        assert (FIXTURE/'.docs-out/site/index.html').is_file()
        assert (FIXTURE/'.docs-out/site/index.md').is_file()
        assert 'index.md' in (FIXTURE/'.docs-out/site/llms.txt').read_text()
        assert (FIXTURE/'.docs-out/example.pdf').read_bytes().startswith(b'%PDF-2.0')
        assert metrics['outputs'][str(FIXTURE/'.docs-out')]['files'] > 1
    if args.warm and args.workload == 'docs':
        # A cache hit must not bypass hashing or reuse a tree derived from
        # corrupted bytes. The published replacement is verified again.
        artifact.write_bytes(b'corrupt restored cache')
        repaired = subprocess.run([sys.executable,str(ROOT/'actions/blueprint-tool/blueprint_tool.py')],env=env,cwd=ROOT,capture_output=True,text=True)
        print(repaired.stderr, end='', file=sys.stderr)
        assert repaired.returncode == 0, repaired.stdout+repaired.stderr
        assert 'blueprint: fetching roc-docs' in repaired.stderr
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == digest
        assert hashlib.sha256(actual.read_bytes()).hexdigest() == values['sha256']
        subprocess.run(command,env=env,check=True)
    marker = os.environ.get('NIX_PROBE_MARKER')
    assert not marker or not Path(marker).exists(), 'a pure package invoked Nix'
    print(f'{args.workload}: actual executable agrees with the reviewed lock; warm={args.warm}; process and outputs verified')


if __name__ == '__main__': main()
