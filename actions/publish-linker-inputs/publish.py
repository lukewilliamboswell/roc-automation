#!/usr/bin/env python3
"""Validate and immutably publish inert linker-input release assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile


TAG = re.compile(r"linker-inputs-v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SHA = re.compile(r"[0-9a-f]{40}$")
DIGEST = re.compile(r"[0-9a-f]{64}$")


def fail(message: str) -> None:
    raise ValueError(message)


def object_keys(value: object, required: set[str], optional: set[str], where: str) -> dict:
    if not isinstance(value, dict):
        fail(f"{where} must be an object")
    missing = required - value.keys()
    unknown = value.keys() - required - optional
    if missing or unknown:
        fail(f"{where} keys invalid (missing={sorted(missing)}, unknown={sorted(unknown)})")
    return value


def safe_name(value: object, where: str) -> str:
    if not isinstance(value, str) or not value or PurePosixPath(value).name != value:
        fail(f"{where} must be a non-empty top-level file name")
    if value in {".", ".."} or "\\" in value:
        fail(f"{where} is unsafe")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            fail(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def validate_candidate(root: Path, manifest_name: str, tag: str, repository: str, commit: str) -> list[Path]:
    if not TAG.fullmatch(tag):
        fail("release tag must be linker-inputs-vMAJOR.MINOR.PATCH")
    if not SHA.fullmatch(commit):
        fail("source commit must be a lowercase 40-character SHA")
    manifest_name = safe_name(manifest_name, "manifest path")
    if not root.is_dir() or root.is_symlink():
        fail("candidate root must be a real directory")

    entries = list(root.iterdir())
    for entry in entries:
        if entry.is_symlink() or not entry.is_file():
            fail(f"candidate contains non-regular entry: {entry.name}")
    manifest_path = root / manifest_name
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        fail(f"cannot read manifest: {error}")
    manifest = object_keys(
        manifest,
        {"schema_version", "release_tag", "source", "assets"},
        {"input_fingerprint", "targets", "dependencies", "license_summary"},
        "manifest",
    )
    if manifest["schema_version"] != 1 or manifest["release_tag"] != tag:
        fail("manifest schema_version or release_tag does not match")
    source = object_keys(manifest["source"], {"repository", "commit"}, {"ref"}, "source")
    if source["repository"] != repository or source["commit"] != commit:
        fail("manifest source must match the caller repository and event commit")
    if "ref" in source and source["ref"] != f"refs/heads/{os.environ.get('DEFAULT_BRANCH', '')}":
        fail("manifest source ref must be the default branch")
    if not isinstance(manifest["assets"], list) or not manifest["assets"]:
        fail("manifest assets must be a non-empty array")

    declared: list[Path] = []
    seen: set[str] = set()
    for index, raw in enumerate(manifest["assets"]):
        asset = object_keys(raw, {"name", "sha256", "size"}, {"media_type", "role"}, f"assets[{index}]")
        name = safe_name(asset["name"], f"assets[{index}].name")
        if name == manifest_name or name in seen:
            fail(f"duplicate or reserved asset name: {name}")
        seen.add(name)
        if not isinstance(asset["sha256"], str) or not DIGEST.fullmatch(asset["sha256"]):
            fail(f"invalid sha256 for {name}")
        if not isinstance(asset["size"], int) or isinstance(asset["size"], bool) or asset["size"] < 0:
            fail(f"invalid size for {name}")
        path = root / name
        if not path.is_file() or path.is_symlink():
            fail(f"declared asset is not a regular file: {name}")
        if path.stat().st_size != asset["size"] or sha256(path) != asset["sha256"]:
            fail(f"declared identity does not match: {name}")
        declared.append(path)
    actual = {entry.name for entry in entries}
    expected = seen | {manifest_name}
    if actual != expected:
        fail(f"candidate file set differs from manifest (extra={sorted(actual-expected)}, missing={sorted(expected-actual)})")
    return [manifest_path, *declared]


def gh_json(*args: str) -> object:
    return json.loads(subprocess.check_output(["gh", *args], text=True))


def enforce_event(repository: str, commit: str) -> str:
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        fail("publication requires an explicit workflow_dispatch")
    if os.environ.get("GITHUB_REPOSITORY") != repository or os.environ.get("GITHUB_SHA") != commit:
        fail("caller identity does not match the workflow event")
    info = gh_json("api", f"repos/{repository}")
    default = info.get("default_branch") if isinstance(info, dict) else None
    if not isinstance(default, str) or not default:
        fail("invalid repository response")
    if os.environ.get("GITHUB_REF") != f"refs/heads/{default}":
        fail("publication must run from the caller's default branch")
    return default


def ensure_absent(repository: str, tag: str) -> None:
    for endpoint in (f"repos/{repository}/releases/tags/{tag}", f"repos/{repository}/git/ref/tags/{tag}"):
        result = subprocess.run(["gh", "api", "--include", endpoint], text=True, capture_output=True)
        response = result.stdout.lstrip()
        match = re.match(r"HTTP/\S+ ([0-9]{3})\b", response)
        if result.returncode == 0 and match and match.group(1).startswith("2"):
            fail(f"release or tag already exists: {tag}")
        if not match or match.group(1) != "404":
            fail(f"could not prove release/tag absence: {endpoint}")


def compare_download(repository: str, tag: str, files: list[Path]) -> None:
    with tempfile.TemporaryDirectory() as directory:
        subprocess.run(["gh", "release", "download", tag, "--repo", repository, "--dir", directory], check=True)
        downloaded = Path(directory)
        if {p.name for p in downloaded.iterdir()} != {p.name for p in files}:
            fail("downloaded release asset set differs from validated candidate")
        for source in files:
            target = downloaded / source.name
            if target.stat().st_size != source.stat().st_size or sha256(target) != sha256(source):
                fail(f"downloaded release asset differs: {source.name}")


def publish(repository: str, tag: str, name: str, files: list[Path]) -> None:
    ensure_absent(repository, tag)
    subprocess.run([
        "gh", "release", "create", tag, "--repo", repository, "--target", os.environ["GITHUB_SHA"],
        "--title", name or tag, "--notes", "Signed, content-addressed linker inputs.", "--draft",
    ], check=True)
    subprocess.run(["gh", "release", "upload", tag, "--repo", repository, *map(str, files)], check=True)
    compare_download(repository, tag, files)
    subprocess.run(["gh", "release", "edit", tag, "--repo", repository, "--draft=false", "--latest=false"], check=True)
    release = gh_json("api", f"repos/{repository}/releases/tags/{tag}")
    if not isinstance(release, dict) or release.get("draft") or not release.get("immutable"):
        fail("published release is not immutable; enable repository release immutability")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("validate", "publish"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", default="dependency.json")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--release-name", default="")
    parser.add_argument("--github-output")
    args = parser.parse_args()
    os.environ.setdefault("DEFAULT_BRANCH", "")
    files = validate_candidate(args.root, args.manifest, args.tag, args.repository, args.commit)
    if args.command == "validate":
        if args.github_output:
            Path(args.github_output).write_text("\n".join(f"asset-{i}={path}" for i, path in enumerate(files)) + f"\nasset-count={len(files)}\n")
        return
    os.environ["DEFAULT_BRANCH"] = enforce_event(args.repository, args.commit)
    # Validate again with the authoritative default branch available.
    files = validate_candidate(args.root, args.manifest, args.tag, args.repository, args.commit)
    publish(args.repository, args.tag, args.release_name, files)


if __name__ == "__main__":
    main()
