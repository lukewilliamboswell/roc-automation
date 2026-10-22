#!/usr/bin/env python3
"""Fetch suppliers' SBOMs for every dependency the Lock pins by file SHA-256.

This is the one step in the SBOM chain that reaches the network, so it holds
no write token and runs no repository code. For each component whose origin is
a locked `SourceFile` or fetched program, it tries `URL.spdx.json` then
`URL.cdx.json` by the naming convention, records the document's digest and
identity, and writes an imports file for `inspect` to verify. It never invents
an opt-out: a dependency with no published SBOM is reported as a stub for the
maintainer to complete, and a recorded document whose bytes changed fails.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

IMPORTS_PROFILE = "roc-automation-sbom-imports-v1"
SUFFIXES = (".spdx.json", ".cdx.json")
MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_LOCK_BYTES = 8 * 1024 * 1024
MAX_IMPORTS = 16
SHA256 = re.compile(r"[0-9a-f]{64}")
COMPONENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]*")

Fetch = Callable[[str], bytes | None]
Run = Callable[[list[str], Path], subprocess.CompletedProcess]


class Refusal(ValueError):
    """A reason the fetched record cannot be trusted or written."""


def run_command(command: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True)


def clean(value: str) -> bool:
    return bool(value) and not any(ord(c) < 32 or ord(c) == 127 for c in value)


def https_url(value: str) -> bool:
    """Only an immutable HTTPS address without userinfo, query or whitespace is fetched."""
    if not isinstance(value, str) or not value.startswith("https://") or not clean(value) or any(c in value for c in " ?#\\"):
        return False
    host = value[len("https://"):].split("/", 1)[0]
    return bool(host) and "@" not in host and host != "localhost" and not host.startswith("127.")


def fetch(url: str) -> bytes | None:
    """The bytes at an HTTPS URL, bounded; None when nothing is published there."""
    request = urllib.request.Request(url, headers={"User-Agent": "roc-automation-fetch-dependency-sboms", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if not response.geturl().startswith("https://"):
                raise Refusal(f"{url} redirected off HTTPS to {response.geturl()}")
            content = response.read(MAX_DOCUMENT_BYTES + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise Refusal(f"fetching {url} failed with HTTP {error.code}; a supplier that publishes nothing answers 404") from error
    except urllib.error.URLError as error:
        raise Refusal(f"fetching {url} failed: {error.reason}") from error
    if len(content) > MAX_DOCUMENT_BYTES:
        raise Refusal(f"{url} exceeds the 1 MiB bound Blueprint accepts for an imported SBOM")
    return content


def parse_json(content: bytes, what: str):
    def no_duplicates(pairs):
        record = {}
        for key, value in pairs:
            if key in record:
                raise Refusal(f"{what} repeats the key {key!r}")
            record[key] = value
        return record

    try:
        return json.loads(content.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Refusal(f"{what} is not valid UTF-8 JSON: {error}") from error


def read_bounded(path: Path, what: str, limit: int = MAX_DOCUMENT_BYTES) -> dict:
    if path.is_symlink() or not path.is_file():
        raise Refusal(f"{what} must be a regular file: {path}")
    with path.open("rb") as source:
        content = source.read(limit + 1)
    if len(content) > limit:
        raise Refusal(f"{what} exceeds {limit} bytes: {path}")
    document = parse_json(content, f"{what} {path}")
    if not isinstance(document, dict):
        raise Refusal(f"{what} must be a JSON object: {path}")
    return document


def text(value) -> str:
    return value if isinstance(value, str) else ""


def document_identity(document: dict) -> dict[str, str]:
    """The format, namespace and described subject of a supplier's SBOM.

    The namespace is what Blueprint compares with the import entry: the
    SpdxDocument's identifier before `#` in SPDX 3, `documentNamespace` in
    SPDX 2, and `serialNumber` in CycloneDX. The subject is what the document
    says it describes; Blueprint requires it to be the locked bytes.
    """
    if isinstance(document.get("@graph"), list):
        return spdx3_identity(document)
    if text(document.get("spdxVersion")).startswith("SPDX-"):
        return spdx2_identity(document)
    if document.get("bomFormat") == "CycloneDX":
        return cyclonedx_identity(document)
    raise Refusal("the document is neither SPDX 3 JSON-LD, SPDX 2 JSON nor a CycloneDX BOM")


def spdx3_identity(document: dict) -> dict[str, str]:
    elements = [entry for entry in document["@graph"] if isinstance(entry, dict)]
    by_id = {text(entry.get("spdxId")): entry for entry in elements if text(entry.get("spdxId"))}
    documents = [entry for entry in elements if entry.get("type") == "SpdxDocument"]
    if len(documents) != 1 or "#" not in text(documents[0].get("spdxId")):
        raise Refusal("an SPDX 3 document needs exactly one SpdxDocument element whose identifier is NAMESPACE#name")
    namespace = text(documents[0]["spdxId"]).split("#", 1)[0]
    roots = [text(identifier) for identifier in documents[0].get("rootElement", []) if isinstance(identifier, str)]
    subject: dict | None = None
    for root in roots:
        element = by_id.get(root)
        if element is None:
            continue
        if element.get("type") == "software_Sbom":
            inner = [by_id.get(text(identifier)) for identifier in element.get("rootElement", []) if isinstance(identifier, str)]
            packages = [entry for entry in inner if isinstance(entry, dict) and entry.get("type") == "software_Package"]
            if packages:
                subject = packages[0]
                break
        elif element.get("type") == "software_Package":
            subject = element
            break
    if subject is None:
        for relationship in elements:
            if relationship.get("type") in ("Relationship", "LifecycleScopedRelationship") and relationship.get("relationshipType") == "describes":
                targets = [by_id.get(text(identifier)) for identifier in relationship.get("to", []) if isinstance(identifier, str)]
                packages = [entry for entry in targets if isinstance(entry, dict) and entry.get("type") == "software_Package"]
                if packages:
                    subject = packages[0]
                    break
    sha256 = ""
    if subject is not None:
        for entry in subject.get("verifiedUsing", []):
            if isinstance(entry, dict) and text(entry.get("algorithm")).lower() == "sha256":
                sha256 = text(entry.get("hashValue")).lower()
    creation = next((text(entry.get("specVersion")) for entry in elements if entry.get("type") == "CreationInfo" and text(entry.get("specVersion"))), "")
    return {
        "format": f"SPDX-{creation or text(documents[0].get('specVersion')) or '3'}",
        "namespace": namespace,
        "name": text(subject.get("name")) if subject else "",
        "version": text(subject.get("software_packageVersion")) if subject else "",
        "sha256": sha256,
    }


def spdx2_identity(document: dict) -> dict[str, str]:
    namespace = text(document.get("documentNamespace"))
    if not namespace:
        raise Refusal("an SPDX 2 document needs a documentNamespace")
    packages = {text(entry.get("SPDXID")): entry for entry in document.get("packages", []) if isinstance(entry, dict)}
    described = [text(identifier) for identifier in document.get("documentDescribes", []) if isinstance(identifier, str)]
    for relationship in document.get("relationships", []):
        if isinstance(relationship, dict) and relationship.get("relationshipType") == "DESCRIBES" and relationship.get("spdxElementId") == text(document.get("SPDXID")):
            described.append(text(relationship.get("relatedSpdxElement")))
        if isinstance(relationship, dict) and relationship.get("relationshipType") == "DESCRIBED_BY" and relationship.get("relatedSpdxElement") == text(document.get("SPDXID")):
            described.append(text(relationship.get("spdxElementId")))
    subject = next((packages[identifier] for identifier in described if identifier in packages), None)
    sha256 = ""
    if subject is not None:
        for entry in subject.get("checksums", []) or []:
            if isinstance(entry, dict) and text(entry.get("algorithm")).upper() == "SHA256":
                sha256 = text(entry.get("checksumValue")).lower()
    return {
        "format": text(document.get("spdxVersion")),
        "namespace": namespace,
        "name": text(subject.get("name")) if subject else "",
        "version": text(subject.get("versionInfo")) if subject else "",
        "sha256": sha256,
    }


def cyclonedx_identity(document: dict) -> dict[str, str]:
    serial = text(document.get("serialNumber"))
    if not serial:
        raise Refusal("a CycloneDX BOM needs a serialNumber to identify it")
    metadata = document.get("metadata") if isinstance(document.get("metadata"), dict) else {}
    subject = metadata.get("component") if isinstance(metadata.get("component"), dict) else None
    sha256 = ""
    if subject is not None:
        for entry in subject.get("hashes", []) or []:
            if isinstance(entry, dict) and text(entry.get("alg")).upper() == "SHA-256":
                sha256 = text(entry.get("content")).lower()
    return {
        "format": f"CycloneDX-{text(document.get('specVersion'))}",
        "namespace": serial,
        "name": text(subject.get("name")) if subject else "",
        "version": text(subject.get("version")) if subject else "",
        "sha256": sha256,
    }


def lock_snapshot(run: Run, blueprint: str, workspace: Path) -> dict:
    """The public format-2 lock report; never the lock's private representation."""
    result = run([blueprint, "lock", "show", "--json"], workspace)
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    if result.returncode:
        raise Refusal(f"blueprint lock show --json failed with status {result.returncode}")
    if len(result.stdout.encode("utf-8")) > MAX_LOCK_BYTES:
        raise Refusal("the lock report exceeds 8 MiB")
    report = parse_json(result.stdout.encode("utf-8"), "lock report")
    if not isinstance(report, dict) or report.get("format") != 2 or type(report.get("format")) is not int:
        raise Refusal("unsupported lock report format; this action reads format 2")
    if not isinstance(report.get("sources"), dict) or not isinstance(report.get("fetched"), list):
        raise Refusal("the lock report needs sources and fetched entries")
    return report


