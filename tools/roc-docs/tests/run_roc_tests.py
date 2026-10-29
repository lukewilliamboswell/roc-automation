#!/usr/bin/env python3
"""Run renderer tests/builds, retaining a narrow upstream warning allowance.

Roc bad769b exits 2 after passing tests with compile warnings. These pinned
packages predate its unreachable-code diagnostic. Never accept local warnings,
a different diagnostic, a missing passing summary, or another nonzero status.
"""
import os
from pathlib import Path
import re
import subprocess
import sys

PACKAGES = {
    'BE186Swhzh5dYZjaECgLSQFb17wiLtq4UkxTmiFAPWze',
    '4W8SHzvwet9hH9qZewJ1J1CVQoH7YKA6zyijWFTB3y1w',
}
SUITES = ['highlight', 'html', 'assets', 'png', 'preparation', 'source_includes', 'build_options']


def passed_with_known_warnings(status, output):
    if status != 2 or not re.search(r'All \([1-9][0-9]*\) tests passed', output):
        return False
    return known_warnings(output)


def known_warnings(output):
    diagnostics = [line for line in output.splitlines() if line.startswith('──')]
    return bool(diagnostics) and all(
        '● unreachable code' in line and any('/.cache/roc/packages/'+package+'/' in line for package in PACKAGES)
        for line in diagnostics
    )


def build_with_known_warnings(status, output):
    return status == 2 and bool(re.search(r'(?m)^0 errors and [1-9][0-9]* warnings? found .* while successfully building:', output)) and known_warnings(output)


def main():
    if len(sys.argv) > 1:
        if sys.argv[1] != '--build' or len(sys.argv) < 4 or sys.argv[3] != 'build':
            raise SystemExit('usage: run_roc_tests.py [--build ROC build ARGS...]')
        result = subprocess.run(sys.argv[2:], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env={**os.environ, 'NO_COLOR':'1'})
        print(result.stdout, end='', flush=True)
        if build_with_known_warnings(result.returncode, result.stdout):
            print('Build succeeded; accepted pinned dependency unreachable-code warnings (Roc warning status 2).')
            return 0
        return result.returncode
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
