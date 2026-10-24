"""Admit inert archives, extract ZIPs and hash exact tar.zst bundle members.

ZIP operations need only the standard library. Bundle inventory uses the
controller's reviewed zstd for bounded decompression, then Python tar headers.
Nothing in an archive is executed; bundles are never extracted. The entire
ZIP inventory is validated before the destination directory is created.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import subprocess
import tarfile
import tempfile
import zipfile

MAX_ARCHIVE = 50_000_000
MAX_EXPANDED = 100_000_000
MAX_ENTRIES = 10_000
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def canonical(name):
    if not name or name.startswith("/") or len(name) > 4096:
        raise ValueError("unsafe archive path")
    for part in name.split("/"):
        if (not re.fullmatch(r"[A-Za-z0-9_.-]{1,255}", part)
                or part in (".", "..") or part.endswith(".")
                or part.split(".", 1)[0].upper() in RESERVED):
            raise ValueError("unsafe archive path or reserved Windows filename")
    return name


class Inventory:
    """Shared cross-platform path, collision and resource admission."""
    def __init__(self):
        self.seen, self.spellings, self.files, self.directories = set(), {}, set(), set()
        self.expanded = 0
        self.entries = 0

    def add(self, name, directory, size):
        self.entries += 1
        if self.entries > MAX_ENTRIES:
            raise ValueError("archive entry limit exceeded")
        name = canonical(name)
        key = name.lower()
        if key in self.seen:
            raise ValueError("duplicate or case-colliding archive member")
        self.seen.add(key)
        parts = name.split("/")
        for length in range(1, len(parts) + 1):
            prefix = "/".join(parts[:length])
            previous = self.spellings.setdefault(prefix.lower(), prefix)
            if previous != prefix:
                raise ValueError("case-colliding archive directory")
        if size < 0 or (directory and size):
            raise ValueError("archive directory has file content or negative size")
        self.expanded += size
        if self.expanded > MAX_EXPANDED:
            raise ValueError("archive expansion limit exceeded")
        (self.directories if directory else self.files).add(key)
        self.directories.update("/".join(parts[:length]).lower() for length in range(1, len(parts)))
        return name

    def finish(self):
        if not self.entries or self.files & self.directories:
            raise ValueError("empty archive or file/directory collisions")


def inventory(archive, root=None):
    """One inventory validator; only suite mode requires a root and spec.json."""
    if root is not None:
        canonical(root)
        if "/" in root:
            raise ValueError("invalid ZIP root")
    entries = archive.infolist()
    if not entries or len(entries) > MAX_ENTRIES:
        raise ValueError("examples ZIP entry limit exceeded")
    checked = Inventory()
    for entry in entries:
        # orig_filename retains NUL bytes which ZipInfo.filename truncates.
        if entry.orig_filename != entry.filename:
            raise ValueError("ZIP member contains a NUL")
        name = canonical(entry.filename.removesuffix("/"))
        if root is not None and name != root and not name.startswith(root + "/"):
            raise ValueError("ZIP member escapes the declared root")
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
        checked.add(name, directory, entry.file_size)
    checked.finish()
    if root is not None and not any(entry.filename == root + "/spec.json" for entry in entries):
        raise ValueError("ZIP lacks spec.json")
    return entries


def checked_source(source, digest):
    if source.is_symlink() or not stat.S_ISREG(source.stat().st_mode):
        raise ValueError("archive input must be a regular file")
    if source.stat().st_size > MAX_ARCHIVE:
        raise ValueError("archive download limit exceeded")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("invalid archive SHA-256")
    with source.open("rb") as stream:
        actual = hashlib.sha256(stream.read(MAX_ARCHIVE + 1)).hexdigest()
    if "sha256:" + actual != digest:
        raise ValueError("archive SHA-256 changed")


def prepare(source, digest, root, destination):
    source, destination = Path(source), Path(destination)
    checked_source(source, digest)
    with zipfile.ZipFile(source) as archive:
        entries = inventory(archive, root)
        print(json.dumps([dict(name=entry.filename, size=entry.file_size) for entry in entries]), flush=True)
        # Refuse pre-existing output, including symlinks; never merge trees.
        destination.mkdir(mode=0o700)
        try:
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
        except BaseException:
            shutil.rmtree(destination)
            raise


def bundle_members(stream):
    """Read bounded tar records with tarfile's header parser, never extract.

    Reject extension headers before their payload can alter a later member's
    interpretation. This also prevents PAX/GNU recursion or hidden inventories.
    """
    checked = Inventory()
    members = []
    while True:
        header = stream.read(tarfile.BLOCKSIZE)
        if len(header) != tarfile.BLOCKSIZE:
            raise ValueError("truncated bundle tar header or missing terminator")
        if header == bytes(tarfile.BLOCKSIZE):
            if stream.read(tarfile.BLOCKSIZE) != bytes(tarfile.BLOCKSIZE):
                raise ValueError("bundle tar requires two zero terminator blocks")
            while chunk := stream.read(65536):
                if any(chunk):
                    raise ValueError("nonzero data after bundle tar terminator")
            break
        member = tarfile.TarInfo.frombuf(header, "utf-8", "strict")
        if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE):
            raise ValueError("bundle links, special files and extension headers are forbidden")
        # Tar names are fixed NUL-padded fields, not NUL-truncated aliases.
        for field in (header[:100], header[345:500]):
            if b"\0" in field and any(field.split(b"\0", 1)[1]):
                raise ValueError("bundle tar name contains hidden bytes after NUL")
        directory = member.isdir()
        if not directory and member.name.endswith("/"):
            raise ValueError("regular bundle member has a directory name")
        name = checked.add(member.name.removesuffix("/"), directory, member.size)
        digest = hashlib.sha256()
        remaining = member.size
        while remaining:
            chunk = stream.read(min(65536, remaining))
            if not chunk:
                raise ValueError("truncated bundle member")
            digest.update(chunk)
            remaining -= len(chunk)
        padding = (-member.size) % tarfile.BLOCKSIZE
        if stream.read(padding) != bytes(padding):
            raise ValueError("truncated or nonzero bundle member padding")
        if not directory:
            members.append(dict(path=name, size=member.size, sha256=digest.hexdigest()))
    checked.finish()
    return dict(schema=1, members=sorted(members, key=lambda item: item["path"]))


def inspect_bundle(source, digest, output):
    source, output = Path(source), Path(output)
    checked_source(source, digest)
    # The controller supplies its own reviewed zstd through its pinned tooling
    # environment. Candidate Blueprint files and executables play no part.
    with tempfile.TemporaryFile() as expanded:
        child = subprocess.Popen([os.environ.get("RA_ZSTD", "zstd"), "--decompress", "--stdout", "--memory=100MB", "--", str(source)],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            length = 0
            while chunk := child.stdout.read(65536):
                length += len(chunk)
                if length > MAX_EXPANDED:
                    raise ValueError("bundle tar byte limit exceeded")
                expanded.write(chunk)
            if child.wait(timeout=30) != 0:
                raise ValueError("zstd rejected the bundle")
        except BaseException:
            child.kill()
            child.wait()
            raise
        finally:
            child.stdout.close()
        expanded.seek(0)
        result = bundle_members(expanded)
    # No inventory is published until every member has been checked and hashed.
    with output.open("x", encoding="utf-8") as target:
        json.dump(result, target, separators=(",", ":"))
        target.write("\n")


def main():
    try:
        if len(sys.argv) != 5:
            raise ValueError("expected ZIP digest root destination; --artifact ZIP digest destination; or --bundle FILE digest output.json")
        if sys.argv[1] == "--bundle":
            inspect_bundle(sys.argv[2], sys.argv[3], sys.argv[4])
        elif sys.argv[1] == "--artifact":
            prepare(sys.argv[2], sys.argv[3], None, sys.argv[4])
        else:
            prepare(*sys.argv[1:])
    except (OSError, ValueError, RuntimeError, NotImplementedError, zipfile.BadZipFile, tarfile.TarError, UnicodeError, subprocess.SubprocessError) as error:
        print("archive verification failed: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