def consumed_components(composition: dict) -> list[dict]:
    components = composition.get("components")
    if not isinstance(components, list) or not all(isinstance(entry, dict) for entry in components):
        raise Refusal("composition components must be a list of records")
    consumed = [entry for entry in components if text(entry.get("origin")).startswith(("source:", "program:"))]
    for entry in consumed:
        if not COMPONENT_ID.fullmatch(text(entry.get("id"))):
            raise Refusal(f"component identifier {entry.get('id')!r} is not letters, digits, '.' and '-'")
    return consumed


def locked_file(component: dict, report: dict, target: str) -> dict[str, str]:
    """Where the Lock says the dependency's bytes come from, and their SHA-256.

    A source pinned by a tree or NAR digest has no file SHA-256, so no
    supplier SBOM can bind to it; it is reported as unbindable, not fetched.
    """
    origin = text(component.get("origin"))
    if origin.startswith("source:"):
        name = origin[len("source:"):]
        source = report["sources"].get(name)
        if not isinstance(source, dict) or source.get("name") != name:
            raise Refusal(f"component {component['id']} names locked source {name!r}, which the Lock does not pin")
        if source.get("provider") != "file" or not SHA256.fullmatch(text(source.get("flatSha256"))):
            return {"state": "unbindable", "urls": [], "sha256": "", "target": target, "detail": f"source {name} is pinned by a {text(source.get('digestKind')) or 'tree'} digest ({text(source.get('content_identity'))}), not a file SHA-256"}
        return {"state": "pinned", "urls": [text(source.get("ref"))], "sha256": text(source["flatSha256"]), "target": target, "detail": ""}
    name, _, system = origin[len("program:"):].rpartition(":")
    entries = [entry for entry in report["fetched"] if isinstance(entry, dict) and entry.get("name") == name and entry.get("system") == system]
    if len(entries) != 1:
        raise Refusal(f"component {component['id']} names fetched program {name!r} on {system!r}, which the Lock does not pin once")
    entry = entries[0]
    if not SHA256.fullmatch(text(entry.get("sha256"))) or entry.get("content_identity") != "sha256:" + text(entry.get("sha256")):
        raise Refusal(f"fetched program {name} on {system} has no consistent SHA-256 in the Lock")
    urls = [url for url in entry.get("urls", []) if isinstance(url, str)]
    return {"state": "pinned", "urls": urls, "sha256": entry["sha256"], "target": system, "detail": ""}


