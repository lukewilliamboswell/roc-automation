#!/usr/bin/env python3
"""Download the released blueprint binary and admit it only by a reviewed SHA-256."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
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
    ("Windows", "X64"): "x86_64-windows",
}
MAX_BYTES = 256 * 1024 * 1024
# The platform URL in a Blueprint.roc header names the release whose binary must evaluate it.
PLATFORM_URL = re.compile(r"\bplatform\s+\"" + re.escape(RELEASES) + r"/([^/\s\"]+)/[0-9A-Za-z]+\.tar\.zst\"")
MAX_CONFIG_BYTES = 1024 * 1024
# Reviewed SHA-256 of every released binary this revision of the action installs without further input.
KNOWN_RELEASES = Path(__file__).with_name("releases.json")

Fetch = Callable[[str, Path], None]


def system_for(runner_os: str, runner_arch: str) -> str:
    system = SYSTEMS.get((runner_os, runner_arch))
    if system is None:
        raise ValueError(f"no blueprint binary is released for {runner_os} {runner_arch}")
    return system


def checked_version(version: str) -> str:
    if not VERSION.fullmatch(version):
        raise ValueError("version must be a roc-blueprint release such as 0.4.0")
    return version


def executable_name(system: str) -> str:
    return "blueprint.exe" if system == "x86_64-windows" else "blueprint"


def asset_url(version: str, system: str) -> str:
    suffix = ".exe" if system == "x86_64-windows" else ""
    return f"{RELEASES}/{checked_version(version)}/blueprint-{system}{suffix}"


def version_in(config: str) -> str:
    """The roc-blueprint release named by the platform URL in the text of a Blueprint.roc."""
    versions = sorted({match.group(1) for match in PLATFORM_URL.finditer(config)})
    if not versions:
        raise ValueError("Blueprint.roc names no roc-blueprint release; set the version input")
    if len(versions) > 1:
        raise ValueError(f"Blueprint.roc names more than one roc-blueprint release ({', '.join(versions)}); set the version input")
    return checked_version(versions[0])


def read_config(path: Path) -> str:
    try:
        with path.open("rb") as source:
            content = source.read(MAX_CONFIG_BYTES + 1)
    except OSError as error:
        raise ValueError(f"cannot read {path} to find the roc-blueprint release; check out the repository first or set the version input") from error
    if len(content) > MAX_CONFIG_BYTES:
        raise ValueError(f"{path} is larger than expected")
    return content.decode("utf-8", errors="replace")


def known_releases(path: Path = KNOWN_RELEASES) -> dict[str, dict[str, str]]:
    return json.loads(path.read_text(encoding="utf-8"))


def expected_sha256(supplied: dict[str, str], known: dict[str, dict[str, str]], version: str, system: str) -> str:
    """A SHA-256 the caller supplied for this system wins; otherwise the one this action records for the release."""
    expected = supplied.get(system, "")
    if expected:
        if not SHA256.fullmatch(expected):
            raise ValueError(f"sha256-{system} must be 64 lowercase hexadecimal characters")
        return expected
    release = known.get(version)
    if release is None:
        raise ValueError(
            f"this revision of setup-blueprint records no SHA-256 for roc-blueprint {version}; "
            f"use a revision that does, or set the sha256-{system} input"
        )
    expected = release.get(system, "")
    if not SHA256.fullmatch(expected):
        raise ValueError(f"no reviewed SHA-256 is recorded for roc-blueprint {version} on {system}; set the sha256-{system} input")
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


def private_directory(directory: Path) -> None:
    """Never follow a cache/install directory supplied as a symlink."""
    if directory.is_symlink() or (directory.exists() and getattr(directory.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise ValueError("Blueprint cache/install directory must not be a symlink")
    directory.mkdir(parents=True, exist_ok=True)
    if not directory.is_dir():
        raise ValueError("Blueprint cache/install path must be a directory")
    directory.chmod(0o700)


def open_cache(source: Path) -> int:
    if os.name != "nt":
        return os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    # OPEN_REPARSE_POINT opens the link itself, never its target. Python's
    # os.open has no Windows equivalent of O_NOFOLLOW.
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(str(source), 0x80000000, 1, None, 3, 0x00200000, None)
    if handle == wintypes.HANDLE(-1).value:
        error = ctypes.get_last_error()
        if error in (2, 3):
            raise FileNotFoundError(str(source))
        raise ctypes.WinError(error)
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        close(handle)
        raise


def copy_cached(source: Path, destination: Path) -> bool:
    """Copy only a bounded regular file; admission uses the copied bytes' hash."""
    try:
        descriptor = open_cache(source)
    except FileNotFoundError:
        return False
    except OSError as error:
        raise ValueError("Blueprint executable cache must contain a regular file, never a symlink") from error
    with os.fdopen(descriptor, "rb") as cached:
        metadata = os.fstat(cached.fileno())
        if getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("Blueprint executable cache must contain a regular file, never a symlink")
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("Blueprint executable cache must contain a regular file")
        if metadata.st_size > MAX_BYTES:
            return False
        with destination.open("wb") as output:
            remaining = MAX_BYTES
            while chunk := cached.read(min(1024 * 1024, remaining + 1)):
                remaining -= len(chunk)
                if remaining < 0:
                    return False
                output.write(chunk)
    return True


