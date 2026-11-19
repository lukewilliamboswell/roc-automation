#!/usr/bin/env python3
"""Install a Roc nightly whose archive matches the digest its release publishes.

The release is read once through the GitHub API with the workflow token. The
archive is downloaded from the release's public download address without any
credential, as Blueprint does, and admitted only when its size and SHA-256
match what the release publishes. Verified archives, never extracted trees,
are kept in a content-addressed store that actions/cache can carry between
runs; restored bytes are verified again before every use.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

REPOSITORY = "roc-lang/nightlies"
API = f"https://api.github.com/repos/{REPOSITORY}/releases"
DOWNLOADS = f"https://github.com/{REPOSITORY}/releases/download"
TAG = re.compile(r"nightly-[A-Za-z0-9][A-Za-z0-9._-]{0,119}")
SHA256 = re.compile(r"[0-9a-f]{64}")
ASSET_NAME = re.compile(r"roc_nightly-[A-Za-z0-9._-]{1,180}")
# Runner (RUNNER_OS, RUNNER_ARCH) to the platform and archive type in nightly asset names.
PLATFORMS = {
    ("Linux", "X64"): ("linux_x86_64", "tar.gz"),
    ("Linux", "ARM64"): ("linux_arm64", "tar.gz"),
    ("macOS", "ARM64"): ("macos_apple_silicon", "tar.gz"),
    ("macOS", "X64"): ("macos_x86_64", "tar.gz"),
    ("Windows", "X64"): ("windows_x86_64", "zip"),
}
MAX_METADATA_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_EXTRACTED_BYTES = 4 * MAX_ARCHIVE_BYTES
MAX_MEMBERS = 100_000
ATTEMPTS = 3
USER_AGENT = "roc-automation-setup-roc-nightly"
GLUE_FILES = {"ROC_RUST_GLUE": "RustGlue.roc", "ROC_ZIG_GLUE": "ZigGlue.roc", "ROC_C_GLUE": "CGlue.roc"}
GLUE_VARIABLES = ("ROC_GLUE_PLATFORM_URL", "ROC_GLUE_PLATFORM_PACKAGE")

Fetch = Callable[[str, Path, int], None]
Request = Callable[[str, str], bytes]


class HttpsOnly(urllib.request.HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith("https://"):
            raise urllib.error.URLError(f"refusing a redirect away from HTTPS to {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(HttpsOnly)


def platform_for(runner_os: str, runner_arch: str) -> tuple[str, str]:
    platform = PLATFORMS.get((runner_os, runner_arch))
    if platform is None:
        raise ValueError(f"no Roc nightly is published for {runner_os} {runner_arch}")
    return platform


def checked_tag(tag: str) -> str:
    if not TAG.fullmatch(tag):
        raise ValueError(f"{tag!r} is not a Roc nightly tag such as nightly-2026-11-18-a520eed")
    return tag


def retrying(action: Callable[[], bytes | None], what: str):
    """Retry transient failures; a refusal such as 403 or 404 is reported at once."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return action()
        except urllib.error.HTTPError as error:
            transient = error.code >= 500 or error.code == 429
            if not transient or attempt == ATTEMPTS:
                raise ValueError(f"{what}: HTTP {error.code} ({attempt} attempt{'s' if attempt > 1 else ''})") from error
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            if attempt == ATTEMPTS:
                raise ValueError(f"{what}: {error} ({attempt} attempts)") from error
        time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def request_metadata(url: str, token: str) -> bytes:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def ask() -> bytes:
        with OPENER.open(urllib.request.Request(url, headers=headers), timeout=60) as response:
            content = response.read(MAX_METADATA_BYTES + 1)
        if len(content) > MAX_METADATA_BYTES:
            raise ValueError("release metadata exceeds 1 MiB")
        return content

    hint = "" if token else "; no token was given, and GitHub answers 60 such requests an hour from one address"
    return retrying(ask, f"could not read {url}{hint}")


def positive(value) -> bool:
    return type(value) is int and 0 < value <= 2 ** 53


