#!/usr/bin/env python3
"""Recheck inert SBOM bindings after transfer and before attestation.

This reviewed controller uses only Python's standard library. It executes no
producer code, Blueprint, Kai, compiler, schema download or document URL. The
existing producer envelope and Blueprint manifests establish final-byte
consistency, not composition completeness or provenance of producer claims.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys

import sbom_sidecars

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fetch-dependency-sboms"))
from fetch_dependency_sboms import document_identity

MAX_JSON_BYTES = 1024 * 1024
MAX_FILE_BYTES = 2 * 1024**3
MAX_TOTAL_BYTES = 8 * 1024**3
MAX_FILES = 2048
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
SHA256 = re.compile(r"[0-9a-f]{64}")
POSITIVE = re.compile(r"[1-9][0-9]*")
EVIDENCE_PATH = "dist/sbom-evidence.json"
FORMATS = {".spdx.json": "SPDX-3.0.1", ".cdx.json": "CycloneDX-1.7", ".spdx-2.3.json": "SPDX-2.3", ".sbom-manifest.json": "manifest"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def record(value, fields, what):
    require(isinstance(value, dict) and set(value) == set(fields.split()), f"unsupported {what} record")
    return value


def json_object(content, what, limit=MAX_JSON_BYTES):
    def pairs(entries):
        result = {}
        for key, value in entries:
            require(key not in result, f"{what} repeats JSON key {key!r}")
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f"{what} contains invalid JSON constant {value}")

    require(len(content) <= limit, f"{what} exceeds {limit // MAX_JSON_BYTES} MiB")
    try:
        value = json.loads(content.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError(f"{what} is not bounded UTF-8 JSON") from error
    require(isinstance(value, dict), f"{what} must be a JSON object")
    pending = [(value, 0)]
    while pending:
        node, depth = pending.pop()
        require(depth <= 64, f"{what} exceeds JSON depth 64")
        if isinstance(node, dict):
            pending.extend((item, depth + 1) for item in node.values())
        elif isinstance(node, list):
            pending.extend((item, depth + 1) for item in node)
    return value


def portable_path(value):
    require(isinstance(value, str) and bool(value), "file path must be a nonempty string")
    path = PurePosixPath(value)
    require(not path.is_absolute() and path.as_posix() == value and all(NAME.fullmatch(part) and part not in (".", "..") for part in path.parts), f"unsafe or aliased file path: {value!r}")
    return path


def explicit_paths(value, what):
    require(isinstance(value, str), f"{what} must be an explicit newline-separated file list")
    paths = value.splitlines()
    require(0 < len(paths) <= MAX_FILES and all(paths), f"{what} must list 1 to {MAX_FILES} files")
    for path in paths:
        portable_path(path)
    require(len(set(paths)) == len(paths), f"{what} repeats a file path")
    require(len({PurePosixPath(path).name for path in paths}) == len(paths), f"{what} repeats a file name")
    return paths


class Files:
    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        self.observed = {}
        self.inodes = set()
        self.total = 0

    def measure(self, name, *, json_limit=None):
        if name in self.observed:
            require(json_limit is None, f"{name} was already read")
            return (*self.observed[name], None)
        relative = portable_path(name)
        path = self.workspace
        for part in relative.parts:
            path = path / part
            require(not path.is_symlink(), f"file path has a symlink: {name}")
        # O_NOFOLLOW and descriptor checks also refuse a substituted final
        # symlink, device, FIFO or hard-link alias. Nothing is extracted.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            before = os.fstat(source.fileno())
            limit = MAX_FILE_BYTES if json_limit is None else json_limit
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, f"{name} must be a regular unaliased file")
            require(before.st_size <= limit, f"{name} exceeds its file size bound")
            identity = (before.st_dev, before.st_ino)
            require(identity not in self.inodes, f"file identity is aliased: {name}")
            self.inodes.add(identity)
            self.total += before.st_size
            require(self.total <= MAX_TOTAL_BYTES and len(self.observed) < MAX_FILES, "transferred file set exceeds its bound")
            digest, pieces, size = hashlib.sha256(), [], 0
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                require(size <= limit, f"{name} exceeds its file size bound")
                digest.update(chunk)
                if json_limit is not None:
                    pieces.append(chunk)
            after = os.fstat(source.fileno())
            require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) == (after.st_size, after.st_mtime_ns, after.st_ctime_ns) and size == before.st_size, f"{name} changed while verifying")
        content = b"".join(pieces) if json_limit is not None else None
        self.observed[name] = (size, digest.hexdigest())
        return (*self.observed[name], content)

    def check(self, entry, *, json_limit=None):
        require(type(entry.get("size")) is int and entry["size"] >= 0 and isinstance(entry.get("sha256"), str) and SHA256.fullmatch(entry["sha256"]), "file record needs an integer size and lowercase SHA-256")
        path = portable_path(entry.get("path"))
        require(entry.get("name") == path.name, "file record name differs from its path")
        size, digest, content = self.measure(str(path), json_limit=json_limit)
        require(size == entry["size"] and digest == entry["sha256"], f"{path} bytes differ from SBOM evidence")
        return json_object(content, str(path), json_limit) if json_limit is not None else None


def check_generator(generator):
    require(isinstance(generator, dict), "unsupported generator record")
    if "kai" in generator:
        # A repository on Kai: the generator is the reviewed sbom plugin of the
        # Kai release its Kaifile names, which kai builds from that bundle.
        record(generator, "tool tool_sha256 generator kai created", "generator")
        require(all(isinstance(value, str) for value in generator.values()), "generator fields must be strings")
        tool = sbom_sidecars.kai_sbom_tool(generator["kai"])
        require(generator["generator"] == tool["generator"], "generator differs from the reviewed Kai sbom plugin")
        require((generator["tool"], generator["tool_sha256"]) == (tool["url"], ""), "generator plugin differs from the reviewed identity")
        require(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", generator["created"]), "generator creation time must be UTC")
        return tool
    record(generator, "tool tool_sha256 compiler generator blueprint created", "generator")
    require(all(isinstance(value, str) for value in generator.values()), "generator fields must be strings")
    tool = sbom_sidecars.sbom_tool(generator["blueprint"])
    require(generator["compiler"] == tool["compiler"] and generator["generator"] == tool["generator"], "generator differs from the reviewed Blueprint tool")
    identities = {(tool["url"], "")}
    identities.update((sbom_sidecars.binary_url(generator["blueprint"], system), digest) for system, digest in tool["binaries"].items())
    require((generator["tool"], generator["tool_sha256"]) in identities, "generator binary or bundle differs from the reviewed identity")
    require(isinstance(generator["created"], str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", generator["created"]), "generator creation time must be UTC")
    return tool


def check_document(document, subject, fmt):
    # Only the Blueprint-emitted standard formats are interpreted here. The
    # reviewed supplier reader extracts identities without executing content.
    if fmt == "SPDX-3.0.1":
        own = [entry for entry in document.get("@graph", []) if isinstance(entry, dict) and entry.get("type") == "software_Package" and isinstance(entry.get("spdxId"), str) and entry["spdxId"].endswith("#Subject")]
        require(len(own) == 1, "SPDX sidecar needs exactly one Blueprint subject")
        require(isinstance(own[0].get("comment"), str), "SPDX subject metadata must be a JSON string")
        json_object(own[0]["comment"].encode("utf-8"), "SPDX subject metadata")
    elif fmt == "SPDX-2.3":
        own = [entry for entry in document.get("packages", []) if isinstance(entry, dict) and entry.get("SPDXID") == "SPDXRef-Subject"]
        require(len(own) == 1, "SPDX sidecar needs exactly one Blueprint subject")
        require(isinstance(own[0].get("comment"), str), "SPDX subject metadata must be a JSON string")
        json_object(own[0]["comment"].encode("utf-8"), "SPDX subject metadata")
    else:
        metadata = document.get("metadata")
        require(isinstance(metadata, dict) and isinstance(metadata.get("component"), dict), "CycloneDX sidecar needs a subject component")
        properties = metadata["component"].get("properties", [])
        require(isinstance(properties, list) and len([entry for entry in properties if isinstance(entry, dict) and entry.get("name") == "blueprint:target"]) == 1, "CycloneDX sidecar needs exactly one subject target")
    identity = document_identity(document)
    require(identity["format"] == fmt, f"sidecar format differs from {fmt}")
    require(all(identity[key] == subject[field] for key, field in (("name", "name"), ("version", "version"), ("target", "target"), ("sha256", "sha256"))), "sidecar subject differs from transferred subject")
    if "@graph" in document:
        identifiers = [entry["spdxId"] for entry in document["@graph"] if isinstance(entry, dict) and "spdxId" in entry]
        require(all(isinstance(identifier, str) for identifier in identifiers) and len(identifiers) == len(set(identifiers)), "sidecar repeats SPDX element identifiers")


def check_manifest(manifest, subject, entries, generator, profile, commit, *, complete=False):
    record(manifest, "profile subject inventory_sha256 context sidecars", "Blueprint manifest")
    require(manifest["profile"] == profile, "manifest profile differs from the reviewed tool")
    listed_subject = record(manifest["subject"], "filename version target sha256 size", "manifest subject")
    require(type(listed_subject["size"]) is int, "manifest subject size must be an integer")
    require(listed_subject == {"filename": subject["name"], **{key: subject[key] for key in ("version", "target", "sha256", "size")}}, "manifest subject differs from transferred subject")
    require(manifest["inventory_sha256"] == subject["inventory_sha256"], "manifest inventory differs from the producer envelope")
    context = record(manifest["context"], "namespace revision created generator", "manifest context")
    require(context["created"] == generator["created"] and context["generator"] == generator["generator"], "manifest context differs from its generator")
    require(isinstance(context["revision"], str) and POSITIVE.fullmatch(context["revision"]), "manifest revision must be positive")
    require(isinstance(context["namespace"], str) and context["namespace"].startswith("https://") and context["namespace"].endswith(f"/{commit}/{subject['name']}/revision-{context['revision']}"), "manifest namespace must identify its revision")
    listed = manifest["sidecars"]
    require(isinstance(listed, list) and 2 <= len(listed) <= 3, "manifest must list the default sidecars")
    seen = set()
    for entry in listed:
        record(entry, "filename format sha256 size", "manifest sidecar")
        name = entry["filename"]
        require(isinstance(name, str) and NAME.fullmatch(name) and name in entries and name not in seen, "manifest lists an unexpected or repeated sidecar")
        seen.add(name)
        expected = entries[name]
        require(type(entry["size"]) is int, "manifest sidecar size must be an integer")
        require(entry == {"filename": name, "format": expected["format"].lower(), "sha256": expected["sha256"], "size": expected["size"]}, f"manifest sidecar differs from the envelope: {name}")
    required = set(entries) if complete else {subject["name"] + ".spdx.json", subject["name"] + ".cdx.json"}
    require(required <= seen, "manifest omits a required sidecar")


def verify(workspace, subjects, assets, expected_source, expected_producer, version, target="source", spdx_2_3=True):
    require(isinstance(version, str) and sbom_sidecars.VERSION.fullmatch(version), "publication version must be SemVer without build metadata")
    subjects = explicit_paths(subjects, "expected subjects")
    assets = set(explicit_paths(assets, "published assets"))
    require(EVIDENCE_PATH not in assets, "the transfer envelope must not be published")
    require(set(subjects) <= assets, "published assets omit an expected subject")
    files = Files(workspace)
    _, _, content = files.measure(EVIDENCE_PATH, json_limit=MAX_JSON_BYTES)
    evidence = record(json_object(content, EVIDENCE_PATH), "format source producer generator subjects dependencies coverage", "producer envelope")
    require(evidence["format"] == "roc-automation-sbom-evidence-v1", "unsupported producer envelope format")
    require(evidence["source"] == expected_source, "SBOM source identity differs from this publication")
    require(evidence["producer"] == expected_producer, "SBOM producing run or attempt differs from the build job")
    require(isinstance(expected_producer.get("run_attempt"), str) and POSITIVE.fullmatch(expected_producer["run_attempt"]), "producing attempt must be a positive integer")
    tool = check_generator(evidence["generator"])
    require(isinstance(evidence["subjects"], list) and len(evidence["subjects"]) == len(subjects), "envelope omits or adds an expected subject")
    require(isinstance(evidence["dependencies"], list) and isinstance(evidence["coverage"], list), "envelope dependency and coverage records must be lists")
    expected = set(subjects)
    bound_assets = set()
    complete_manifest = "complete-manifest" in tool["features"]
    suffixes = list(sbom_sidecars.sidecar_suffixes(spdx_2_3 or complete_manifest))
    if "manifest" in tool["features"]:
        suffixes.append(".sbom-manifest.json")
    for subject in evidence["subjects"]:
        record(subject, "name path size sha256 target version url inventory_sha256 sidecars", "envelope subject")
        path = str(portable_path(subject["path"]))
        require(path in expected, "envelope lists an unexpected or repeated subject")
        expected.remove(path)
        require(subject["target"] == target and subject["version"] == version, "subject target or version differs from this publication")
        require(subject["url"] == f"{expected_source['server']}/{expected_source['repository']}/releases/download/{version}/{subject['name']}", "subject URL differs from this publication")
        require(isinstance(subject["inventory_sha256"], str) and SHA256.fullmatch(subject["inventory_sha256"]), "subject needs its inventory SHA-256")
        files.check(subject)
        require(isinstance(subject["sidecars"], list) and len(subject["sidecars"]) == len(suffixes), "envelope omits or adds a required sidecar format")
        expected_sidecars = {path + suffix: FORMATS[suffix] for suffix in suffixes}
        entries, manifest = {}, None
        for sidecar in subject["sidecars"]:
            record(sidecar, "name path size sha256 format", "envelope sidecar")
            sidecar_path = str(portable_path(sidecar["path"]))
            require(sidecar_path in expected_sidecars and sidecar["format"] == expected_sidecars.pop(sidecar_path), "envelope lists an unexpected, repeated or mislabelled sidecar")
            limit = MAX_JSON_BYTES if sidecar["format"] == "manifest" else tool["payload_bytes"]
            document = files.check(sidecar, json_limit=limit)
            bound_assets.add(sidecar_path)
            if sidecar["format"] == "manifest":
                manifest = document
            else:
                entries[sidecar["name"]] = sidecar
                check_document(document, subject, sidecar["format"])
        if manifest is not None:
            check_manifest(manifest, subject, entries, evidence["generator"], tool["profile"], expected_source["commit"], complete=complete_manifest)
        bound_assets.add(path)
    require(not expected and bound_assets <= assets, "published assets omit a bound subject or sidecar")
    require({path for path in assets if path.endswith(tuple(FORMATS))} == bound_assets - set(subjects), "published assets contain an unbound sidecar")
    # Also admit only bounded regular files for the other explicitly published
    # assets. Their composition is outside this bundle-sidecar contract.
    for path in assets - bound_assets:
        files.measure(path)
    return evidence


def main(env=None):
    env = os.environ if env is None else env
    evidence = verify(Path.cwd(), env.get("INPUT_SUBJECTS", ""), env.get("INPUT_ASSETS", ""),
        {"server": env["GITHUB_SERVER_URL"], "repository": env["GITHUB_REPOSITORY"], "commit": env["GITHUB_SHA"]},
        {"workflow": env["GITHUB_WORKFLOW_REF"], "run_id": env["GITHUB_RUN_ID"], "run_attempt": env["INPUT_PRODUCING_ATTEMPT"]},
        env["INPUT_VERSION"])
    print(f"Verified transferred SBOM bindings for {len(evidence['subjects'])} subjects before attestation")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f"::error::{error}", file=sys.stderr)
        raise SystemExit(1) from error