def install(version: str, system: str, expected: str, directory: Path, download: Fetch | None = None,
            cache_directory: Path | None = None, cache_status: list[bool] | None = None) -> Path:
    """Admit downloaded or restored bytes in a private file before exposing an executable."""
    download = download or fetch
    url = asset_url(version, system)
    private_directory(directory)
    if cache_directory is not None:
        private_directory(cache_directory.parent)
        private_directory(cache_directory)
    handle, name = tempfile.mkstemp(prefix="blueprint-", suffix=".download", dir=directory)
    os.close(handle)
    candidate = Path(name)
    try:
        hit = cache_directory is not None and copy_cached(cache_directory / "blueprint", candidate)
        if hit and not hmac.compare_digest(sha256_of(candidate), expected):
            print("Blueprint executable cache digest mismatch; fetching the reviewed release again")
            hit = False
        if not hit:
            download(url, candidate)
        actual = sha256_of(candidate)
        if not hmac.compare_digest(actual, expected):
            raise ValueError(f"SHA-256 mismatch for {url}: expected {expected}, got {actual}")
        if cache_directory is not None:
            cache_handle, cache_name = tempfile.mkstemp(prefix=".verified-", dir=cache_directory)
            try:
                with os.fdopen(cache_handle, "wb") as output, candidate.open("rb") as source:
                    shutil.copyfileobj(source, output)
                Path(cache_name).replace(cache_directory / "blueprint")
            finally:
                Path(cache_name).unlink(missing_ok=True)
        if cache_status is not None:
            cache_status.append(bool(hit))
        candidate.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
        installed = directory / executable_name(system)
        candidate.replace(installed)
        return installed
    finally:
        candidate.unlink(missing_ok=True)


def boolean_input(value: str, name: str) -> bool:
    if value not in ("true", "false"):
        raise ValueError(f"{name} must be true or false")
    return value == "true"


