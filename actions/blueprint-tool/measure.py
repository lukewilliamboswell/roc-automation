#!/usr/bin/env python3
"""Optional host-side measurements; no browser/PDF acceptance work is included."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time


def output_sizes(paths: list[str]) -> dict[str, dict[str, int]]:
    result = {}
    for name in paths:
        root = Path(name)
        files = [root] if root.is_file() else root.rglob('*') if root.is_dir() else []
        count = size = 0
        for path in files:
            if path.is_file() and not path.is_symlink():
                count += 1
                if count > 100000:
                    raise ValueError('output measurement exceeds file-count limit')
                size += path.stat().st_size
        result[name] = {'files': count, 'bytes': size}
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', required=True)
    parser.add_argument('--metrics', required=True)
    parser.add_argument('--output', action='append', default=[])
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('supply a command after --')
    started = time.perf_counter()
    completed = subprocess.run(command)
    elapsed = time.perf_counter() - started
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    rss = int(usage.ru_maxrss / 1024) if sys.platform == 'darwin' else int(usage.ru_maxrss)
    record = {'label':args.label, 'seconds':elapsed, 'max-rss-kib':rss, 'exit-code':completed.returncode, 'outputs':{}}
    measurement_error = False
    try:
        record['outputs'] = output_sizes(args.output)
    except (OSError, ValueError) as error:
        record['measurement-error'] = str(error)
        measurement_error = True
    path = Path(args.metrics)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2)+'\n', encoding='utf-8')
    print(f'{args.label}: command={elapsed:.3f} seconds; max RSS={rss} KiB; exit={completed.returncode}; outputs={record["outputs"]}', file=sys.stderr)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as summary:
            summary.write(f'\n### Command measurement\n\n{args.label}: {elapsed:.3f} seconds; maximum RSS {rss} KiB; exit {completed.returncode}.\n')
    status = completed.returncode if completed.returncode >= 0 else 128 - completed.returncode
    return status or int(measurement_error)


if __name__ == '__main__':
    raise SystemExit(main())
