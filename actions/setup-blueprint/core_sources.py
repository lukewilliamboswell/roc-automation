#!/usr/bin/env python3
"""Admit only the reviewed Blueprint 0.12 Core blob; never resolve source inputs.

Raw and tar SHA256 plus the full inventory bind this to the blob whose official
Roc BLAKE3 unbundle was witnessed in Graph issue32 comment6736148215.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time

VERSION = "0.12.0"
CONTENT_HASH = "3mh7nRh7hHcVamEwYFCtd7aMUEoP3cdpAk9gLsZMV17m"
ASSET_ID = 725946981
ASSET_URL = "https://api.github.com/repos/lukewilliamboswell/roc-blueprint/releases/assets/725946981"
RAW_BYTES = 132723
RAW_SHA256 = "b367d5d91a32c002d09d5309a28ae82c0e978a6cd59ed4eb249e395700a01910"
TAR_BYTES = 531968
TAR_SHA256 = "48c76e46db21fe1562fc07e4fc16e457f18f9111dc982dca3bc18bbee9ee7cb9"
MEMBERS = (
    ('main.roc', 313, '017d20c102592c9c502aa46cf3e6e78eb86249cb9f44757eea5f80e2190190c6'),
    ('Activation.roc', 6080, '8c7b68df85f5e0607db71ef653e02efd351e6a6a194e1d96e40fa8e4e53ee117'),
    ('BuildModel.roc', 13239, '03e540330810e54e05f879b8d1af647209983a3b4e2dd2ef57f20e089272bf41'),
    ('CycloneDx.roc', 19960, '334b7ef4d3881142ed1e4c5edd7c4edd3588a0c6a7fae7dc07c8d0023d671ce4'),
    ('JsonData.roc', 17611, 'a1a4cacbb774184ba3573b5fb03519094ede649c0c3704cbbc24d81f269dd656'),
    ('Layout.roc', 2510, '2788742ec5aa05b875c1a717a0362fc03fee4be56a7acc5bc8e4f33cba49cdf3'),
    ('Lock.roc', 31027, '6e6b2f0f93dfb8e88b1f73eaaf442748b980cedd63f008196c03b107ae7cd8b1'),
    ('LockSnapshot.roc', 18687, '08e948136d70d70d2c0ea61378ff4cbbd6300d7dea84b08a414d1e06b05d61d6'),
    ('Lookup.roc', 2391, '876a2cc466ab609ead5849cd65ad3c18f741ab5b643d3be597be5e2dde6749ed'),
    ('Manifest.roc', 9690, '6ab1e0189ce7962c9a0216b29154a33de9af258170edc53eddd035fe2ad962d2'),
    ('Project.roc', 94022, '363b876e8be8d8da43574ae89af53c63940a36ce8d2847212a1cf7165f760a5f'),
    ('Provider.roc', 5774, '4e6af75588f5854c2373a6693ccb5929bd90040068263d4df45475c751578c6c'),
    ('Realise.roc', 60253, 'ae47de85c20ad12366420e99eb5c40b74565607ab11512f4c56463f6a5df8c7d'),
    ('Request.roc', 175, '64c38f6c49e3f993b88c3a736365005624584dc1d2692486b11a21799d7fdb91'),
    ('Sbom.roc', 111185, '13c2f51b8703324e61b38cb8b7aaba205cb54d9d223e1026011936f0728fb123'),
    ('SbomLicenses.roc', 14878, 'ccb1aadbd6d955564c400a0c0ef94e8fc8f6e65781eb172f4cd1182afd6a3975'),
    ('Sexpr.roc', 14985, '73946181e9e2f2e500d359796476d37805a32cebdd4dea99989e61e052cc6515'),
    ('Sidecar.roc', 6882, '5cd13d8a3553005472dda9a001e48a92d0b53a51c7e8fc19140ef12d28b26e44'),
    ('Spdx2.roc', 21038, '0a991a66ef13de35c1487fc9dda9443d4b2e9e788539350fa62c2b6f307161d0'),
    ('Spdx3.roc', 23586, 'a19d011f3f710c75f1050dbbaac203f257fb2f7cc12caa46cee0ce3bf893c3cf'),
    ('Spec.roc', 17235, '4e09cce6645824d4aaee778515c71fcfd9f78ab838d4952018fac4db0c373373'),
    ('Steps.roc', 5449, 'b62dbe60212d6f4edcac103e6b03d441696e811f85167bb41472df1f4e44adec'),
    ('Tree.roc', 3715, 'b56e382a481480325f537e1ff6c086379cd063a986d5be1a368c709bd58536ad'),
    ('Value.roc', 6169, 'a5bb19f95c3ee2af7e598e9b831cce3960de6083ee4d1c21d556087d4ac0df02'),
    ('lock-snapshot-v2.schema.json', 5004, '11c7f5497d0d2cf55a6cfd66288d969ecf61603e18f94d864c16b89ca9587246'),
)

# Data readers receive host paths/settings only, never caller GitHub/OIDC tokens,
# loader injection variables, Python paths, shell startup files or arbitrary env.
_CHILD_KEYS = {
    "PATH", "HOME", "XDG_CACHE_HOME", "ROC_CACHE_DIR", "USERPROFILE", "LOCALAPPDATA",
    "SYSTEMROOT", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR",
    "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
}
_DARWIN_ALIASES = ((Path("/var"), Path("/private/var")), (Path("/tmp"), Path("/private/tmp")))
_STDLIB_PROBE = """import sys
try:
    from compression import zstd
