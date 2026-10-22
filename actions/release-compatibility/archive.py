"""Verify and extract an inert examples ZIP identically on Windows and Unix.

Only the standard library is needed. Nothing in the archive is executed here.
The entire inventory is validated before the destination directory is created.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import zipfile

MAX_ARCHIVE = 50_000_000
MAX_EXPANDED = 100_000_000
MAX_ENTRIES = 10_000
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def canonical(name):
    if not name or name.startswith("/") or len(name) > 4096:
        raise ValueError("unsafe ZIP path")
    for part in name.split("/"):
        if (not re.fullmatch(r"[A-Za-z0-9_.-]{1,255}", part)
                or part in (".", "..") or part.endswith(".")
                or part.split(".", 1)[0].upper() in RESERVED):
            raise ValueError("unsafe ZIP path or reserved Windows filename")
    return name


def inventory(archive, root):
    canonical(root)
    if "/" in root:
        raise ValueError("invalid ZIP root")
    entries = archive.infolist()
    if not entries or len(entries) > MAX_ENTRIES:
        raise ValueError("examples ZIP entry limit exceeded")
    seen, spellings, files, directories = set(), {}, set(), set()
    expanded = 0
    for entry in entries:
        # orig_filename retains NUL bytes which ZipInfo.filename truncates.
        if entry.orig_filename != entry.filename:
            raise ValueError("ZIP member contains a NUL")
        name = canonical(entry.filename.removesuffix("/"))
        if name != root and not name.startswith(root + "/"):
            raise ValueError("ZIP member escapes the declared root")
        key = name.lower()
        if key in seen:
            raise ValueError("duplicate or case-colliding ZIP member")
        seen.add(key)
        # Include implicit parents: A/file and a/other collide on Windows too.
        parts = name.split("/")
        for length in range(1, len(parts) + 1):
            prefix = "/".join(parts[:length])
            previous = spellings.setdefault(prefix.lower(), prefix)
            if previous != prefix:
                raise ValueError("case-colliding ZIP directory")
        mode = entry.external_attr >> 16
        kind = stat.S_IFMT(mode)
        directory = entry.is_dir()
        if entry.create_system == 3:
            if kind != (stat.S_IFDIR if directory else stat.S_IFREG):
                raise ValueError("ZIP links and special files are forbidden")
        elif entry.create_system == 0:
            attributes = entry.external_attr & 0xFFFF
            if attributes & (0x08 | 0x40 | 0x400) or bool(attributes & 0x10) != directory:
                raise ValueError("unsupported DOS ZIP attributes")
        else:
            raise ValueError("unsupported ZIP metadata origin")
        if entry.flag_bits & 1 or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ValueError("encrypted or unsupported ZIP compression")
        if directory and entry.file_size:
            raise ValueError("ZIP directory has file content")
        expanded += entry.file_size
        if expanded > MAX_EXPANDED:
            raise ValueError("examples ZIP expansion limit exceeded")
        (directories if directory else files).add(key)
        directories.update("/".join(parts[:length]).lower() for length in range(1, len(parts)))
    if files & directories or not any(entry.filename == root + "/spec.json" for entry in entries):
        raise ValueError("ZIP lacks spec.json or has file/directory collisions")
    return entries


def prepare(source, digest, root, destination):
    source, destination = Path(source), Path(destination)
    if source.stat().st_size > MAX_ARCHIVE:
        raise ValueError("examples ZIP download limit exceeded")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("invalid examples ZIP SHA-256")
    with source.open("rb") as stream:
        actual = hashlib.sha256(stream.read(MAX_ARCHIVE + 1)).hexdigest()
    if "sha256:" + actual != digest:
        raise ValueError("examples archive SHA-256 changed")
    with zipfile.ZipFile(source) as archive:
        entries = inventory(archive, root)
        print(json.dumps([dict(name=entry.filename, size=entry.file_size) for entry in entries]), flush=True)
        # Refuse pre-existing output, including symlinks; never merge trees.
        destination.mkdir(mode=0o700)
        for entry in entries:
            target = destination.joinpath(*entry.filename.removesuffix("/").split("/"))
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as incoming, target.open("xb") as outgoing:
                remaining = entry.file_size
                while remaining:
                    chunk = incoming.read(min(65536, remaining))
                    if not chunk:
                        raise ValueError("truncated ZIP member")
                    outgoing.write(chunk)
                    remaining -= len(chunk)
                if incoming.read(1):
                    raise ValueError("ZIP member exceeded declared size")
            # Preserve only executable permission, never set-id or other flags.
            target.chmod(0o755 if entry.external_attr >> 16 & 0o111 else 0o644)


def main():
    try:
        if len(sys.argv) != 5:
            raise ValueError("expected archive, digest, root and destination")
        prepare(*sys.argv[1:])
    except (OSError, ValueError, RuntimeError, NotImplementedError, zipfile.BadZipFile) as error:
        print("ZIP verification failed: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
