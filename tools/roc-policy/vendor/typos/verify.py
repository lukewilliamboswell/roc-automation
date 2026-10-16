#!/usr/bin/env python3
"""Verify or refresh the pinned typos snapshot; development-only stdlib tool."""
import argparse
import csv
import hashlib
from pathlib import Path
import urllib.request

COMMIT = "631208b7aac2daa8b707f55e7331f9112b0e062d"
FILES = {
    "words.csv": ("crates/typos-dict/assets/words.csv", "57d79d4354ada7569d24e017c0233b8937c40ba4aac5ad854566f4028f99d0f2"),
    "LICENSE-MIT": ("LICENSE-MIT", "6efb0476a1cc085077ed49357026d8c173bf33017278ef440f222fb9cbcb66e6"),
}
ROOT = Path(__file__).resolve().parent


def verify(name, data):
    expected = FILES[name][1]
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f"{name}: bytes differ from reviewed snapshot ({expected})")
    if name != "words.csv":
        return
    if not data.endswith(b"\n") or b'"' in data or b"\r" in data:
        raise ValueError("dictionary is not unquoted LF-terminated CSV")
    rows = list(csv.reader(data.decode("utf-8").splitlines(), strict=True))
    if len(rows) != 93861:
        raise ValueError("unexpected correction count")
    previous = b""
    for row in rows:
        if len(row) < 2 or any(not word.isalpha() for word in row):
            raise ValueError(f"unsupported dictionary row: {row!r}")
        key = row[0].encode("utf-8")
        if key <= previous or row[0] != row[0].lower():
            raise ValueError(f"keys must be unique, sorted UTF-8, and lowercase: {row!r}")
        previous = key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="download this exact pinned snapshot and verify before replacing files")
    args = parser.parse_args()
    snapshots = {}
    for name, (source, _) in FILES.items():
        if args.fetch:
            url = f"https://raw.githubusercontent.com/crate-ci/typos/{COMMIT}/{source}"
            with urllib.request.urlopen(url, timeout=60) as response:
                data = response.read()
        else:
            data = (ROOT / name).read_bytes()
        verify(name, data)
        snapshots[name] = data
    if args.fetch:
        for name, data in snapshots.items():
            (ROOT / name).write_bytes(data)
    print("Verified typos v1.44.0: 93,861 correction entries and MIT notice")


if __name__ == "__main__":
    main()
