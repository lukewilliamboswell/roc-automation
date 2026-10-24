"""Independent inert member hashing for actual tar.zst platform bundles."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

HELPER = Path(__file__).resolve().parents[1] / "actions/release-compatibility/archive.py"
spec = importlib.util.spec_from_file_location("bundle_archive", HELPER)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
ZSTD = os.environ.get("RA_ZSTD") or shutil.which("zstd")
REAL_BUNDLE = os.environ.get("ROC_REAL_BUNDLE")


def tar_bytes(entries, format=tarfile.USTAR_FORMAT):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=format) as archive:
        for name, content, kind in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = len(content)
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                member.linkname = "../outside"
                member.size = 0
            archive.addfile(member, io.BytesIO(content))
    return output.getvalue()


class BundleMemberTests(unittest.TestCase):
    def test_hashes_exact_regular_member_bytes_sorted_without_extraction(self):
        data = tar_bytes([("targets/arm64mac/libhost.a", b"native archive", tarfile.REGTYPE),
                          ("main.roc", b"platform", tarfile.REGTYPE)])
        found = helper.bundle_members(io.BytesIO(data))
        self.assertEqual(found, {"schema": 1, "members": [
            {"path": "main.roc", "size": 8, "sha256": hashlib.sha256(b"platform").hexdigest()},
            {"path": "targets/arm64mac/libhost.a", "size": 14, "sha256": hashlib.sha256(b"native archive").hexdigest()},
        ]})

    def test_links_specials_pax_and_gnu_extension_headers_are_rejected(self):
        for kind in [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE,
                     tarfile.FIFOTYPE, tarfile.XHDTYPE, tarfile.XGLTYPE,
                     tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK, tarfile.GNUTYPE_SPARSE]:
            with self.subTest(kind=kind):
                data = tar_bytes([("member", b"", kind)])
                with self.assertRaises(ValueError):
                    helper.bundle_members(io.BytesIO(data))
        for format in [tarfile.PAX_FORMAT, tarfile.GNU_FORMAT]:
            with self.subTest(format=format), self.assertRaises(ValueError):
                helper.bundle_members(io.BytesIO(tar_bytes([("a" * 120, b"payload", tarfile.REGTYPE)], format)))

    def test_paths_duplicates_case_and_implicit_parent_collisions_are_rejected(self):
        for names in [["../outside"], ["/absolute"], ["C:/escape"], ["NUL.txt"], ["name."],
                      ["a\\b"], ["dir//file"], ["one", "ONE"], ["same", "same"],
                      ["A/file", "a/other"], ["a", "a/file"], ["a/"]]:
            with self.subTest(names=names), self.assertRaises(ValueError):
                helper.bundle_members(io.BytesIO(tar_bytes([(name, b"x", tarfile.REGTYPE) for name in names])))

    def test_entry_and_expansion_limits_apply_before_payload_read(self):
        data = tar_bytes([("one", b"12", tarfile.REGTYPE), ("two", b"34", tarfile.REGTYPE)])
        with patch.object(helper, "MAX_ENTRIES", 1), self.assertRaises(ValueError):
            helper.bundle_members(io.BytesIO(data))
        member = tarfile.TarInfo("oversized")
        member.size = helper.MAX_EXPANDED + 1
        with self.assertRaisesRegex(ValueError, "expansion limit"):
            helper.bundle_members(io.BytesIO(member.tobuf()))

    def test_truncation_checksum_padding_and_hidden_trailing_inventory_fail(self):
        data = tar_bytes([("main.roc", b"source", tarfile.REGTYPE)])
        bad_checksum = bytearray(data)
        bad_checksum[0] ^= 1
        bad_padding = bytearray(data)
        bad_padding[512 + len(b"source")] = 1
        for payload in [b"", data[:200], data[:514], data[:1024], bytes(bad_checksum), bytes(bad_padding),
                        data + tar_bytes([("hidden", b"x", tarfile.REGTYPE)])]:
            with self.subTest(length=len(payload)), self.assertRaises((ValueError, tarfile.TarError)):
                helper.bundle_members(io.BytesIO(payload))

    def test_directory_records_are_validated_but_not_reported_as_files(self):
        data = tar_bytes([("targets/", b"", tarfile.DIRTYPE),
                          ("targets/host.a", b"native", tarfile.REGTYPE)])
        result = helper.bundle_members(io.BytesIO(data))
        self.assertEqual([item["path"] for item in result["members"]], ["targets/host.a"])
        with self.assertRaises(ValueError):
            helper.bundle_members(io.BytesIO(tar_bytes([("targets/", b"not empty", tarfile.DIRTYPE)])))


@unittest.skipUnless(ZSTD, "requires the controller's reviewed zstd executable")
class BundleCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "bundle.tar.zst"
        self.output = self.root / "members.json"

    def compressed(self, data):
        self.source.write_bytes(subprocess.check_output([ZSTD, "--compress", "--stdout"], input=data))

    def digest(self):
        return "sha256:" + hashlib.sha256(self.source.read_bytes()).hexdigest()

    def invoke(self, digest=None):
        return subprocess.run([sys.executable, str(HELPER), "--bundle", str(self.source), digest or self.digest(), str(self.output)],
                              env={**os.environ, "RA_ZSTD": ZSTD}, capture_output=True, text=True, timeout=15)

    def test_actual_compressed_tar_is_hashed_and_no_archive_member_is_extracted(self):
        self.compressed(tar_bytes([("main.roc", b"platform", tarfile.REGTYPE), ("targets/host.a", b"native", tarfile.REGTYPE)]))
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({item["path"] for item in json.loads(self.output.read_text())["members"]}, {"main.roc", "targets/host.a"})
        self.assertEqual({file.name for file in self.root.iterdir()}, {"bundle.tar.zst", "members.json"})

    def test_compressed_digest_size_and_invalid_zstd_fail_without_inventory(self):
        self.compressed(tar_bytes([("main.roc", b"x", tarfile.REGTYPE)]))
        self.assertNotEqual(self.invoke("sha256:" + "0" * 64).returncode, 0)
        self.assertFalse(self.output.exists())
        with patch.object(helper, "MAX_ARCHIVE", 1), self.assertRaises(ValueError):
            helper.inspect_bundle(self.source, self.digest(), self.output)
        self.source.write_bytes(b"not zstd")
        self.assertNotEqual(self.invoke().returncode, 0)
        self.assertFalse(self.output.exists())

    def test_tar_stream_byte_cap_kills_and_reaps_decoder_before_parsing(self):
        self.compressed(tar_bytes([("main.roc", b"source", tarfile.REGTYPE)]))
        processes = []
        real_popen = subprocess.Popen
        def start(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process
        with patch.object(helper.subprocess, "Popen", start), patch.object(helper, "MAX_EXPANDED", 1024):
            with self.assertRaisesRegex(ValueError, "tar byte limit"):
                helper.inspect_bundle(self.source, self.digest(), self.output)
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())
        self.assertTrue(processes[0].stdout.closed)
        self.assertFalse(self.output.exists())

    def test_existing_inventory_is_never_replaced(self):
        self.compressed(tar_bytes([("main.roc", b"source", tarfile.REGTYPE)]))
        self.output.write_text("original")
        self.assertNotEqual(self.invoke().returncode, 0)
        self.assertEqual(self.output.read_text(), "original")

    @unittest.skipUnless(REAL_BUNDLE, "set ROC_REAL_BUNDLE to inspect an actual Roc-generated bundle")
    def test_real_roc_bundle_members_match_independent_tar_reader(self):
        self.source.write_bytes(Path(REAL_BUNDLE).read_bytes())
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        data = subprocess.check_output([ZSTD, "--decompress", "--stdout", str(self.source)])
        expected = []
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            for member in archive:
                if member.isfile():
                    content = archive.extractfile(member).read()
                    expected.append({"path": member.name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()})
        self.assertEqual(json.loads(self.output.read_text()), {"schema": 1, "members": sorted(expected, key=lambda item: item["path"])})