def find_published(urls: list[str], download: Fetch) -> tuple[str, bytes] | None:
    """The first sidecar the naming convention finds beside any of the locked URLs."""
    for url in urls:
        if not https_url(url):
            raise Refusal(f"a locked dependency URL is not an HTTPS address this action fetches from: {url!r}")
        for suffix in SUFFIXES:
            content = download(url + suffix)
            if content is not None:
                return url + suffix, content
    return None


def import_entry(component: dict, pinned: dict[str, str], url: str, content: bytes, contact: str) -> dict:
    """An imported_documents entry whose subject binds the supplier's SBOM to the locked bytes.

    What the document says it describes is compared with the Lock here so the
    failure names the supplier's statement; `inspect` repeats the comparison
    and remains the authority.
    """
    identity = document_identity(parse_json(content, f"SBOM at {url}"))
    filename = url[: -len(next(suffix for suffix in SUFFIXES if url.endswith(suffix)))].rsplit("/", 1)[-1]
    version = text(component.get("version")) or identity["version"] or f"sha256-{pinned['sha256']}"
    if identity["sha256"] and identity["sha256"] != pinned["sha256"]:
        raise Refusal(f"{url} describes {identity['name'] or 'a file'} with SHA-256 {identity['sha256']}, not the locked bytes {pinned['sha256']} of {component['id']}; the supplier's SBOM is for other bytes")
    if identity["name"] and identity["name"] != filename:
        raise Refusal(f"{url} describes {identity['name']!r}, not {filename!r}; the sidecar beside {component['id']} is for another file")
    if identity["version"] and identity["version"] != version:
        raise Refusal(f"{url} describes version {identity['version']!r}, but the composition reviews {component['id']} as {version!r}; agree on the release the supplier names")
    return {
        "id": f"{component['id']}-sbom",
        "url": url,
        "sha256": hashlib.sha256(content).hexdigest(),
        "namespace": identity["namespace"],
        "target": pinned["target"],
        "document": content.decode("utf-8"),
        "relation": "dependsOn",
        "basis": f"Supplier's published {identity['format']} SBOM beside the locked file, found by naming convention",
        "contact": contact,
        "subject": {"component": component["id"], "filename": filename, "version": version, "sha256": pinned["sha256"]},
    }