def select(metadata: dict, platform: str, extension: str, requested: str) -> dict:
    """The one compiler asset for this platform, with the identity its release publishes."""
    if not isinstance(metadata, dict):
        raise ValueError("release metadata is not an object")
    tag = metadata.get("tag_name")
    if metadata.get("draft") is not False or metadata.get("immutable") is not True or not positive(metadata.get("id")) \
            or not isinstance(tag, str):
        raise ValueError("release metadata does not describe a published immutable release")
    checked_tag(tag)
    if requested and tag != requested:
        raise ValueError(f"asked for {requested} but the release is {tag}")
    assets = metadata.get("assets")
    if not isinstance(assets, list):
        raise ValueError("release metadata lists no assets")
    prefix = f"roc_nightly-{platform}-"
    matches = [asset for asset in assets if isinstance(asset, dict) and isinstance(asset.get("name"), str)
               and asset["name"].startswith(prefix) and asset["name"].endswith("." + extension)]
    if len(matches) != 1:
        raise ValueError(f"{tag} has {len(matches)} compiler assets for {platform}; expected exactly one")
    asset = matches[0]
    name, size, digest = asset["name"], asset.get("size"), asset.get("digest")
    if not ASSET_NAME.fullmatch(name) or not positive(asset.get("id")) or not positive(size) or size > MAX_ARCHIVE_BYTES:
        raise ValueError(f"invalid compiler asset metadata for {name}")
    if not isinstance(digest, str) or not digest.startswith("sha256:") or not SHA256.fullmatch(digest[7:]):
        raise ValueError(f"{tag} publishes no SHA-256 digest for {name}")
    url = f"{DOWNLOADS}/{tag}/{name}"
    if asset.get("browser_download_url") != url:
        raise ValueError(f"{name} is not served from {url}")
    return {"tag": tag, "asset": name, "size": size, "sha256": digest[7:], "url": url,
            "directory": name[: -len("." + extension)], "extension": extension}


def resolve(env, request: Request = request_metadata) -> dict:
    platform, extension = platform_for(env.get("RUNNER_OS", ""), env.get("RUNNER_ARCH", ""))
    requested = env.get("INPUT_NIGHTLY_TAG", "").strip()
    url = f"{API}/tags/{checked_tag(requested)}" if requested else f"{API}/latest"
    try:
        metadata = json.loads(request(url, env.get("INPUT_TOKEN", "")))
    except json.JSONDecodeError as error:
        raise ValueError(f"{url} did not answer with JSON") from error
    selection = select(metadata, platform, extension, requested)
    selection["platform"] = platform
    return selection


def boolean_input(value: str, name: str) -> bool:
    if value not in ("true", "false"):
        raise ValueError(f"{name} must be true or false")
    return value == "true"


def safe_path(value: str, what: str) -> Path:
    if not value or not Path(value).is_absolute() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"{what} must be an absolute path without control characters")
    return Path(value)


def store_directory(env, sha256: str, windows: bool) -> Path:
    """Where verified archives are kept, beside Blueprint's store: XDG_CACHE_HOME, then LOCALAPPDATA on Windows, then .cache in HOME."""
    home = env.get("XDG_CACHE_HOME") or (windows and env.get("LOCALAPPDATA")) or str(Path(env.get("HOME", "")) / ".cache")
    return safe_path(home, "the cache home") / "roc-automation" / "roc-nightly" / "v1" / "sha256" / sha256


def private_directory(directory: Path) -> None:
    if directory.is_symlink():
        raise ValueError(f"{directory} must not be a symlink")
    directory.mkdir(parents=True, exist_ok=True)
    if not directory.is_dir():
        raise ValueError(f"{directory} must be a directory")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(url: str, destination: Path, size: int) -> None:
    """Download public release bytes with no credential, stopping past the published size."""
    def download() -> None:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"})
        with OPENER.open(request, timeout=300) as response, destination.open("wb") as output:
            remaining = size
            while chunk := response.read(1024 * 1024):
                remaining -= len(chunk)
                if remaining < 0:
                    raise ValueError(f"{url} served more than the {size} bytes its release publishes")
                output.write(chunk)

    retrying(download, f"could not download {url}")