def cache_settings(env: dict[str, str], version: str, system: str) -> tuple[str, str]:
    """Cache only Blueprint's verified download bytes, scoped by locked tool set."""
    install_nix = boolean_input(env.get("INPUT_INSTALL_NIX", "false"), "install-nix")
    if system == "x86_64-windows" and install_nix:
        raise ValueError("install-nix is unavailable on native Windows; use Programs/Fetch/Expect environments")
    if not boolean_input(env.get("INPUT_CACHE", "false"), "cache"):
        return "", ""
    workload = env.get("INPUT_CACHE_WORKLOAD", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", workload):
        raise ValueError("cache-workload must name the tool set (1 to 80 letters, digits, '.', '_' or '-'); use distinct names for different job/matrix tool subsets")
    workspace = Path(env["GITHUB_WORKSPACE"])
    lock = workspace / "Blueprint.lock"
    if lock.is_symlink() or not lock.is_file():
        raise ValueError("artifact caching requires a regular checked-in Blueprint.lock")
    with lock.open("rb") as source:
        content = source.read(MAX_CONFIG_BYTES + 1)
    if len(content) > MAX_CONFIG_BYTES:
        raise ValueError("Blueprint.lock exceeds cache-key byte limit")
    lock_digest = hashlib.sha256(content).hexdigest()
    installer_digest = hashlib.sha256(Path(__file__).read_bytes() + KNOWN_RELEASES.read_bytes()).hexdigest()
    workflow = hashlib.sha256(env.get("GITHUB_WORKFLOW", "local").encode()).hexdigest()[:16]
    cache_home = env.get("XDG_CACHE_HOME")
    if not cache_home and system == "x86_64-windows":
        cache_home = env.get("LOCALAPPDATA")
    if not cache_home:
        home = env.get("HOME")
        if not home:
            raise ValueError("Blueprint artifact caching requires XDG_CACHE_HOME, LOCALAPPDATA on Windows, or HOME")
        cache_home = str(Path(home) / ".cache")
    if not Path(cache_home).is_absolute() or any(ord(c) < 32 or ord(c) == 127 for c in cache_home):
        raise ValueError("the Blueprint cache home must be an absolute path without control characters")
    artifacts = str(Path(cache_home) / "blueprint" / "v1" / "artifacts")
    key = f"blueprint-artifacts-v1-{system}-{version}-{workflow}-{workload}-{installer_digest}-{lock_digest}"
    return artifacts, key


def binary_cache_settings(env: dict[str, str], version: str, system: str, expected: str) -> tuple[str, str]:
    if not boolean_input(env.get("INPUT_CACHE_BINARY", "true"), "cache-binary"):
        return "", ""
    identity = f"blueprint-executable-v1-{system}-{version}-{expected}"
    directory = Path(env["RUNNER_TEMP"]) / "roc-automation-blueprint-cache" / identity
    if not directory.is_absolute() or any(ord(c) < 32 or ord(c) == 127 for c in str(directory)):
        raise ValueError("the Blueprint executable cache must be an absolute path without control characters")
    return str(directory), identity


def main(prepare: bool = False) -> None:
    env = os.environ
    system = system_for(env.get("RUNNER_OS", ""), env.get("RUNNER_ARCH", ""))
    supplied = {name: env.get("INPUT_SHA256_" + name.replace("-", "_").upper(), "").strip() for name in SYSTEMS.values()}
    version = env.get("INPUT_VERSION", "").strip()
    if version:
        checked_version(version)
    else:
        version = version_in(read_config(Path(env.get("GITHUB_WORKSPACE", ".")) / "Blueprint.roc"))
    expected = expected_sha256(supplied, known_releases(), version, system)
    artifacts, cache_key = cache_settings(env, version, system)
    binary_directory, binary_key = binary_cache_settings(env, version, system, expected)
    if prepare:
        with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"binary-cache-directory={binary_directory}\nbinary-cache-key={binary_key}\n")
        return
    started = time.perf_counter()
    directory = Path(env["RUNNER_TEMP"]) / "roc-automation-blueprint"
    cache_status: list[bool] = []
    installed = install(version, system, expected, directory,
                        cache_directory=Path(binary_directory) if binary_directory else None,
                        cache_status=cache_status)
    duration = time.perf_counter() - started
    size = installed.stat().st_size
    with open(env["GITHUB_PATH"], "a", encoding="utf-8") as path_file:
        path_file.write(f"{directory}\n")
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"blueprint-path={installed}\nsystem={system}\nversion={version}\nsha256={expected}\nbytes={size}\ninstall-seconds={duration:.3f}\nartifacts-directory={artifacts}\ncache-key={cache_key}\n")
        output.write(f"binary-cache-hit={str(cache_status[0]).lower()}\n")
    print(f"Installed blueprint {version} for {system}; SHA-256 {expected} verified; {size} bytes; installation {duration:.3f} seconds")
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write(f"\n### Blueprint bootstrap\n\nRelease `{version}`, system `{system}`; {size} bytes; {duration:.3f} seconds. Verified SHA-256: `{expected}`. Executable reused from verified cache: `{str(cache_status[0]).lower()}`.\n")


if __name__ == "__main__":
    try:
        main(prepare=sys.argv[1:] == ["--prepare"])
    except ValueError as error:
        print(f"::error::{error}", file=sys.stderr)
        raise SystemExit(1) from error
