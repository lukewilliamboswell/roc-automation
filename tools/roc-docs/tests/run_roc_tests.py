#!/usr/bin/env python3
"""Run the pinned renderer suites, retaining a narrow upstream warning allowance.

Roc 1df370f exits 2 after passing tests with compile warnings. These pinned
packages predate its unreachable-code diagnostic. Never accept local warnings,
a different diagnostic, a missing passing summary, or another nonzero status.
"""
import os
from pathlib import Path
import re
import subprocess
import sys

PACKAGES = {
    'E5r48puF3KGvqzbd6v1JWA7qJFMehppWb7A6NzRWieJT',
    '4W8SHzvwet9hH9qZewJ1J1CVQoH7YKA6zyijWFTB3y1w',
    'BRKYfdjzNFJmhx5c3Do9gTnW7FrZuYqoUzMcg1FHkYBf',
}
SUITES = ['highlight', 'html', 'assets', 'png', 'preparation', 'source_includes', 'build_options']


def passed_with_known_warnings(status, output):
    if status != 2 or not re.search(r'All \([1-9][0-9]*\) tests passed', output):
        return False
    diagnostics = [line for line in output.splitlines() if line.startswith('──')]
    return bool(diagnostics) and all(
        '● unreachable code' in line and any('/.cache/roc/packages/'+package+'/' in line for package in PACKAGES)
        for line in diagnostics
    )


def main():
    compiler = os.environ.get('ROC', 'roc')
    root = Path(__file__).resolve().parents[1]
    for suite in SUITES:
        result = subprocess.run([compiler, 'test', str(root/f'test_{suite}.roc')], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env={**os.environ, 'NO_COLOR':'1'})
        print(result.stdout, end='', flush=True)
        if result.returncode and not passed_with_known_warnings(result.returncode, result.stdout):
            return result.returncode
        if result.returncode:
            print('Tests passed; accepted pinned dependency unreachable-code warnings (Roc warning status 2).', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
