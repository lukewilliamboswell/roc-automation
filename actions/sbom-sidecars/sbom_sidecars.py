#!/usr/bin/env python3
"""Generate and check SBOM sidecars for an explicit list of release files.

The action runs in a read-only build job. It drives the standalone Blueprint
SBOM tool that the repository's Blueprint release names, through the Roc
compiler that release records, and never holds a write token. Every decision
about composition is Blueprint's `inspect`; this controller stages inputs,
names subjects and documents, refuses stale or aliased sidecars, and reports
the complete asset set for the test jobs and the publisher.

A repository on Kai (Kaifile.roc and no Blueprint.roc) gets documents in the
profile Blueprint's SBOM tool writes from `kai sbom`, Kai's port of that
tool: `inspect`, `bind` and `check` read the Kaifile and its lock themselves.
Kai reads no release file, so this controller lists and hashes a source
bundle's members, states each file's SHA-256 and size in its subject, and
compares the file with the SHA-256 the manifest states.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "setup-blueprint"))
import setup_blueprint  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "setup-kai"))
import setup_kai  # noqa: E402

SBOM_TOOLS = Path(__file__).resolve().parents[1] / "setup-blueprint" / "sbom-tools.json"
SIDECAR_SUFFIXES = (".spdx.json", ".cdx.json", ".spdx-2.3.json")
MANIFEST_SUFFIX = ".sbom-manifest.json"
TOOL_FEATURES = ("members", "manifest", "complete-manifest")
KAI_TOOL_FEATURES = ("members", "manifest")
DOCUMENT_PROFILES = ("blueprint-spdx-3.0-v1", "blueprint-spdx-3.0-v2")
SOURCE_INVOCATIONS = ("roc-run-separator-v1", "roc-run-direct-v1")
MEMBER_ID = re.compile(r"[^A-Za-z0-9.-]+")
MAX_JSON_BYTES = 1024 * 1024
MAX_FILES = 256
SHA256 = re.compile(r"[0-9a-f]{64}")
SYSTEM = re.compile(r"[a-z0-9_]+-[a-z0-9]+")
# Build metadata is deliberately unsupported in release URLs. Numeric prerelease
# identifiers have no leading zeros; alphanumeric identifiers may contain hyphens.
PRERELEASE_IDENTIFIER = r"(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-" + PRERELEASE_IDENTIFIER + r"(?:\." + PRERELEASE_IDENTIFIER + r")*)?")
AGENT = re.compile(r"(Organization|Person): \S.*")
COMMIT = re.compile(r"[0-9a-f]{40}")
IMPORT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]*")
# What blueprint-sbom's `members` accepts in a source bundle, which a Kai
# release lists here instead: regular files with portable relative names.
MEMBER_NAME = re.compile(r"[A-Za-z0-9+._-]+(?:/[A-Za-z0-9+._-]+)*")
MAX_MEMBERS = 4096
MAX_MEMBER_BYTES = 1024**3

Run = Callable[[list[str], Path], subprocess.CompletedProcess]


class Refusal(ValueError):
    """A reason this step cannot produce evidence; nothing is published."""


def run_command(command: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True)


def succeed(run: Run, command: list[str], cwd: Path, what: str) -> str:
    result = run(command, cwd)
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    if result.returncode:
        raise Refusal(f"{what} failed with status {result.returncode}: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def clean(value: str) -> bool:
    return bool(value) and not any(ord(c) < 32 or ord(c) == 127 for c in value)


def read_bounded_json(path: Path, what: str, limit: int = MAX_JSON_BYTES) -> dict:
    """Read a bounded regular JSON object, refusing duplicate keys."""
    if path.is_symlink() or not path.is_file():
        raise Refusal(f"{what} must be a regular file: {path}")
    with path.open("rb") as source:
        content = source.read(limit + 1)
    if len(content) > limit:
        raise Refusal(f"{what} exceeds the {limit // MAX_JSON_BYTES} MiB bound Blueprint accepts: {path}")

    def no_duplicates(pairs):
        record = {}
        for key, value in pairs:
            if key in record:
                raise Refusal(f"{what} repeats the key {key!r}: {path}")
            record[key] = value
        return record

    try:
        document = json.loads(content.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Refusal(f"{what} is not valid UTF-8 JSON: {path}: {error}") from error
    if not isinstance(document, dict):
        raise Refusal(f"{what} must be a JSON object: {path}")
    return document


def sbom_tool(version: str, path: Path = SBOM_TOOLS) -> dict:
    """The reviewed standalone SBOM tool of a Blueprint release.

    Every release records the Roc app bundle and the compiler it needs. A
    release that also ships `blueprint-sbom-<system>` binaries records their
    reviewed SHA-256 per system; the action then runs the binary and needs
    no compiler. Source invocation is a bounded compiler/app protocol; omitted
    fields preserve the legacy separator prefix.
    """
    tools = json.loads(path.read_text(encoding="utf-8"))
    entry = tools.get(version)
    if not isinstance(entry, dict):
        raise Refusal(
            f"this revision of roc-automation records no SBOM tool for roc-blueprint {version}; "
            "use a revision whose actions/setup-blueprint/sbom-tools.json records it"
        )
    url, compiler, generator = entry.get("url", ""), entry.get("compiler", ""), entry.get("generator", "")
    if not re.fullmatch(r"https://github\.com/lukewilliamboswell/roc-blueprint/releases/download/" + re.escape(version) + r"/roc-blueprint-sbom-[0-9A-Za-z]+\.tar\.zst", url):
        raise Refusal(f"the recorded SBOM tool URL for roc-blueprint {version} is not an immutable roc-blueprint release bundle")
    if not re.fullmatch(r"nightly-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9a-f]{7,40}|[0-9]+\.[0-9]+\.[0-9]+", compiler):
        raise Refusal(f"the recorded compiler for roc-blueprint {version} is not a Roc release tag")
    if not generator.startswith("Tool: ") or not clean(generator):
        raise Refusal(f"the recorded generator name for roc-blueprint {version} is malformed")
    binaries = entry.get("binaries", {})
    if not isinstance(binaries, dict) or any(system not in setup_blueprint.SYSTEMS.values() or not isinstance(digest, str) or not SHA256.fullmatch(digest) for system, digest in binaries.items()):
        raise Refusal(f"the recorded SBOM tool binaries for roc-blueprint {version} must map released systems to 64 lowercase hexadecimal SHA-256 digests")
    features = entry.get("features", [])
    if not isinstance(features, list) or any(feature not in TOOL_FEATURES for feature in features) or len(set(features)) != len(features):
        raise Refusal(f"the recorded SBOM tool features for roc-blueprint {version} must be a subset of {', '.join(TOOL_FEATURES)}")
    profile = entry.get("profile", DOCUMENT_PROFILES[0])
    if profile not in DOCUMENT_PROFILES:
        raise Refusal(f"the recorded SBOM document profile for roc-blueprint {version} is unsupported")
    source_invocation = entry.get("source_invocation", SOURCE_INVOCATIONS[0])
    if not isinstance(source_invocation, str) or source_invocation not in SOURCE_INVOCATIONS:
        raise Refusal(f"the recorded SBOM source invocation for roc-blueprint {version} must be one of {', '.join(SOURCE_INVOCATIONS)}")
    payload_mib = entry.get("payload_mib", 1)
    if type(payload_mib) is not int or payload_mib not in (1, 16):
        raise Refusal(f"the recorded SBOM payload bound for roc-blueprint {version} must be 1 or 16 MiB")
    if "complete-manifest" in features and "manifest" not in features:
        raise Refusal(f"the complete-manifest feature for roc-blueprint {version} requires manifest")
    return {"url": url, "compiler": compiler, "generator": generator, "binaries": dict(binaries), "features": list(features), "profile": profile, "source_invocation": source_invocation, "payload_bytes": payload_mib * MAX_JSON_BYTES}


def source_tool_command(roc: str, tool: dict) -> list[str]:
    """Use the reviewed compiler/app interface, never retry another prefix."""
    command = [roc, "run", tool["url"]]
    if tool["source_invocation"] == "roc-run-separator-v1":
        command.append("--")
    return command


def kai_sbom_tool(version: str, path: Path = SBOM_TOOLS) -> dict:
    """The reviewed sbom plugin of a Kai release, from the file's `kai` record.

    Each Kai release publishes its sbom plugin as one content-addressed
    bundle, which the repository's Kaifile names and kai builds; the record
    is the URL a release's notes give it, and the generator its documents
    name.
    """
    tools = json.loads(path.read_text(encoding="utf-8")).get("kai", {})
    entry = tools.get(version) if isinstance(tools, dict) else None
    if not isinstance(entry, dict):
        raise Refusal(
            f"this revision of roc-automation records no sbom plugin for Kai {version}; "
            "use a revision whose actions/setup-blueprint/sbom-tools.json records it under kai"
        )
    url, generator = entry.get("plugin", ""), entry.get("generator", "")
    if not re.fullmatch(re.escape(f"{setup_kai.RELEASES}/v{version}/sbom-") + setup_kai.BASE58 + r"\.tar\.zst", url):
        raise Refusal(f"the recorded sbom plugin URL for Kai {version} is not an immutable Kai release bundle")
    if generator != f"Tool: kai-sbom-{version}":
        raise Refusal(f"the recorded generator name for Kai {version} must be Tool: kai-sbom-{version}")
    # The sbom plugin lists members in this action, not with a `members`
    # command, and writes manifests that list their SPDX 2.3 renderings.
    features = entry.get("features", [])
    if not isinstance(features, list) or sorted(features) != sorted(KAI_TOOL_FEATURES):
        raise Refusal(f"the recorded sbom plugin features for Kai {version} must be {', '.join(KAI_TOOL_FEATURES)}")
    profile = entry.get("profile", "")
    if profile not in DOCUMENT_PROFILES:
        raise Refusal(f"the recorded SBOM document profile for Kai {version} is unsupported")
    return {"url": url, "compiler": "", "generator": generator, "binaries": {}, "features": list(features), "profile": profile, "payload_bytes": MAX_JSON_BYTES}


def archive_members(archive: Path) -> dict:
    """List a .tar.zst source bundle as blueprint-sbom's `members` does.

    The listing is the archive's file name and SHA-256 and each member's name,
    size and SHA-256 in archive order. Only regular files with portable,
    unique relative names are accepted; nothing is extracted to disk.
    """
    members, seen, total = [], set(), 0
    decompress = subprocess.Popen(["zstd", "-dcq", "--", str(archive)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=decompress.stdout, mode="r|") as listing:
            for member in listing:
                name = member.name
                if member.pax_headers or not member.isreg():
                    raise Refusal(f"{archive.name}: member is not a plain regular file: {name!r}")
                if not MEMBER_NAME.fullmatch(name) or name.startswith("-") or any(part in (".", "..") for part in name.split("/")):
                    raise Refusal(f"{archive.name}: unsafe member name: {name!r}")
                if name in seen:
                    raise Refusal(f"{archive.name}: duplicate member: {name!r}")
                seen.add(name)
                if len(members) == MAX_MEMBERS:
                    raise Refusal(f"{archive.name}: more than {MAX_MEMBERS} members")
                total += member.size
                if total > MAX_MEMBER_BYTES:
                    raise Refusal(f"{archive.name}: members expand to more than {MAX_MEMBER_BYTES} bytes")
                digest, size = hashlib.sha256(), 0
                source = listing.extractfile(member)
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
                members.append({"name": name, "size": size, "sha256": digest.hexdigest()})
    except tarfile.TarError as error:
        raise Refusal(f"{archive.name} is not a readable tar archive: {error}") from error
    finally:
        decompress.stdout.close()
        status, errors = decompress.wait(), decompress.stderr.read().decode("utf-8", "replace").strip()
        decompress.stderr.close()
    if status:
        raise Refusal(f"zstd could not decompress {archive.name}: {errors or f'status {status}'}")
    return {"archive": archive.name, "sha256": sha256_of(archive), "members": members}


def binary_url(version: str, system: str) -> str:
    return f"{setup_blueprint.RELEASES}/{setup_blueprint.checked_version(version)}/blueprint-sbom-{system}{'.exe' if setup_blueprint.windows(system) else ''}"


def install_binary(version: str, system: str, expected: str, directory: Path, download: setup_blueprint.Fetch = setup_blueprint.fetch) -> Path:
    """Admit the released tool binary by its reviewed SHA-256 before it can run, as setup-blueprint admits blueprint."""
    url = binary_url(version, system)
    setup_blueprint.private_directory(directory)
    handle, name = tempfile.mkstemp(prefix="blueprint-sbom-", suffix=".download", dir=directory)
    os.close(handle)
    candidate = Path(name)
    try:
        download(url, candidate)
        actual = sha256_of(candidate)
        if not hmac.compare_digest(actual, expected):
            raise Refusal(f"SHA-256 mismatch for {url}: expected {expected}, got {actual}")
        candidate.chmod(0o755)
        installed = directory / f"blueprint-sbom-{system}{'.exe' if setup_blueprint.windows(system) else ''}"
        candidate.replace(installed)
        return installed
    finally:
        candidate.unlink(missing_ok=True)


def compiler_matches(output: str, tag: str) -> bool:
    """`roc version` names the compiler tag as a whole word on its first line."""
    first = output.strip().splitlines()[0] if output.strip() else ""
    return re.search(rf"(?<![0-9A-Za-z._-]){re.escape(tag)}(?![0-9A-Za-z._-])", first) is not None


def release_files(listing: str, workspace: Path) -> list[Path]:
    """An explicit, bounded list of regular release files; never a glob."""
    names = [line.strip() for line in listing.splitlines() if line.strip()]
    if not names:
        raise Refusal("files must list at least one release file, one per line")
    if len(names) > MAX_FILES:
        raise Refusal(f"files lists more than {MAX_FILES} release files")
    files: list[Path] = []
    seen_names: set[str] = set()
    root = workspace.resolve()
    for name in names:
        if not clean(name) or any(c in name for c in "*?[") or name.startswith("-"):
            raise Refusal(f"files must name each release file explicitly, without glob characters: {name!r}")
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise Refusal(f"files must be relative workspace paths without '..': {name!r}")
        if relative.name.endswith(SIDECAR_SUFFIXES + (MANIFEST_SUFFIX,)):
            raise Refusal(f"a sidecar is not a release subject: {name!r}")
        path = workspace / relative
        if path.is_symlink() or not path.is_file():
            raise Refusal(f"release file is missing or not a regular file: {name!r}")
        resolved = path.resolve()
        if root not in resolved.parents:
            raise Refusal(f"release file resolves outside the workspace: {name!r}")
        if path.name in seen_names:
            raise Refusal(f"two release files share the name {path.name!r}; sidecars are named by the file name")
        seen_names.add(path.name)
        files.append(path)
    return files


def refuse_stale_sidecars(files: list[Path]) -> None:
    """Any sidecar beside a release file predates this build and is stale."""
    for file in files:
        for suffix in SIDECAR_SUFFIXES + (MANIFEST_SUFFIX,):
            sidecar = file.with_name(file.name + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                raise Refusal(f"a sidecar already exists and would be stale: {sidecar}; generate into a fresh build")


def sidecar_suffixes(spdx_2_3: bool) -> tuple[str, ...]:
    return (".spdx.json", ".cdx.json", ".spdx-2.3.json") if spdx_2_3 else (".spdx.json", ".cdx.json")


def created_from_commit(timestamp: str) -> str:
    """The generation time is the source commit's, in UTC, never the wall clock."""
    if not re.fullmatch(r"[0-9]{1,12}", timestamp.strip()):
        raise Refusal("the source commit timestamp is not a positive epoch second count")
    moment = datetime.datetime.fromtimestamp(int(timestamp.strip()), datetime.timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def https_base(value: str, what: str) -> str:
    if not value.startswith("https://") or not clean(value) or any(c in value for c in "?#\\ ") or "@" in value.split("/")[2]:
        raise Refusal(f"{what} must be an HTTPS URL without query, fragment, userinfo or whitespace: {value!r}")
    return value.rstrip("/")


def context_for(namespace_base: str, commit: str, filename: str, revision: str, created: str, author: str, generator: str) -> dict[str, str]:
    return {
        "namespace": f"{namespace_base}/{commit}/{filename}/revision-{revision}",
        "revision": revision,
        "created": created,
        "author": author,
        "generator": generator,
    }


def subject_for(filename: str, version: str, target: str, url_base: str) -> dict[str, str]:
    return {"filename": filename, "version": version, "target": target, "sha256": "", "url": f"{url_base}/{filename}"}


def member_components(composition: dict, listing: dict) -> dict:
    """Account for every member of a source archive, as Blueprint's own release does.

    A member a reviewed component already states by SHA-256, or an input
    disposition already names, is left to the review. Every other member
    becomes a component with `sha256-<digest>` as its version and
    `urn:blueprint:archive-member:<name>:<digest>` as its identity, with
    NOASSERTION for what nobody reviewed, contained by the subject. The
    archive, not the bundler's argument list, is then the authority.
    """
    members = listing.get("members")
    if not isinstance(members, list) or not all(isinstance(member, dict) and isinstance(member.get("name"), str) and isinstance(member.get("sha256"), str) and SHA256.fullmatch(member["sha256"]) for member in members):
        raise Refusal("the members listing must name each member with its SHA-256")
    stated = {component.get("sha256") for component in composition.get("components", []) if isinstance(component, dict)}
    disposed = {entry.get("input") for entry in composition.get("inputs", []) if isinstance(entry, dict)}
    taken = {component.get("id") for component in composition.get("components", []) if isinstance(component, dict)}
    merged = dict(composition)
    merged["components"] = list(composition.get("components", []))
    merged["relationships"] = list(composition.get("relationships", []))
    for member in members:
        name, digest = member["name"], member["sha256"]
        if digest in stated or f"archive-member:{name}:{digest}" in disposed:
            continue
        identifier = "member-" + MEMBER_ID.sub("-", name).strip("-.") or "member"
        candidate, counter = identifier, 2
        while candidate in taken:
            candidate, counter = f"{identifier}-{counter}", counter + 1
        taken.add(candidate)
        merged["components"].append({
            "id": candidate, "name": name, "version": f"sha256-{digest}", "producer": "NOASSERTION", "license": "NOASSERTION", "license_url": "",
            "identity": f"urn:blueprint:archive-member:{name}:{digest}", "download": "NOASSERTION", "sha256": digest, "modified": "", "target": "", "origin": "reviewed",
        })
        merged["relationships"].append({"from": "Subject", "to": candidate, "relation": "contains", "scope": "", "basis": f"Member {name} of the archive roc bundle produced; digest is the member's bytes"})
    return merged


def merge_imports(composition: dict, imports: dict | None, imports_path: str) -> dict:
    """Add a generated imports file's documents to the reviewed composition.

    Only `imported_documents` is taken from the generated file: the composition's
    own `unavailable_sboms` stay the reviewed opt-outs, so a stub in the
    generated file never becomes an exemption by itself.
    """
    merged = dict(composition)
    own = composition.get("imported_documents", [])
    if not isinstance(own, list):
        raise Refusal("composition imported_documents must be a list")
    if imports is None:
        return merged
    if imports.get("profile") != "roc-automation-sbom-imports-v1":
        raise Refusal(f"{imports_path} is not a roc-automation-sbom-imports-v1 file")
    added = imports.get("imported_documents", [])
    if not isinstance(added, list) or not all(isinstance(entry, dict) for entry in added):
        raise Refusal(f"{imports_path} imported_documents must be a list of entries")
    ids = [entry.get("id", "") for entry in own + added]
    if any(not isinstance(identifier, str) or not IMPORT_ID.fullmatch(identifier) for identifier in ids):
        raise Refusal("every imported document needs an identifier of letters, digits, '.' and '-'")
    if len(set(ids)) != len(ids):
        raise Refusal(f"{imports_path} repeats an import identifier the composition already uses")
    merged["imported_documents"] = own + added
    return merged


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def private_directory(directory: Path) -> Path:
    if directory.is_symlink():
        raise Refusal("the staging directory must not be a symlink")
    directory.mkdir(parents=True, exist_ok=False)
    directory.chmod(0o700)
    return directory


def write_json(path: Path, value: dict, limit: int | None = None) -> None:
    with path.open("x", encoding="utf-8") as output:
        json.dump(value, output, indent=2, sort_keys=True)
        output.write("\n")
    if limit is not None and path.stat().st_size > limit:
        raise Refusal(f"staged composition exceeds the {limit // MAX_JSON_BYTES} MiB bound Blueprint accepts: {path}")


def dependency_report(composition: dict, inventory: dict) -> list[dict[str, str]]:
    """Per locked dependency: bound, or unavailable with the reviewed reason."""
    rows = []
    bound = {entry.get("subject", {}).get("component", ""): entry for entry in composition.get("imported_documents", []) if isinstance(entry, dict) and isinstance(entry.get("subject"), dict)}
    unavailable = {entry.get("component", ""): entry for entry in composition.get("unavailable_sboms", []) if isinstance(entry, dict)}
    for component in composition.get("components", []):
        if not isinstance(component, dict):
            continue
        origin = str(component.get("origin", ""))
        if not origin.startswith(("source:", "program:")):
            continue
        identifier = str(component.get("id", ""))
        if identifier in bound:
            rows.append({"component": identifier, "origin": origin, "state": "bound", "detail": f"{bound[identifier].get('url', '')} sha256 {bound[identifier].get('sha256', '')}"})
        elif identifier in unavailable:
            entry = unavailable[identifier]
            rows.append({"component": identifier, "origin": origin, "state": "unavailable", "detail": f"{entry.get('reason', '')} (contact {entry.get('contact', '')})"})
        else:
            rows.append({"component": identifier, "origin": origin, "state": "missing", "detail": "neither a bound supplier SBOM nor an unavailable_sboms entry"})
    gaps = [entry for entry in inventory.get("coverage", []) if isinstance(entry, dict) and entry.get("status") == "incomplete"]
    for gap in gaps:
        rows.append({"component": str(gap.get("scope", "")), "origin": "coverage", "state": "incomplete", "detail": f"{gap.get('reason', '')} (contact {gap.get('contact', '')})"})
    return rows


SIDECAR_FORMATS = {".spdx.json": "SPDX-3.0.1", ".cdx.json": "CycloneDX-1.7", ".spdx-2.3.json": "SPDX-2.3", MANIFEST_SUFFIX: "manifest"}


def evidence_envelope(env: dict[str, str], workspace: Path, files: list[Path], sidecars: dict[Path, list[tuple[Path, str]]],
                      tool: dict, project_version: tuple[str, str], target: str, version: str, url_base: str,
                      commit: str, created: str, rows: list[dict[str, str]], inventories: dict[Path, Path] | None = None) -> dict:
    """The producer's handoff: every file and sidecar by name, size and SHA-256, bound to source and run.

    This is a transfer manifest for the independent validator and publisher,
    not a component inventory; composition stays in the sidecars.
    """
    subjects = []
    for file in files:
        subjects.append({
            "name": file.name,
            "path": str(file.relative_to(workspace)),
            "size": file.stat().st_size,
            "sha256": sha256_of(file),
            "target": target,
            "version": version,
            "url": f"{url_base}/{file.name}",
            "inventory_sha256": sha256_of(inventories[file]) if inventories and file in inventories else "",
            "sidecars": [
                {"name": sidecar.name, "path": str(sidecar.relative_to(workspace)), "size": sidecar.stat().st_size, "sha256": digest,
                 "format": next(fmt for suffix, fmt in SIDECAR_FORMATS.items() if sidecar.name.endswith(suffix))}
                for sidecar, digest in sidecars[file]
            ],
        })
    return {
        "format": "roc-automation-sbom-evidence-v1",
        "source": {"server": env.get("GITHUB_SERVER_URL", ""), "repository": env.get("GITHUB_REPOSITORY", ""), "commit": commit},
        "producer": {"workflow": env.get("GITHUB_WORKFLOW_REF", ""), "run_id": env.get("GITHUB_RUN_ID", ""), "run_attempt": env.get("GITHUB_RUN_ATTEMPT", "")},
        "generator": generator_record(tool, project_version, created),
        "subjects": subjects,
        "dependencies": [row for row in rows if row["origin"] != "coverage"],
        "coverage": [{"scope": row["component"], "status": "incomplete", "detail": row["detail"]} for row in rows if row["origin"] == "coverage"],
    }


def generator_record(tool: dict, project_version: tuple[str, str], created: str) -> dict[str, str]:
    """Who generated the documents: the Blueprint release and its tool, or the Kai release and its sbom plugin."""
    project, release = project_version
    if project == "kai":
        return {"tool": tool["url"], "tool_sha256": "", "generator": tool["generator"], "kai": release, "created": created}
    return {"tool": tool["url"], "tool_sha256": tool.get("sha256", ""), "compiler": tool["compiler"], "generator": tool["generator"], "blueprint": release, "created": created}


def summary_text(files: list[Path], sidecars: dict[Path, list[tuple[Path, str]]], rows: list[dict[str, str]], tool: dict[str, str], target: str) -> str:
    runner = f"on compiler `{tool['compiler']}`" if tool["compiler"] else "built by kai"
    lines = ["", "### SBOM sidecars", "", f"Tool `{tool['url']}` {runner}; target `{target}`.", "", "| Release file | Sidecar | SHA-256 |", "| --- | --- | --- |"]
    for file in files:
        for sidecar, digest in sidecars[file]:
            lines.append(f"| `{file.name}` | `{sidecar.name}` | `{digest}` |")
    lines += ["", "| Dependency or scope | Origin | State | Detail |", "| --- | --- | --- | --- |"]
    for row in rows:
        detail = row["detail"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| `{row['component']}` | `{row['origin']}` | {row['state']} | {detail} |")
    if not rows:
        lines.append("| (none) | | | No locked dependency and no incomplete coverage scope |")
    return "\n".join(lines) + "\n"


def main(run: Run = run_command, download: setup_blueprint.Fetch = setup_blueprint.fetch, list_members: Callable[[Path], dict] = archive_members) -> None:
    env = os.environ
    workspace = Path(env.get("GITHUB_WORKSPACE", ".")).resolve()
    project = setup_kai.project_tool(workspace)
    if project == "kai":
        binary = env.get("INPUT_KAI_BINARY") or "kai"
        if not clean(binary) or shutil.which(binary) is None:
            raise Refusal(f"kai-binary is not an executable on this runner: {binary!r}")
    else:
        binary = env.get("INPUT_BLUEPRINT_BINARY") or "blueprint"
        if not clean(binary) or shutil.which(binary) is None:
            raise Refusal(f"blueprint-binary is not an executable on this runner: {binary!r}")
    roc = env.get("INPUT_ROC_BINARY") or "roc"
    version = env.get("INPUT_VERSION", "").strip()
    if not VERSION.fullmatch(version):
        raise Refusal("version must be a release version such as 0.12.0 or 0.12.0-rc1")
    target = env.get("INPUT_TARGET", "").strip()
    if target != "source" and not SYSTEM.fullmatch(target):
        raise Refusal("target must be a Blueprint system name such as x86_64-linux, or source for a source bundle")
    revision = env.get("INPUT_REVISION", "1").strip() or "1"
    if not re.fullmatch(r"[1-9][0-9]*", revision):
        raise Refusal("revision must be a positive integer")
    author = env.get("INPUT_AUTHOR", "").strip()
    if not AGENT.fullmatch(author) or not clean(author):
        raise Refusal("author must be an SPDX agent such as 'Organization: Example Ltd' or 'Person: A Maintainer'")
    spdx_2_3 = setup_blueprint.boolean_input(env.get("INPUT_SPDX_2_3", "false"), "spdx-2-3")
    archive_members_mode = env.get("INPUT_ARCHIVE_MEMBERS", "generate") or "generate"
    if archive_members_mode not in ("generate", "reviewed"):
        raise Refusal("archive-members must be generate or reviewed")
    server, repository = env.get("GITHUB_SERVER_URL", "https://github.com"), env.get("GITHUB_REPOSITORY", "")
    url_base = https_base(env.get("INPUT_URL_BASE", "").strip() or f"{server}/{repository}/releases/download/{version}", "url-base")
    namespace_base = https_base(env.get("INPUT_NAMESPACE_BASE", "").strip() or f"{server}/{repository}/sbom", "namespace-base")

    files = release_files(env.get("INPUT_FILES", ""), workspace)
    refuse_stale_sidecars(files)
    if project == "kai":
        kaifile = setup_kai.read_kaifile(workspace / setup_kai.KAIFILE)
        project_version = setup_kai.version_in(kaifile)
        tool = kai_sbom_tool(project_version, SBOM_TOOLS)
        named = setup_kai.plugin_url(kaifile, "sbom")
        if named != tool["url"]:
            raise Refusal(f"Kaifile.roc names the sbom plugin {named}, but Kai {project_version}'s reviewed sbom plugin is {tool['url']}")
        installed = succeed(run, [binary, "--version"], workspace, "kai --version").strip()
        if installed != project_version:
            raise Refusal(f"kai-binary reports {installed!r}, but Kaifile.roc names Kai {project_version}; install it with actions/setup-kai")
        tool_command = [binary, "sbom"]
        tool_identity = {"url": tool["url"], "sha256": ""}
    else:
        project_version = setup_blueprint.version_in(setup_blueprint.read_config(workspace / "Blueprint.roc"))
        tool = sbom_tool(project_version, SBOM_TOOLS)
        system = setup_blueprint.SYSTEMS.get((env.get("RUNNER_OS", ""), env.get("RUNNER_ARCH", "")), "")
        expected = tool["binaries"].get(system, "")
        if expected:
            # A released native tool: admitted by its reviewed digest, no compiler needed.
            installed_binary = install_binary(project_version, system, expected, Path(env["RUNNER_TEMP"]) / "roc-automation-blueprint-sbom", download)
            tool_command = [str(installed_binary)]
            tool_identity = {"url": binary_url(project_version, system), "sha256": expected}
        else:
            if not clean(roc) or shutil.which(roc) is None:
                raise Refusal(f"roc-binary is not an executable on this runner: {roc!r}; roc-blueprint {project_version} records no native SBOM tool binary for {system or 'this runner'}, so the app bundle needs the compiler {tool['compiler']}")
            compiler_output = succeed(run, [roc, "version"], workspace, f"{roc} version")
            if not compiler_matches(compiler_output, tool["compiler"]):
                raise Refusal(
                    f"roc-binary reports {compiler_output.strip().splitlines()[0] if compiler_output.strip() else 'nothing'!r}, but roc-blueprint {project_version}'s SBOM tool "
                    f"needs compiler {tool['compiler']}; provision that nightly through a Blueprint Fetch recipe and actions/blueprint-tool, or roc-lang/setup-roc with nightly-tag"
                )
            tool_command = source_tool_command(roc, tool)
            tool_identity = {"url": tool["url"], "sha256": ""}
    payload_bytes = tool["payload_bytes"]
    complete_manifest = "complete-manifest" in tool["features"]
    composition_path = workspace / (env.get("INPUT_COMPOSITION", "").strip() or "sbom/composition.json")
    composition = read_bounded_json(composition_path, "composition", payload_bytes)
    imports_name = env.get("INPUT_IMPORTS", "").strip()
    imports = read_bounded_json(workspace / imports_name, "imports", payload_bytes) if imports_name else None
    composition = merge_imports(composition, imports, imports_name)

    commit = succeed(run, ["git", "rev-parse", "HEAD"], workspace, "git rev-parse HEAD").strip()
    if not COMMIT.fullmatch(commit):
        raise Refusal("the workspace is not a checkout at a full commit")
    event = env.get("GITHUB_SHA", "")
    if event and event != commit:
        raise Refusal(f"checkout {commit} differs from the event commit {event}; sidecars bind to the tested commit only")
    created = created_from_commit(succeed(run, ["git", "show", "-s", "--format=%ct", commit], workspace, "git show"))

    staging = private_directory(Path(env["RUNNER_TEMP"]) / f"roc-automation-sbom-{secrets.token_hex(8)}")
    if project == "blueprint":
        spec = succeed(run, [binary, "spec"], workspace, "blueprint spec")
        if not spec.strip():
            raise Refusal("blueprint spec printed nothing")
        (staging / "Spec").write_text(spec, encoding="utf-8")
        lock = workspace / "Blueprint.lock"
        if lock.is_symlink() or not lock.is_file():
            raise Refusal("Blueprint.lock must be a regular checked-in file")
        shutil.copyfile(lock, staging / "Lock")
    # kai runs in the repository, where it reads Kaifile.roc and .kai/lock.json.
    tool_cwd = workspace if project == "kai" else staging
    features = set(tool["features"])
    member_files = {file for file in files if "members" in features and target == "source" and file.name.endswith(".tar.zst")}
    with_manifest = "manifest" in features

    def inspect(composition_path: Path, inventory_path: Path, listing: Path | None) -> dict:
        if project == "kai":
            command = tool_command + ["inspect", "--composition", str(composition_path), "--output", str(inventory_path)]
            command += (["--members", str(listing)] if listing is not None else []) + ["--", target]
        else:
            command = tool_command + ["inspect", str(staging / "Spec"), str(staging / "Lock"), str(composition_path), target, str(inventory_path)]
            if listing is not None:
                command += ["--members", str(listing)]
        result = run(command, tool_cwd)
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        if result.returncode:
            message = result.stderr.strip() or result.stdout.strip()
            hint = ""
            if "has no supplier SBOM" in message:
                hint = "; run actions/fetch-dependency-sboms to fetch the supplier's sidecar by naming convention, or complete the unavailable_sboms stub it reports"
            raise Refusal(f"inspect refused the composition for {target}: {message}{hint}")
        return read_bounded_json(inventory_path, "inventory", payload_bytes)

    def bind_commands(inventory: Path, subject: Path, context: Path, file: Path) -> tuple[list[str], list[str]]:
        """The default bind, and the one that adds the SPDX 2.3 rendering."""
        spdx_2_3_path = str(file.with_name(file.name + ".spdx-2.3.json"))
        if project == "kai":
            base = tool_command + ["bind", "--inventory", str(inventory), "--subject", str(subject), "--context", str(context), "--artifact", str(file)]
            return base, base + ["--output", spdx_2_3_path, "--spdx-2-3"]
        base = tool_command + ["bind", str(inventory), str(subject), str(context), str(file)]
        return base, base + [spdx_2_3_path, "--spdx-2.3"]

    inventories: dict[Path, Path] = {}
    inventory_path = staging / "inventory.json"
    if len(member_files) != len(files):
        # Composition-only inventories apply to binaries, ZIPs and PDFs. Only
        # source .tar.zst bundles enter the archive member listing path.
        write_json(staging / "composition.json", composition, payload_bytes)
        inventory = inspect(staging / "composition.json", inventory_path, None)
        inventories = {file: inventory_path for file in files if file not in member_files}
    written: dict[Path, list[tuple[Path, str]]] = {}
    manifests: dict[Path, tuple[Path, str]] = {}
    for index, file in enumerate(files):
        directory = private_directory(staging / f"subject-{index}")
        if file in member_files:
            # The archive is the authority on a source bundle's contents: list
            # it, account for every member, and inspect this file's inventory.
            listing_path = directory / "members.json"
            if project == "kai":
                write_json(listing_path, list_members(file), payload_bytes)
            else:
                succeed(run, tool_command + ["members", str(file), str(listing_path)], staging, f"members {file.name}")
            listing = read_bounded_json(listing_path, "members listing", payload_bytes)
            accounted = member_components(composition, listing) if archive_members_mode == "generate" else composition
            write_json(directory / "composition.json", accounted, payload_bytes)
            inventories[file] = directory / "inventory.json"
            inventory = inspect(directory / "composition.json", inventories[file], listing_path)
            if index == 0:
                inventory_path = inventories[file]
        subject = subject_for(file.name, version, target, url_base)
        if project == "kai":
            # kai reads no release file, so the subject states its bytes.
            subject = {**subject, "sha256": sha256_of(file), "size": file.stat().st_size}
        write_json(directory / "subject.json", subject)
        write_json(directory / "context.json", context_for(namespace_base, commit, file.name, revision, created, author, tool["generator"]))
        base, with_spdx_2_3 = bind_commands(inventories[file], directory / "subject.json", directory / "context.json", file)
        succeed(run, base, tool_cwd, f"bind {file.name}")
        if spdx_2_3 and not complete_manifest:
            succeed(run, with_spdx_2_3, tool_cwd, f"bind {file.name} (SPDX 2.3)")
        written[file] = []
        for suffix in sidecar_suffixes(spdx_2_3 or complete_manifest):
            sidecar = file.with_name(file.name + suffix)
            if sidecar.is_symlink() or not sidecar.is_file():
                raise Refusal(f"bind did not write {sidecar}")
            written[file].append((sidecar, sha256_of(sidecar)))
        if with_manifest:
            manifest = file.with_name(file.name + MANIFEST_SUFFIX)
            if manifest.is_symlink() or not manifest.is_file():
                raise Refusal(f"bind did not write {manifest}")
            if read_bounded_json(manifest, "manifest").get("profile") != tool["profile"]:
                raise Refusal(f"bind wrote an unexpected document profile; expected {tool['profile']}")
            manifests[file] = (manifest, sha256_of(manifest))

    for file in files:
        # With a manifest, check holds it to the files and checks the sidecars
        # it lists; a rendering the manifest does not list is named as well.
        command = tool_command + ["check"]
        documents = [sidecar for sidecar, _ in written[file]]
        if file in manifests:
            command += ["--manifest", str(manifests[file][0])]
            documents = [] if complete_manifest else [sidecar for sidecar in documents if sidecar.name.endswith(".spdx-2.3.json")]
        if project == "kai":
            command += ["--inventory", str(inventories[file]), "--"] + [str(sidecar) for sidecar in documents]
        else:
            command += ["--inventory", str(inventories[file]), "--artifacts", str(file.parent)] + [str(sidecar) for sidecar in documents]
        succeed(run, command, tool_cwd, f"check {file.name}")
        if project == "kai":
            # kai check holds the documents to the manifest but never reads the
            # release file; the file must be the bytes the manifest states.
            stated = read_bounded_json(manifests[file][0], "manifest").get("subject", {})
            actual = {"sha256": sha256_of(file), "size": file.stat().st_size}
            if not isinstance(stated, dict) or not hmac.compare_digest(str(stated.get("sha256", "")), actual["sha256"]) or stated.get("size") != actual["size"]:
                raise Refusal(f"{file.name} is {actual['size']} bytes with SHA-256 {actual['sha256']}, which differs from the subject its manifest states")

    for file in files:
        if file in manifests:
            written[file].append(manifests[file])
    assets = sorted({str(file.relative_to(workspace)) for file in files} | {str(sidecar.relative_to(workspace)) for file in files for sidecar, _ in written[file]})
    sidecars_only = sorted(str(sidecar.relative_to(workspace)) for file in files for sidecar, _ in written[file])
    digests = staging / "sidecars.sha256"
    with digests.open("x", encoding="utf-8") as output:
        for file in files:
            for sidecar, digest in written[file]:
                output.write(f"{digest}  {sidecar.relative_to(workspace)}\n")
    rows = dependency_report(composition, inventory)
    evidence = staging / "sbom-evidence.json"
    write_json(evidence, evidence_envelope(dict(env), workspace, files, written, {**tool, **tool_identity}, (project, project_version), target, version, url_base, commit, created, rows, inventories))
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        delimiter = f"roc-automation-{secrets.token_hex(8)}"
        output.write(f"assets<<{delimiter}\n" + "\n".join(assets) + f"\n{delimiter}\n")
        output.write(f"sidecars<<{delimiter}\n" + "\n".join(sidecars_only) + f"\n{delimiter}\n")
        output.write(f"run-attempt={env.get('GITHUB_RUN_ATTEMPT', '')}\ninventory={inventory_path}\ndigests={digests}\nevidence={evidence}\ntool-url={tool_identity['url']}\ntool-sha256={tool_identity['sha256']}\ncompiler={tool['compiler']}\ncommit={commit}\ncreated={created}\n")
    for file in files:
        for sidecar, digest in written[file]:
            print(f"{sidecar.relative_to(workspace)}: SHA-256 {digest}")
    for row in rows:
        print(f"{row['state']}: {row['component']} ({row['origin']}): {row['detail']}")
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write(summary_text(files, written, rows, {**tool, **tool_identity}, target))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(f"::error::{error}", file=sys.stderr)
        raise SystemExit(1) from error
