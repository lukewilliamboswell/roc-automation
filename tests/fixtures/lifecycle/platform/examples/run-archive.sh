#!/usr/bin/env bash
# A product-specific native consumer test, run only in read-only jobs.
# Shared tooling supplies the exact Roc output archive as one quoted argument.
set -u
if [[ $# != 1 ]]; then exit 125; fi
suite_examples=$(cd -- "$(dirname -- "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
clang --version >&2
if ! clang "$suite_examples/harness.c" "$1" -o "$work/native-check.exe"; then
  # Toolchain/link failures cannot masquerade as the expected negative case.
  exit 125
fi
"$work/native-check.exe"