def copy_cached(source: Path, destination: Path, size: int) -> bool:
    """Copy a restored archive only when it is a regular file of the published size."""
    if source.is_symlink():
        return False
    try:
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    except OSError:
        return False
    with os.fdopen(descriptor, "rb") as cached:
        metadata = os.fstat(cached.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != size:
            return False
        with destination.open("wb") as output:
            shutil.copyfileobj(cached, output, 1024 * 1024)
    return True


def admitted(path: Path, selection: dict) -> bool:
    return path.stat().st_size == selection["size"] and hmac.compare_digest(sha256_of(path), selection["sha256"])


def member_parts(name: str, top: str) -> tuple[str, ...]:
    """Every archive member must lie inside the compiler directory the asset is named for."""
    if "\\" in name or name.startswith("/") or re.match(r"[A-Za-z]:", name):
        raise ValueError(f"archive member {name!r} is not a relative path")
    parts = tuple(part for part in PurePosixPath(name).parts if part != ".")
    if not parts or parts[0] != top or ".." in parts:
        raise ValueError(f"archive member {name!r} is outside {top}/")
    return parts


def extract(archive: Path, selection: dict, destination: Path) -> Path:
    top = selection["directory"]
    total = 0
    if selection["extension"] == "zip":
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            if len(members) > MAX_MEMBERS:
                raise ValueError("the compiler archive has too many members")
            for member in members:
                member_parts(member.filename, top)
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise ValueError(f"archive member {member.filename!r} is a symlink")
                total += member.file_size
            if total > MAX_EXTRACTED_BYTES:
                raise ValueError("the compiler archive expands beyond the extraction limit")
            bundle.extractall(destination)
    else:
        with tarfile.open(archive, "r:gz") as bundle:
            members = bundle.getmembers()
            if len(members) > MAX_MEMBERS:
                raise ValueError("the compiler archive has too many members")
            for member in members:
                member_parts(member.name, top)
                if not (member.isfile() or member.isdir()):
                    raise ValueError(f"archive member {member.name!r} is neither a file nor a directory")
                total += member.size
            if total > MAX_EXTRACTED_BYTES:
                raise ValueError("the compiler archive expands beyond the extraction limit")
            bundle.extractall(destination, members=members, filter="data")
    root = destination / top
    executable = root / ("roc.exe" if selection["extension"] == "zip" else "roc")
    if root.is_symlink() or not root.is_dir() or executable.is_symlink() or not executable.is_file():
        raise ValueError(f"the archive does not contain {top}/{executable.name}")
    return root


def glue_environment(root: Path) -> dict[str, str]:
    """The glue paths and variables a nightly ships for platform CI, as setup-roc exposes them."""
    glue = root / "glue"
    if glue.is_symlink() or not glue.is_dir():
        return {}
    values = {"ROC_GLUE_DIR": str(glue)}
    for variable, name in GLUE_FILES.items():
        if (glue / name).is_file():
            values[variable] = str(glue / name)
    env_file = glue / "env"
    if env_file.is_file() and env_file.stat().st_size <= 64 * 1024:
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            name, _, value = line.partition("=")
            if name in GLUE_VARIABLES and not any(ord(c) < 32 or ord(c) == 127 for c in value):
                values[name] = value
    return values


def install(selection: dict, install_root: Path, store: Path | None, download: Fetch | None = None) -> tuple[Path, bool]:
    """Admit restored or downloaded bytes, keep them in the store, and extract a fresh tree."""
    download = download or fetch
    private_directory(install_root)
    scratch = Path(tempfile.mkdtemp(prefix="roc-nightly-", dir=install_root))
    candidate = scratch / selection["asset"]
    hit = False
    if store is not None:
        private_directory(store)
        if copy_cached(store / selection["asset"], candidate, selection["size"]):
            hit = admitted(candidate, selection)
            if not hit:
                print("The cached Roc archive does not match its published digest; downloading it again")
    if not hit:
        candidate.unlink(missing_ok=True)
        download(selection["url"], candidate, selection["size"])
        if not admitted(candidate, selection):
            raise ValueError(f"{selection['url']} does not match the size {selection['size']} and SHA-256 {selection['sha256']} {selection['tag']} publishes")
        if store is not None:
            handle, name = tempfile.mkstemp(prefix=".verified-", dir=store)
            try:
                with os.fdopen(handle, "wb") as output, candidate.open("rb") as source:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                Path(name).replace(store / selection["asset"])
            finally:
                Path(name).unlink(missing_ok=True)
    root = extract(candidate, selection, scratch)
    candidate.unlink()
    return root, hit


SELECTION_FIELDS = ("tag", "platform", "asset", "size", "sha256", "url", "directory", "extension")


def selection_from(env) -> dict:
    """Read back, and check again, what the prepare step selected."""
    selection = {field: env.get("SELECTED_" + field.upper(), "") for field in SELECTION_FIELDS}
    platform, extension = platform_for(env.get("RUNNER_OS", ""), env.get("RUNNER_ARCH", ""))
    if selection["platform"] != platform or selection["extension"] != extension:
        raise ValueError("the selected asset is for another platform")
    checked_tag(selection["tag"])
    if not selection["size"].isdigit() or not 0 < int(selection["size"]) <= MAX_ARCHIVE_BYTES:
        raise ValueError("the selected size is not a positive byte count")
    selection["size"] = int(selection["size"])
    if not SHA256.fullmatch(selection["sha256"]) or not ASSET_NAME.fullmatch(selection["asset"]) \
            or not selection["asset"].startswith(f"roc_nightly-{platform}-") \
            or selection["asset"] != f"{selection['directory']}.{extension}" \
            or selection["url"] != f"{DOWNLOADS}/{selection['tag']}/{selection['asset']}":
        raise ValueError("the selected asset identity is malformed")
    return selection


def write_lines(path: str, lines: dict[str, str]) -> None:
    for name, value in lines.items():
        if "\n" in value or "\r" in value:
            raise ValueError(f"{name} contains a line break")
    with open(path, "a", encoding="utf-8") as output:
        output.writelines(f"{name}={value}\n" for name, value in lines.items())


def main(prepare: bool) -> None:
    env = os.environ
    cache = boolean_input(env.get("INPUT_CACHE", "true"), "cache")
    windows = env.get("RUNNER_OS") == "Windows"
    if prepare:
        selection = resolve(env)
        store = store_directory(env, selection["sha256"], windows) if cache else None
        write_lines(env["GITHUB_OUTPUT"], {field: str(selection[field]) for field in SELECTION_FIELDS} | {
            "store": str(store or ""),
            "cache-key": f"roc-nightly-v1-{selection['platform']}-{selection['sha256']}" if cache else "",
        })
        print(f"Selected {selection['asset']} of {selection['tag']}: {selection['size']} bytes, SHA-256 {selection['sha256']}")
        return
    selection = selection_from(env)
    store = store_directory(env, selection["sha256"], windows) if cache else None
    if store is not None and str(store) != env.get("SELECTED_STORE", ""):
        raise ValueError("the archive store moved between preparing and installing")
    started = time.perf_counter()
    root, hit = install(selection, safe_path(env.get("RUNNER_TEMP", ""), "RUNNER_TEMP"), store)
    duration = time.perf_counter() - started
    executable = root / ("roc.exe" if windows else "roc")
    with open(env["GITHUB_PATH"], "a", encoding="utf-8") as path_file:
        path_file.write(f"{root}\n")
    write_lines(env["GITHUB_ENV"], glue_environment(root))
    write_lines(env["GITHUB_OUTPUT"], {"roc": str(executable), "directory": str(root), "cache-hit": str(hit).lower()})
    source = "the verified cache" if hit else selection["url"]
    print(f"Installed Roc {selection['tag']} from {source}; {selection['size']} bytes, SHA-256 {selection['sha256']} verified; {duration:.1f} seconds")
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write(f"\n### Roc compiler\n\n`{selection['tag']}` for `{selection['platform']}`; SHA-256 `{selection['sha256']}`. "
                          f"Archive reused from the verified cache: `{str(hit).lower()}`.\n")


if __name__ == "__main__":
    try:
        main(prepare=sys.argv[1:] == ["--prepare"])
    except ValueError as error:
        print(f"::error::{error}", file=sys.stderr)
        raise SystemExit(1) from error
