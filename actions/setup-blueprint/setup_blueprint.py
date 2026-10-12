#!/usr/bin/env python3
"""Download the released blueprint binary and admit it only by a reviewed SHA-256."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import stat
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable

RELEASES = "https://github.com/lukewilliamboswell/roc-blueprint/releases/download"
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z]+(\.[0-9A-Za-z]+)*)?")
SHA256 = re.compile(r"[0-9a-f]{64}")
# Runner (RUNNER_OS, RUNNER_ARCH) to the system name used in release asset names.
SYSTEMS = {
    ("Linux", "X64"): "x86_64-linux",
    ("Linux", "ARM64"): "aarch64-linux",
    ("macOS", "ARM64"): "aarch64-darwin",
}
MAX_BYTES = 256 * 1024 * 1024

Fetch = Callable[[str, Path], None]


def system_for(runner_os: str, runner_arch: str) -> str:
    system = SYSTEMS.get((runner_os, runner_arch))
    if system is None:
        raise ValueError(f"no blueprint binary is released for {runner_os} {runner_arch}")
    return system


def asset_url(version: str, system: str) -> str:
    if not VERSION.fullmatch(version):
        raise ValueError("version must be a roc-blueprint release such as 0.4.0")
    return f"{RELEASES}/{version}/blueprint-{system}"


def expected_sha256(checksums: dict[str, str], system: str) -> str:
    expected = checksums.get(system, "")
    if not expected:
        raise ValueError(f"no reviewed SHA-256 was supplied for {system}; set the sha256-{system} input")
    if not SHA256.fullmatch(expected):
        raise ValueError(f"sha256-{system} must be 64 lowercase hexadecimal characters")
    return expected


def fetch(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "roc-automation-setup-blueprint"})
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as output:
        remaining = MAX_BYTES
        while chunk := response.read(1024 * 1024):
            remaining -= len(chunk)
            if remaining < 0:
                raise ValueError("the downloaded binary is larger than expected")
            output.write(chunk)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def install(version: str, system: str, expected: str, directory: Path, download: Fetch | None = None) -> Path:
    """Download into a private temporary file; the binary becomes visible only after its hash matches."""
    download = download or fetch
    url = asset_url(version, system)
    directory.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix="blueprint-", suffix=".download", dir=directory)
    os.close(handle)
    candidate = Path(name)
    try:
        download(url, candidate)
        actual = sha256_of(candidate)
        if not hmac.compare_digest(actual, expected):
            raise ValueError(f"SHA-256 mismatch for {url}: expected {expected}, got {actual}")
        candidate.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
        installed = directory / "blueprint"
        candidate.replace(installed)
        return installed
    finally:
        candidate.unlink(missing_ok=True)


def main() -> None:
    env = os.environ
    system = system_for(env.get("RUNNER_OS", ""), env.get("RUNNER_ARCH", ""))
    checksums = {name: env.get("INPUT_SHA256_" + name.replace("-", "_").upper(), "").strip() for name in SYSTEMS.values()}
    expected = expected_sha256(checksums, system)
    version = env.get("INPUT_VERSION", "").strip()
    directory = Path(env["RUNNER_TEMP"]) / "roc-automation-blueprint"
    installed = install(version, system, expected, directory)
    with open(env["GITHUB_PATH"], "a", encoding="utf-8") as path_file:
        path_file.write(f"{directory}\n")
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"blueprint-path={installed}\nsystem={system}\n")
    print(f"Installed blueprint {version} for {system}; SHA-256 {expected} verified")


if __name__ == "__main__":
    try:
        main()
    except ValueError as error:
        print(f"::error::{error}", file=sys.stderr)
        raise SystemExit(1) from error
