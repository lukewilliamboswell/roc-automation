#!/usr/bin/env python3
"""Read which Kai release and plugins a checked-out Kaifile.roc names.

A repository on Kai names the Kai release it is written for by its platform
URL, `pf: platform "https://github.com/thebrandonlucas/kai/releases/download/vVERSION/HASH.tar.zst"`,
as a repository on Blueprint names its roc-blueprint release in
Blueprint.roc. The setup-kai action installs that release, and the SBOM
controllers hold the sbom plugin the Kaifile names to the one that release
publishes. Nothing here runs kai or the Kaifile: the header is read as text.

A repository that has Blueprint.roc stays on Blueprint, even with a
Kaifile.roc beside it, so that no existing caller changes path.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

RELEASES = "https://github.com/thebrandonlucas/kai/releases/download"
KAIFILE = "Kaifile.roc"
MAX_KAIFILE_BYTES = 1024 * 1024
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
# Kai's release bundles are named by the base58 content hash Roc checks.
BASE58 = r"[1-9A-HJ-NP-Za-km-z]+"
PLATFORM = re.compile(r"https://github\.com/thebrandonlucas/kai/releases/download/v(" + VERSION + r")/" + BASE58 + r"\.tar\.zst")
ENTRY = re.compile(r"([a-z][a-z0-9_]*)\s*:\s*(platform\s+)?\"([^\"\n]*)\"")


class Refusal(ValueError):
    """A reason the Kaifile does not name a Kai release this action installs."""


def project_tool(workspace: Path) -> str:
    """`kai` for a repository with Kaifile.roc and no Blueprint.roc; otherwise `blueprint`."""
    if (workspace / KAIFILE).is_file() and not (workspace / "Blueprint.roc").exists():
        return "kai"
    return "blueprint"


def read_kaifile(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise Refusal(f"{path} must be a regular checked-in file")
    content = path.read_bytes()
    if len(content) > MAX_KAIFILE_BYTES:
        raise Refusal(f"{path} exceeds 1 MiB")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise Refusal(f"{path} is not UTF-8") from error


def header(text: str) -> dict[str, tuple[bool, str]]:
    """The app header's entries, `name: [platform] "value"`, without comments.

    Only the first `app [...] { ... }` block is read, so a URL in the body or
    in a comment never selects the release.
    """
    lines = [line.split("#", 1)[0] for line in text.splitlines()]
    source = "\n".join(lines)
    start = re.search(r"^app\s*\[[^\]]*\]\s*\{", source, re.MULTILINE)
    if start is None:
        raise Refusal(f"{KAIFILE} has no app header")
    end = source.find("}", start.end())
    if end < 0:
        raise Refusal(f"{KAIFILE}'s app header is not closed")
    entries: dict[str, tuple[bool, str]] = {}
    for name, platform, value in ENTRY.findall(source[start.end():end]):
        if name in entries:
            raise Refusal(f"{KAIFILE}'s app header names {name} twice")
        entries[name] = (bool(platform), value)
    return entries


def version_in(text: str) -> str:
    """The Kai release whose platform the header's `pf` names."""
    entries = header(text)
    platform, url = entries.get("pf", (False, ""))
    if not platform:
        raise Refusal(f"{KAIFILE}'s app header has no pf: platform entry")
    match = PLATFORM.fullmatch(url)
    if match is None:
        raise Refusal(f"{KAIFILE} names its platform as {url!r}, not a Kai release bundle ({RELEASES}/vVERSION/HASH.tar.zst); "
                      "name the platform of the Kai release the repository uses")
    return match.group(1)


def plugin_url(text: str, name: str) -> str:
    """The URL the header names plugin `name` by, such as the sbom plugin."""
    platform, url = header(text).get(name, (False, ""))
    if platform or not url:
        raise Refusal(f"{KAIFILE}'s app header does not name the {name} plugin")
    return url


def main(argv: list[str]) -> None:
    if len(argv) != 2 or argv[0] not in ("version", "tool"):
        raise Refusal("usage: setup_kai.py version DIRECTORY | tool DIRECTORY")
    directory = Path(argv[1])
    if argv[0] == "tool":
        line = f"tool={project_tool(directory)}"
    else:
        version = version_in(read_kaifile(directory / KAIFILE))
        line = f"version={version}"
        print(f"{directory / KAIFILE} names Kai {version}")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(line + "\n")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (ValueError, OSError) as error:
        print(f"::error::{error}", file=sys.stderr)
        raise SystemExit(1) from error