except ImportError:
    sys.stdout.buffer.write(b'unavailable\\n')
else:
    sys.stdout.buffer.write(b'available\\n')
"""
_STDLIB_DECODE = """from compression import zstd
from pathlib import Path
import sys
decoder = zstd.ZstdDecompressor(options={zstd.DecompressionParameter.window_log_max: 26})
limit = int(sys.argv[2])
data = decoder.decompress(Path(sys.argv[1]).read_bytes(), max_length=limit + 1)
if len(data) != limit or not decoder.eof or decoder.unused_data:
    raise SystemExit('invalid bounded Core zstd stream')
sys.stdout.buffer.write(data)
"""


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def child_environment(environment):
    return {name: value for name, value in environment.items() if name in _CHILD_KEYS}


def _stop(process):
    if os.name == "nt":
        # The job was already closed, killing descendants even if their parent
        # exited. This also covers a still-suspended job-assignment failure.
        if process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired as error:
        raise ValueError("Core child cleanup deadline exceeded") from error


def _start_windows(command, environment, cwd):
    """Assign a suspended process to a kill-on-close job before it can run.

    This is the same narrow primitive exercised by the Windows Core decoder
    witness in Graph run42595070331; no child can escape through a startup race.
    """
    from ctypes import wintypes as W

    class BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", W.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", W.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", W.DWORD), ("SchedulingClass", W.DWORD)]

    class IOCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in
                    ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount",
                     "WriteTransferCount", "OtherTransferCount")]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IOCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class ThreadEntry(ctypes.Structure):
        _fields_ = [("dwSize", W.DWORD), ("cntUsage", W.DWORD), ("th32ThreadID", W.DWORD),
                    ("th32OwnerProcessID", W.DWORD), ("tpBasePri", W.LONG), ("tpDeltaPri", W.LONG), ("dwFlags", W.DWORD)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([W.LPVOID, W.LPCWSTR], W.HANDLE),
        "SetInformationJobObject": ([W.HANDLE, ctypes.c_int, W.LPVOID, W.DWORD], W.BOOL),
        "AssignProcessToJobObject": ([W.HANDLE, W.HANDLE], W.BOOL),
        "CloseHandle": ([W.HANDLE], W.BOOL),
        "CreateToolhelp32Snapshot": ([W.DWORD, W.DWORD], W.HANDLE),
        "Thread32First": ([W.HANDLE, ctypes.POINTER(ThreadEntry)], W.BOOL),
        "Thread32Next": ([W.HANDLE, ctypes.POINTER(ThreadEntry)], W.BOOL),
        "OpenThread": ([W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
        "ResumeThread": ([W.HANDLE], W.DWORD),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = arguments, result
    job = kernel.CreateJobObjectW(None, None)
    require(bool(job), "creating Core Windows process job failed")
    process = None
    try:
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        require(kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)),
                "setting Core Windows process job limits failed")
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, cwd=cwd, env=environment,
                                   creationflags=4 | subprocess.CREATE_NEW_PROCESS_GROUP)  # CREATE_SUSPENDED
        require(kernel.AssignProcessToJobObject(job, int(process._handle)), "assigning Core suspended process failed")
        snapshot = kernel.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD
        require(snapshot not in (None, ctypes.c_void_p(-1).value), "Core thread snapshot failed")
        identifiers = []
        try:
            entry = ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            more = kernel.Thread32First(snapshot, ctypes.byref(entry))
            while more:
                if entry.th32OwnerProcessID == process.pid:
                    identifiers.append(entry.th32ThreadID)
                more = kernel.Thread32Next(snapshot, ctypes.byref(entry))
        finally:
            kernel.CloseHandle(snapshot)
        require(len(identifiers) == 1, "expected one Core suspended initial thread")
        thread = kernel.OpenThread(2, False, identifiers[0])  # THREAD_SUSPEND_RESUME
        require(bool(thread), "opening Core suspended thread failed")
        try:
            require(kernel.ResumeThread(thread) == 1, "resuming Core suspended thread failed")
        finally:
            kernel.CloseHandle(thread)
        return process, kernel, job
    except BaseException:
        kernel.CloseHandle(job)
        if process is not None:
            _stop(process)
            process.stdout.close()
            process.stderr.close()
        raise


def run_bounded(command, maximum, seconds, environment, label, cwd=None):
    """Use bounded pipe readers on Unix and Windows; keep diagnostics public."""
    environment = child_environment(environment)
    deadline = time.monotonic() + seconds
    job = None
    if os.name == "nt":
        process, kernel, job = _start_windows(command, environment, cwd)
    else:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, cwd=cwd, env=environment, start_new_session=True)
    buffers = [bytearray(), bytearray()]
    failed = threading.Event()
    finished = [threading.Event(), threading.Event()]

    def read(index, stream, limit):
        try:
            while True:
                chunk = stream.read1(min(65536, limit - len(buffers[index]) + 1))
                if not chunk:
                    break
                buffers[index].extend(chunk)
                if len(buffers[index]) > limit:
                    failed.set()
                    break
        except (OSError, ValueError):
            failed.set()
        finally:
            finished[index].set()

    readers = [threading.Thread(target=read, args=(0, process.stdout, maximum), daemon=True),
               threading.Thread(target=read, args=(1, process.stderr, 4096), daemon=True)]
    try:
        for reader in readers:
            reader.start()
        while not (process.poll() is not None and all(event.is_set() for event in finished)):
            require(not failed.is_set(), label + " output bound exceeded")
            require(time.monotonic() < deadline, label + " deadline exceeded")
            time.sleep(0.01)
        require(not failed.is_set(), label + " output bound exceeded")
        if process.returncode:
            # Never reflect arbitrary stderr or environment/path values.
            status = re.search(rb"requested URL returned error: ([0-9]{3})", buffers[1])
            detail = "; HTTP " + status[1].decode() if status else ""
            raise ValueError(f"{label} failed (exit {process.returncode}{detail})")
        return bytes(buffers[0])
    finally:
        if job is not None:
            kernel.CloseHandle(job)  # Kills descendants even after the initial process exited.
        _stop(process)
        for reader in readers:
            if reader.ident is not None:
                reader.join(timeout=2)
        cleaned = all(not reader.is_alive() for reader in readers)
        if cleaned:
            process.stdout.close()
            process.stderr.close()
        require(cleaned, "Core child pipe cleanup failed")


def _link(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _owned(info):
    # Unix UID ownership is checked. Windows relies on a private caller cache,
    # native no-reparse/nlink checks and full-byte validation; no SID claim.
    return not hasattr(os, "geteuid") or info.st_uid == os.geteuid()


def physical_path(path):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts
            and not any(ord(char) < 32 or ord(char) == 127 for char in str(path)),
            "Core cache path must be absolute without control characters")
    if sys.platform == "darwin":
        for alias, target in _DARWIN_ALIASES:
            if (path == alias or alias in path.parents) and alias.is_symlink():
                actual = Path(os.readlink(alias))
                if not actual.is_absolute():
                    actual = alias.parent / actual
                require(actual == target, "unexpected macOS system alias")
                return target / path.relative_to(alias)
    return path


def safe_directory(path):
    path = physical_path(path)
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = current.lstat()
        require(stat.S_ISDIR(info.st_mode) and not _link(info), "unsafe Core cache directory")
    return path


def safe_owned_directory(path):
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and not _link(info) and _owned(info),
            "Core cache directory must be permitted regular data")
    if os.name != "nt":
        mode = stat.S_IMODE(info.st_mode)
        require(mode & 0o700 == 0o700 and mode & 0o7022 == 0,
                "Core cache directory has unsafe permissions")


def cache_home(environment, *, windows=None):
    if "XDG_CACHE_HOME" in environment:
        require(bool(environment["XDG_CACHE_HOME"]), "Core XDG_CACHE_HOME override must not be empty")
        return environment["XDG_CACHE_HOME"]
    if (os.name == "nt" if windows is None else windows) and "LOCALAPPDATA" in environment:
        require(bool(environment["LOCALAPPDATA"]), "Core LOCALAPPDATA override must not be empty")
        return environment["LOCALAPPDATA"]
    require(bool(environment.get("HOME")), "Core admission needs HOME or XDG_CACHE_HOME")
    return str(Path(environment["HOME"]) / ".cache")


def packages_directory(environment):
    selected = cache_home(environment)
    cache = safe_directory(selected)
    roc = safe_directory(cache / "roc")
    packages = safe_directory(roc / "packages")
    for directory in (cache, roc, packages):
        safe_owned_directory(directory)
    return packages


def regular_bytes(path, expected_size):
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and not _link(before)
            and before.st_nlink == 1 and _owned(before), "Core data must be one permitted regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as source:
        current = os.fstat(source.fileno())
        require((current.st_dev, current.st_ino) == (before.st_dev, before.st_ino)
                and current.st_size == expected_size and stat.S_ISREG(current.st_mode)
                and current.st_nlink == 1 and _owned(current), "Core file changed or has the wrong size")
        data = source.read(expected_size + 1)
    require(len(data) == expected_size, "Core file size mismatch")
    return data, current


def verify_tree(directory):
    safe_owned_directory(directory)
    expected = {name: (size, digest) for name, size, digest in MEMBERS}
    seen = set()
    with os.scandir(directory) as entries:
        for entry in entries:
            require(entry.name in expected and entry.name not in seen and len(seen) < len(expected),
                    "unexpected Core tree entry")
            size, digest = expected[entry.name]
            data, info = regular_bytes(directory / entry.name, size)
            if os.name != "nt":
                require(stat.S_IMODE(info.st_mode) == 0o600, "Core file mode mismatch")
            # Windows POSIX mode bits do not prove ACLs. Use the caller's
            # private cache, reject reparse points, and verify every byte.
            require(sha256(data) == digest, "Core file digest mismatch")
            seen.add(entry.name)
    require(seen == set(expected), "incomplete Core tree")


def checked_sidecar(path):
    if not os.path.lexists(path):
        return None
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and not _link(info)
            and info.st_nlink == 1 and _owned(info), "unsafe Core dependency sidecar")
    return info


def invalidate_sidecar(path, original):
    if original is None:
        require(not os.path.lexists(path), "Core dependency sidecar raced with admission")
        return False
    current = checked_sidecar(path)
    require(current is not None and (current.st_dev, current.st_ino) == (original.st_dev, original.st_ino),
            "Core dependency sidecar changed during admission")
    path.unlink()
    require(not os.path.lexists(path), "Core dependency sidecar raced with admission")
    return True


def atomic_insert(source, destination):
    if os.name == "nt":
        # The managed Windows witness exercised this exact primitive: it
        # refuses existing files and directories, including a racing slot.
        library = ctypes.WinDLL("kernel32", use_last_error=True)
        function = library.MoveFileW
        function.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        function.restype = ctypes.c_int
        require(bool(function(str(source), str(destination))), "Core atomic publication failed")
        return
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux") and hasattr(library, "renameat2"):
        function = library.renameat2
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        result = function(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    elif sys.platform == "darwin" and hasattr(library, "renamex_np"):
        function = library.renamex_np
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        result = function(os.fsencode(source), os.fsencode(destination), 4)
    else:
        raise ValueError("Core admission needs a no-replace atomic rename")
    require(result == 0, "Core atomic publication failed")


def verify_raw(data):
    require(len(data) == RAW_BYTES and sha256(data) == RAW_SHA256, "Core archive size or SHA256 mismatch")


def inspect_tar(data):
    require(len(data) == TAR_BYTES and sha256(data) == TAR_SHA256, "Core tar size or SHA256 mismatch")
    expected = {name: (size, digest) for name, size, digest in MEMBERS}
    payloads = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
            for member in archive:
                name = member.name
                require(name in expected and name not in payloads and "/" not in name and "\\" not in name,
                        "unexpected or duplicate Core archive path")
                require(member.isreg() and member.type == tarfile.REGTYPE and member.mode == 0o644
                        and not member.linkname and not member.pax_headers,
                        "Core archive member must be plain regular0644 data")
                size, digest = expected[name]
                require(member.size == size, "Core member size mismatch")
                with archive.extractfile(member) as source:
                    body = source.read(size + 1)
                require(len(body) == size and sha256(body) == digest, "Core member digest mismatch")
                payloads[name] = body
    except (tarfile.TarError, OSError) as error:
        raise ValueError("invalid Core archive") from error
    require(set(payloads) == set(expected), "missing Core archive member")
    return payloads


def fetch_raw(environment):
    data = run_bounded(["curl", "--disable", "--no-netrc", "--fail", "--silent", "--show-error",
                        "--location", "--proto", "=https", "--proto-redir", "=https", "--max-redirs", "3",
                        "--connect-timeout", "10", "--max-time", "60", "--max-filesize", str(RAW_BYTES),
                        "--header", "Accept: application/octet-stream", ASSET_URL],
                       RAW_BYTES, 60, environment, "Core API download")
    verify_raw(data)
    return data


def compiler_identity(compiler, environment):
    compiler = Path(compiler)
    require(compiler.is_absolute() and not any(ord(char) < 32 or ord(char) == 127 for char in str(compiler)),
            "explicit Core compiler must be an absolute path")
    info = compiler.lstat()
    require(stat.S_ISREG(info.st_mode) and not _link(info), "explicit Core compiler must be a regular executable")
    require(os.access(compiler, os.X_OK), "explicit Core compiler is not executable")
    digest = hashlib.sha256()
    with compiler.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    version = run_bounded([str(compiler), "version"], 4096, 30, environment, "Core compiler identity").decode().strip()
    require(bool(version), "Core compiler version is empty")
    return {"path": str(compiler), "sha256": digest.hexdigest(), "version": version}


def receipt(directory, method, reused, invalidated, compiler=None, decoder=None):
    return {"source": {"type": "known-blueprint-core", "version": VERSION, "asset_id": ASSET_ID,
                       "content_hash": CONTENT_HASH, "raw_bytes": RAW_BYTES, "raw_sha256": RAW_SHA256},
            "method": method, "inventory": {"files": len(MEMBERS), "content_bytes": sum(size for _, size, _ in MEMBERS),
                                            "tar_bytes": TAR_BYTES, "known_tar_sha256": TAR_SHA256},
            "tar_digest_recomputed": method == "data-zstd",
            "decoder": decoder,
            "mode_policy": "windows-regular-no-reparse" if os.name == "nt" else "posix-private-regular",
            "ownership_policy": "unix-euid" if hasattr(os, "geteuid") else "caller-private-cache-no-sid-verification",
            "cache": {"directory": str(directory), "reused": reused},
            "sidecar": {"invalidated": invalidated, "created": False}, "compiler": compiler}


def decode_data(archive, environment):
    # Python's optional stdlib backend is available starting with 3.14. Probe
    # under -I so checkout modules and Python environment hooks cannot supply it.
    if sys.version_info >= (3, 14):
        available = run_bounded([sys.executable, "-I", "-c", _STDLIB_PROBE], 32, 30,
                                environment, "Core stdlib zstd availability")
        require(available in (b"available\n", b"unavailable\n"), "unexpected Core decoder availability")
        if available == b"available\n":
            return run_bounded([sys.executable, "-I", "-c", _STDLIB_DECODE, str(archive), str(TAR_BYTES)],
                               TAR_BYTES, 30, environment, "Core data decode"), "python-stdlib-zstd"
    decoder = shutil.which("zstd", path=environment.get("PATH"))
    require(bool(decoder), "Core data admission needs Python 3.14 compression.zstd or trusted host zstd")
    return run_bounded([decoder, "--decompress", "--stdout", "--memory=64MB", str(archive)],
                       TAR_BYTES, 30, environment, "Core data decode"), "host-zstd"


def admit(version, environment=None, *, compiler=None):
    if version != VERSION:
        return None
    if compiler is not None:
        require(Path(compiler).is_absolute() and not any(ord(char) < 32 or ord(char) == 127 for char in str(compiler)),
                "explicit Core compiler must be an absolute path")
    environment = dict(os.environ if environment is None else environment)
    packages = packages_directory(environment)
    destination = packages / CONTENT_HASH
    sidecar = packages / (CONTENT_HASH + ".deps")
    metadata = checked_sidecar(sidecar)
    if os.path.lexists(destination):
        verify_tree(destination)
        invalidated = invalidate_sidecar(sidecar, metadata)
        return receipt(destination, "verified-existing-tree", True, invalidated)
    require(metadata is None, "orphan Core dependency sidecar")
    identity = decoder = None
    with tempfile.TemporaryDirectory(prefix=".core-admission-", dir=packages) as temporary:
        stage = Path(temporary)
        raw = fetch_raw(environment)
        verify_raw(raw)
        archive = stage / (CONTENT_HASH + ".tar.zst")
        with archive.open("xb") as output:
            archive.chmod(0o600)
            output.write(raw)
        tree = stage / CONTENT_HASH
        if compiler is None:
            tar, decoder = decode_data(archive, environment)
            payloads = inspect_tar(tar)
            tree.mkdir(mode=0o700)
            for name, body in payloads.items():
                with (tree / name).open("xb") as output:
                    (tree / name).chmod(0o600)
                    output.write(body)
            method = "data-zstd"
        else:
            identity = compiler_identity(compiler, environment)
            run_bounded([identity["path"], "unbundle", archive.name], 8192, 60,
                        environment, "trusted Core unbundle", cwd=stage)
            require(stat.S_ISDIR(tree.lstat().st_mode) and not _link(tree.lstat()),
                    "trusted Core unbundle did not create the expected directory")
            if os.name != "nt":
                tree.chmod(0o700)
            method = "trusted-roc-unbundle"
        require({entry.name for entry in stage.iterdir()} == {archive.name, CONTENT_HASH},
                "unexpected Core extraction output")
        verify_tree(tree)
        require(not os.path.lexists(destination) and not os.path.lexists(sidecar),
                "Core cache slot or sidecar raced with admission")
        atomic_insert(tree, destination)
        verify_tree(destination)
        require(not os.path.lexists(sidecar), "Core dependency sidecar raced with publication")
    return receipt(destination, method, False, False, identity, decoder)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, choices=[VERSION])
    parser.add_argument("--compiler", type=Path, help="explicit trusted compiler, for read-only managed hooks")
    options = parser.parse_args(argv)
    print(json.dumps(admit(options.version, compiler=options.compiler), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except ValueError as error:
        print(f"Core admission failed: {error}", file=sys.stderr)
        sys.exit(1)
    except (OSError, UnicodeError):
        print("Core admission failed: unavailable or unsafe local data", file=sys.stderr)
        sys.exit(1)
