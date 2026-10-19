#!/usr/bin/env python3
"""Run a repository task that reports one persistent executable path."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


def locate(blueprint: str, task: str, workspace: Path) -> tuple[Path, float]:
    if not task or task.startswith('-') or len(task) > 512 or any(ord(c) < 32 for c in task):
        raise ValueError('task must be a nonempty Blueprint task name, not an option')
    command = shutil.which(blueprint)
    if command is None:
        raise ValueError('Blueprint executable is missing; run setup-blueprint first')
    started = time.perf_counter()
    result = subprocess.run([command, 'run', task], cwd=workspace, capture_output=True, text=True)
    duration = time.perf_counter() - started
    if result.stderr:
        print(result.stderr, file=sys.stderr, end='')
    if result.returncode:
        raise ValueError(f'Blueprint task {task!r} failed with status {result.returncode}')
    path = result.stdout.strip()
    if not path or len(path.splitlines()) != 1 or any(ord(c) < 32 for c in path) or not Path(path).is_absolute():
        raise ValueError('the task must print exactly one absolute executable path; resolve command -v with realpath inside the task')
    try:
        executable = Path(path).resolve(strict=True)
    except OSError as error:
        raise ValueError('task executable expired or is missing; resolve the temporary PATH link with realpath before the Blueprint task ends') from error
    if any(ord(c) < 32 or ord(c) == 127 for c in str(executable)):
        raise ValueError('resolved executable path contains control characters')
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError('the task output must name a persistent regular executable')
    return executable, duration


def main() -> None:
    env = os.environ
    workspace = Path(env.get('GITHUB_WORKSPACE', '.')).resolve()
    measure = env.get('INPUT_MEASURE', 'false')
    if measure not in ('true', 'false'):
        raise ValueError('measure must be true or false')
    label = env.get('INPUT_LABEL') or env['INPUT_TASK']
    if any(ord(c) < 32 or ord(c) == 127 for c in label):
        raise ValueError('diagnostic labels must be single lines')
    executable, seconds = locate(env.get('INPUT_BLUEPRINT_BINARY') or 'blueprint', env['INPUT_TASK'], workspace)
    digest = digest_of(executable)
    size = executable.stat().st_size
    directory = Path(tempfile.mkdtemp(prefix='roc-automation-tool-', dir=env['RUNNER_TEMP']))
    metrics = directory / 'metrics.json'
    script = Path(__file__).with_name('measure.py').resolve()
    binary = executable
    if measure == 'true':
        binary = directory / 'utility'
        outputs = [env['INPUT_OUTPUT_DIRECTORY']] if env.get('INPUT_OUTPUT_DIRECTORY') else []
        for output in outputs:
            if Path(output).is_absolute() or '..' in Path(output).parts or any(ord(c) < 32 for c in output):
                raise ValueError('output-directory must be a relative workspace path without escapes or controls')
        arguments = [str(script), '--label', label, '--metrics', str(metrics)]
        for output in outputs:
            arguments += ['--output', str(workspace / output)]
        arguments += ['--', str(executable)]
        binary.write_text(f'#!{sys.executable}\nimport runpy, sys\nsys.argv = {arguments!r} + sys.argv[1:]\nrunpy.run_path({str(script)!r}, run_name="__main__")\n', encoding='utf-8')
        binary.chmod(0o755)
    with open(env['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
        for key, value in {'binary':binary, 'executable-path':executable, 'sha256':digest, 'bytes':size, 'provision-seconds':f'{seconds:.3f}', 'metrics-path':metrics, 'measure-script':script}.items():
            output.write(f'{key}={value}\n')
    print(f'{label}: executable={executable}; SHA-256={digest}; bytes={size}; provisioning={seconds:.3f} seconds')
    if env.get('GITHUB_STEP_SUMMARY'):
        with open(env['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as summary:
            summary.write(f'\n### Utility provisioning\n\n{label}: {seconds:.3f} seconds; {size} bytes. SHA-256: `{digest}`.\n')


def digest_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        print(f'::error::{error}', file=sys.stderr)
        raise SystemExit(1) from error