def stub(component: dict, urls: list[str]) -> dict[str, str]:
    """A ready-to-paste opt-out the maintainer must complete; never committed as is."""
    return {
        "component": component["id"],
        "reason": f"REPLACE: state why no supplier SBOM is published beside {urls[0] if urls else 'the locked file'}",
        "contact": "REPLACE: who can resolve this gap, such as the supplier's security contact",
    }


def check_recorded(previous: dict | None, entries: list[dict], output: Path) -> None:
    """Re-fetching must return the recorded bytes; changed bytes are never adopted silently."""
    if previous is None:
        return
    if previous.get("profile") != IMPORTS_PROFILE:
        raise Refusal(f"{output} exists but is not a {IMPORTS_PROFILE} file; move it aside to regenerate")
    recorded = {text(entry.get("id")): entry for entry in previous.get("imported_documents", []) if isinstance(entry, dict)}
    fetched = {entry["id"]: entry for entry in entries}
    for identifier, entry in recorded.items():
        now = fetched.get(identifier)
        if now is None:
            raise Refusal(f"{output} records {identifier} from {text(entry.get('url'))}, which the supplier no longer publishes; review before removing the record")
        if now["sha256"] != text(entry.get("sha256")) or now["url"] != text(entry.get("url")):
            raise Refusal(f"the supplier's SBOM for {identifier} changed: {output} records {text(entry.get('sha256'))} at {text(entry.get('url'))}, the fetch returned {now['sha256']} at {now['url']}; review the new document and update the record deliberately")


