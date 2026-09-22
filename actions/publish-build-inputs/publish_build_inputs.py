#!/usr/bin/env python3
"""Publish an attested PR build and commit its content lock without executing it."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import time
import zipfile

IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
HEX256 = re.compile(r"[0-9a-f]{64}")
HEX160 = re.compile(r"[0-9a-f]{40}")
MAX_CANDIDATE_BYTES = 2 * 1024 ** 3
MAX_FILES = 16


def run(args, *, data=None, stdout=None):
    return subprocess.run(args, input=data, text=stdout is None, stdout=stdout,
                          capture_output=stdout is None, check=True).stdout


def api(endpoint, data=None, method=None):
    args = ["gh", "api", endpoint, "-H", "X-GitHub-Api-Version: 2026-03-10"]
    if method:
        args += ["--method", method]
    if data is not None:
        args += ["--input", "-"]
    try:
        output = run(args, data=json.dumps(data) if data is not None else None)
    except subprocess.CalledProcessError as error:
        try:
            message = json.loads(error.stdout)["message"]
        except (ValueError, TypeError, KeyError):
            raise error
        raise ValueError(f"GitHub API rejected {endpoint}: {message}") from None
    return json.loads(output) if output else None


def output(name, value):
    value = str(value)
    if any(char in name + value for char in "\r\n"):
        raise ValueError("multiline workflow output")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        stream.write(f"{name}={value}\n")


def repository():
    value = os.environ["GITHUB_REPOSITORY"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise ValueError("invalid repository")
    return value


def require_default_dispatch():
    default = os.environ["DEFAULT_BRANCH"]
    if (os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or os.environ.get("GITHUB_REF") != f"refs/heads/{default}"):
        raise ValueError("build-input publication requires an explicit default-branch dispatch")
    current = api(f"repos/{repository()}/git/ref/heads/{default}")["object"]["sha"]
    if current != os.environ["GITHUB_SHA"]:
        raise ValueError("default branch moved; retry publication from its current commit")


def checked_name(value, description, pattern=IDENTIFIER):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"invalid {description}")
    return value


def checked_workflow(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.ya?ml", value):
        raise ValueError("invalid producer workflow")
    return value


def checked_lock_path(value):
    path = PurePosixPath(value)
    if (path.is_absolute() or ".." in path.parts or len(path.parts) > 4
            or not value.endswith(".lock.json") or str(path) != value):
        raise ValueError("invalid lock path")
    return value


def pr_source(number):
    if not re.fullmatch(r"[1-9][0-9]*", number):
        raise ValueError("invalid pull request number")
    item = api(f"repos/{repository()}/pulls/{number}")
    if (item["state"] != "open" or item["head"]["repo"]["full_name"] != repository()
            or item["base"]["ref"] != os.environ["DEFAULT_BRANCH"]
            or not HEX160.fullmatch(item["head"]["sha"])):
        raise ValueError("publisher requires an open same-repository pull request to the default branch")
    return item["head"]["ref"], item["head"]["sha"]


def dispatch_and_wait(workflow, branch, sha):
    dispatched = api(f"repos/{repository()}/actions/workflows/{workflow}/dispatches", {
        "ref": branch, "inputs": {"release_candidate": True, "expected_sha": sha},
    })
    run_id = dispatched["workflow_run_id"]
    deadline = time.monotonic() + 175 * 60
    while True:
        item = api(f"repos/{repository()}/actions/runs/{run_id}")
        if (item["event"] != "workflow_dispatch" or item["head_sha"] != sha
                or item["head_branch"] != branch or item["path"] != f".github/workflows/{workflow}"):
            raise ValueError("candidate run does not belong to the selected producer and PR head")
        if item["status"] == "completed":
            if item["conclusion"] != "success":
                raise ValueError("candidate producer did not succeed")
            return run_id, item["html_url"]
        if time.monotonic() >= deadline:
            raise TimeoutError("timed out waiting for the build-input producer")
        time.sleep(30)


def download_candidate(run_id, artifact_name, destination):
    listing = api(f"repos/{repository()}/actions/runs/{run_id}/artifacts?per_page=100")
    matches = [item for item in listing["artifacts"] if item["name"] == artifact_name and not item["expired"]]
    if len(matches) != 1:
        raise ValueError("candidate run must expose exactly one unexpired publication artifact")
    archive = destination / "candidate.zip"
    with archive.open("wb") as stream:
        run(["gh", "api", f"repos/{repository()}/actions/artifacts/{matches[0]['id']}/zip"], stdout=stream)
    if archive.stat().st_size > MAX_CANDIDATE_BYTES:
        raise ValueError("candidate artifact is oversized")
    extracted = destination / "candidate"
    extracted.mkdir()
    with zipfile.ZipFile(archive) as packed:
        records = packed.infolist()
        if not records or len(records) > MAX_FILES:
            raise ValueError("candidate artifact has an invalid file count")
        total = 0
        for record in records:
            path = PurePosixPath(record.filename)
            mode = record.external_attr >> 16
            if (record.is_dir() or path.is_absolute() or len(path.parts) != 1 or ".." in path.parts
                    or str(path) != record.filename or (mode and mode & 0o170000 not in (0, 0o100000))):
                raise ValueError("candidate artifact contains an unsafe entry")
            total += record.file_size
            if total > MAX_CANDIDATE_BYTES:
                raise ValueError("candidate artifact expands beyond its limit")
            target = extracted / record.filename
            with packed.open(record) as source, target.open("xb") as output_stream:
                while chunk := source.read(1024 ** 2):
                    output_stream.write(chunk)
    return extracted


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_candidate(directory, workflow, branch, source_sha):
    manifest_path = directory / "build-input-release.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if set(manifest) != {"schema_version", "kind", "source", "assets"} or manifest["schema_version"] != 1:
        raise ValueError("unsupported build-input release manifest")
    kind = checked_name(manifest["kind"], "release kind")
    source = manifest["source"]
    expected_source = {
        "repository": repository(), "sha": source_sha, "ref": f"refs/heads/{branch}",
        "workflow": repository() + "/.github/workflows/" + workflow,
    }
    if (not isinstance(source, dict) or set(source) != set(expected_source) | {"input_fingerprint"}
            or any(source.get(key) != value for key, value in expected_source.items())
            or not HEX256.fullmatch(source.get("input_fingerprint", ""))):
        raise ValueError("candidate source identity differs from the dispatched producer")
    assets = manifest["assets"]
    if not isinstance(assets, dict) or not assets:
        raise ValueError("candidate manifest has no target assets")
    expected_files = {manifest_path.name}
    for target, record in assets.items():
        checked_name(target, "target")
        if (not isinstance(record, dict) or set(record) != {"asset", "sha256", "size"}
                or not IDENTIFIER.fullmatch(record.get("asset", ""))
                or not record["asset"].endswith(".tar")
                or not HEX256.fullmatch(record.get("sha256", ""))
                or type(record.get("size")) is not int or not 0 < record["size"] <= MAX_CANDIDATE_BYTES):
            raise ValueError("invalid target asset record")
        path = directory / record["asset"]
        if not path.is_file() or path.is_symlink() or path.stat().st_size != record["size"] or sha256(path) != record["sha256"]:
            raise ValueError("target asset differs from its manifest")
        expected_files.add(record["asset"])
    if {path.name for path in directory.iterdir()} != expected_files:
        raise ValueError("candidate artifact contains undeclared files")
    return manifest, manifest_bytes, kind


def verify_attestations(directory, manifest):
    source = manifest["source"]
    for name in ["build-input-release.json", *(item["asset"] for item in manifest["assets"].values())]:
        subprocess.run([
            "gh", "attestation", "verify", str(directory / name), "--repo", repository(),
            "--signer-workflow", source["workflow"], "--source-digest", source["sha"],
            "--source-ref", source["ref"],
        ], check=True)


def release_by_tag(tag):
    releases = api(f"repos/{repository()}/releases?per_page=100")
    matches = [item for item in releases if item["tag_name"] == tag]
    if len(matches) > 1:
        raise ValueError("release identity is ambiguous")
    return matches[0] if matches else None


def make_lock(manifest, tag, manifest_digest, lock_path):
    return json.dumps({
        "schema_version": 1,
        "kind": manifest["kind"],
        "repository": repository(),
        "release": tag,
        "manifest": {"asset": "build-input-release.json", "sha256": manifest_digest},
        "source": manifest["source"],
        "targets": manifest["assets"],
    }, indent=2).encode() + b"\n"


def publish(directory, manifest, manifest_digest, tag, lock_path, lock_bytes, run_url):
    lock_asset = directory / Path(lock_path).name
    lock_asset.write_bytes(lock_bytes)
    assets = [directory / "build-input-release.json", lock_asset]
    assets += [directory / record["asset"] for record in manifest["assets"].values()]
    expected = {path.name for path in assets}
    release = release_by_tag(tag)
    if release is None:
        notes = directory / "release-notes.md"
        notes.write_text(
            f"Content-addressed `{manifest['kind']}` inputs built and tested by [{manifest['source']['workflow']}]({run_url}) "
            f"from `{manifest['source']['sha']}`. Consumers must verify the committed SHA-256 values on every use.\n"
        )
        subprocess.run(["gh", "release", "create", tag, *map(str, assets), "--repo", repository(),
                        "--target", manifest["source"]["sha"], "--latest=false", "--draft",
                        "--title", f"{manifest['kind']} {manifest_digest}", "--notes-file", str(notes)], check=True)
        release = release_by_tag(tag)
    observed = {asset["name"] for asset in release["assets"]}
    if release["target_commitish"] != manifest["source"]["sha"] or observed != expected:
        raise ValueError("existing release differs from the exact candidate")
    for asset in release["assets"]:
        local = next(path for path in assets if path.name == asset["name"])
        if asset["size"] != local.stat().st_size:
            raise ValueError("published release asset size differs from the candidate")
    if release["draft"]:
        subprocess.run(["gh", "release", "edit", tag, "--repo", repository(), "--draft=false"], check=True)


def signed_lock_commit(number, branch, expected_head, lock_path, lock_bytes, tag):
    request = {"query": """mutation($input: CreateCommitOnBranchInput!) {
      createCommitOnBranch(input: $input) { commit { oid } }
    }""", "variables": {"input": {
        "branch": {"repositoryNameWithOwner": repository(), "branchName": branch},
        "expectedHeadOid": expected_head,
        "message": {"headline": f"Adopt linker inputs {tag}"},
        "fileChanges": {"additions": [{"path": lock_path,
            "contents": base64.b64encode(lock_bytes).decode()}]},
    }}}
    sha = api("graphql", request)["data"]["createCommitOnBranch"]["commit"]["oid"]
    commit = api(f"repos/{repository()}/commits/{sha}")
    if (not commit["commit"]["verification"]["verified"] or len(commit["files"]) != 1
            or commit["files"][0]["filename"] != lock_path):
        raise ValueError("lock adoption was not a verified lock-only commit")
    current = api(f"repos/{repository()}/pulls/{number}")
    if current["head"]["sha"] != sha:
        raise ValueError("pull request did not advance to the signed lock commit")
    return sha


def main():
    require_default_dispatch()
    number = os.environ["INPUT_PULL_REQUEST"]
    workflow = checked_workflow(os.environ["INPUT_PRODUCER_WORKFLOW"])
    artifact = checked_name(os.environ["INPUT_CANDIDATE_ARTIFACT"], "candidate artifact")
    lock_path = checked_lock_path(os.environ["INPUT_LOCK_PATH"])
    prefix = checked_name(os.environ["INPUT_RELEASE_PREFIX"], "release prefix")
    branch, source_sha = pr_source(number)
    run_id, run_url = dispatch_and_wait(workflow, branch, source_sha)
    with tempfile.TemporaryDirectory(prefix="roc-build-input-publication-") as temporary:
        directory = download_candidate(run_id, artifact, Path(temporary))
        manifest, manifest_bytes, kind = validate_candidate(directory, workflow, branch, source_sha)
        verify_attestations(directory, manifest)
        manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
        tag = f"{prefix}-sha256-{manifest_digest}"
        lock_bytes = make_lock(manifest, tag, manifest_digest, lock_path)
        publish(directory, manifest, manifest_digest, tag, lock_path, lock_bytes, run_url)
        commit = signed_lock_commit(number, branch, source_sha, lock_path, lock_bytes, tag)
    output("release", tag)
    output("manifest_sha256", manifest_digest)
    output("lock_commit", commit)
    print(f"Published {kind} {tag} and committed {lock_path} at {commit}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError, TimeoutError,
            zipfile.BadZipFile, json.JSONDecodeError) as error:
        print(f"Build-input publisher failed: {error}", file=os.sys.stderr)
        raise SystemExit(1)