def summary_text(rows: list[dict[str, str]], output: Path) -> str:
    lines = ["", "### Supplier SBOMs", "", f"Imports written to `{output}`.", "", "| Dependency | Origin | State | Detail |", "| --- | --- | --- | --- |"]
    for row in rows:
        detail = row["detail"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| `{row['component']}` | `{row['origin']}` | {row['state']} | {detail} |")
    if not rows:
        lines.append("| (none) | | | The composition consumes no locked dependency |")
    return "\n".join(lines) + "\n"


def main(run: Run = run_command, download: Fetch = fetch) -> None:
    env = os.environ
    workspace = Path(env.get("GITHUB_WORKSPACE", ".")).resolve()
    blueprint = env.get("INPUT_BLUEPRINT_BINARY") or "blueprint"
    if not clean(blueprint) or shutil.which(blueprint) is None:
        raise Refusal(f"blueprint-binary is not an executable on this runner: {blueprint!r}")
    target = env.get("INPUT_TARGET", "").strip()
    if target != "source" and not re.fullmatch(r"[a-z0-9_]+-[a-z0-9]+", target):
        raise Refusal("target must be a Blueprint system name such as x86_64-linux, or source")
    policies = {}
    for name in ("missing", "stale-opt-outs"):
        value = env.get("INPUT_" + name.upper().replace("-", "_"), "fail")
        if value not in ("fail", "warn"):
            raise Refusal(f"{name} must be fail or warn")
        policies[name] = value
    server, repository = env.get("GITHUB_SERVER_URL", "https://github.com"), env.get("GITHUB_REPOSITORY", "")
    contact = env.get("INPUT_CONTACT", "").strip() or f"{server}/{repository}/security/policy"
    if not https_url(contact):
        raise Refusal("contact must be an HTTPS URL that names who resolves a coverage gap")
    output_name = env.get("INPUT_OUTPUT", "").strip() or "sbom/imports.json"
    output = workspace / output_name
    if Path(output_name).is_absolute() or ".." in Path(output_name).parts or not clean(output_name):
        raise Refusal("output must be a relative workspace path without '..'")

    composition = read_bounded(workspace / (env.get("INPUT_COMPOSITION", "").strip() or "sbom/composition.json"), "composition")
    consumed = consumed_components(composition)
    bound = {text(entry.get("subject", {}).get("component")) for entry in composition.get("imported_documents", []) if isinstance(entry, dict) and isinstance(entry.get("subject"), dict)}
    exempt = {text(entry.get("component")): entry for entry in composition.get("unavailable_sboms", []) if isinstance(entry, dict)}
    previous = read_bounded(output, "recorded imports", MAX_IMPORTS * MAX_DOCUMENT_BYTES) if output.exists() or output.is_symlink() else None
    report = lock_snapshot(run, blueprint, workspace) if consumed else {"format": 2, "sources": {}, "fetched": []}

    entries: list[dict] = []
    rows: list[dict[str, str]] = []
    stubs: list[dict[str, str]] = []
    unbindable: list[dict[str, str]] = []
    stale: list[str] = []
    for component in consumed:
        identifier, origin = component["id"], text(component.get("origin"))
        if identifier in bound:
            rows.append({"component": identifier, "origin": origin, "state": "bound", "detail": "imported in the composition"})
            continue
        pinned = locked_file(component, report, target)
        if pinned["state"] == "unbindable":
            unbindable.append({"component": identifier, "detail": pinned["detail"]})
            state = "unavailable" if identifier in exempt else "unbindable"
            rows.append({"component": identifier, "origin": origin, "state": state, "detail": pinned["detail"] + (f"; reviewed reason: {text(exempt[identifier].get('reason'))}" if identifier in exempt else "")})
            continue
        published = find_published(pinned["urls"], download)
        if published is None:
            if identifier in exempt:
                rows.append({"component": identifier, "origin": origin, "state": "unavailable", "detail": f"{text(exempt[identifier].get('reason'))} (contact {text(exempt[identifier].get('contact'))})"})
            else:
                stubs.append(stub(component, pinned["urls"]))
                rows.append({"component": identifier, "origin": origin, "state": "missing", "detail": f"no .spdx.json or .cdx.json beside {pinned['urls'][0] if pinned['urls'] else 'the locked file'}"})
            continue
        url, content = published
        entry = import_entry(component, pinned, url, content, contact)
        if identifier in exempt:
            stale.append(identifier)
            rows.append({"component": identifier, "origin": origin, "state": "stale opt-out", "detail": f"unavailable_sboms says none is published, but {url} exists (sha256 {entry['sha256']})"})
            continue
        entries.append(entry)
        rows.append({"component": identifier, "origin": origin, "state": "fetched", "detail": f"{url} sha256 {entry['sha256']} ({entry['namespace']})"})
    if len(entries) > MAX_IMPORTS:
        raise Refusal(f"Blueprint imports at most {MAX_IMPORTS} documents; {len(entries)} were fetched")
    check_recorded(previous, entries, output)

    record = {"profile": IMPORTS_PROFILE, "target": target, "imported_documents": entries, "unavailable_sboms_stub": stubs, "unbindable": unbindable}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(output)

    for row in rows:
        print(f"{row['state']}: {row['component']} ({row['origin']}): {row['detail']}")
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write(summary_text(rows, output.relative_to(workspace)))
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
        handle.write(f"imports={output}\nfetched={len(entries)}\nmissing={len(stubs)}\nunbindable={len(unbindable)}\nstale={len(stale)}\n")
    problems = []
    if stubs:
        problems.append((policies["missing"], f"{len(stubs)} locked dependenc{'y has' if len(stubs) == 1 else 'ies have'} no published SBOM: " + ", ".join(entry["component"] for entry in stubs) + f". Complete the unavailable_sboms stub in {output.relative_to(workspace)} with a real reason and contact, then add it to the composition:\n" + json.dumps({"unavailable_sboms": stubs}, indent=2)))
    if stale:
        problems.append((policies["stale-opt-outs"], "unavailable_sboms lists " + ", ".join(stale) + ", whose supplier now publishes a sidecar; remove the opt-out so the supplier's SBOM is imported"))
    for level, message in problems:
        if level == "warn":
            print(f"::warning::{message}")
    failures = [message for level, message in problems if level == "fail"]
    if failures:
        raise Refusal("\n".join(failures))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(f"::error::{error}", file=sys.stderr)
        raise SystemExit(1) from error
