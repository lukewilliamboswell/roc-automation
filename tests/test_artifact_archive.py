"""Exercise the shared extractor's inert workflow-artifact profile."""
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

HELPER = Path(__file__).resolve().parents[1] / "actions/release-compatibility/archive.py"
spec = importlib.util.spec_from_file_location("artifact_archive", HELPER)
archive_helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive_helper)


class ArtifactArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.archive = self.root / "artifact.zip"
        self.destination = self.root / "extracted"

    def write(self, names, *, mode=stat.S_IFREG | 0o644):
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for name, content in names:
                entry = zipfile.ZipInfo(name)
                entry.create_system = 3
                entry.external_attr = mode << 16
                entry.compress_type = zipfile.ZIP_DEFLATED
                bundle.writestr(entry, content)

    def digest(self):
        return "sha256:" + hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def invoke(self, artifact=True, digest=None):
        args = ["--artifact", str(self.archive), digest or self.digest(), str(self.destination)] if artifact else [str(self.archive), digest or self.digest(), "suite", str(self.destination)]
        return subprocess.run([sys.executable, str(HELPER), *args], capture_output=True, text=True, timeout=10)

    def test_workflow_upload_with_top_level_trace_and_nested_assets(self):
        self.write([("trace.json", b'{"obligations":["build"]}'), ("assets/product.bin", b"\0native bytes"), ("inventory.json", b"[]")])
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.destination / "assets/product.bin").read_bytes(), b"\0native bytes")
        self.assertEqual({entry["name"] for entry in json.loads(result.stdout)}, {"trace.json", "assets/product.bin", "inventory.json"})

    def test_suite_mode_still_requires_its_single_root_and_spec(self):
        self.write([("trace.json", b"{}")])
        self.assertNotEqual(self.invoke(artifact=False).returncode, 0)
        self.assertFalse(self.destination.exists())
        self.write([("suite/spec.json", b"{}"), ("suite/examples/main.roc", b"app")])
        result = self.invoke(artifact=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_artifact_mode_rejects_unsafe_or_ambiguous_inventory_before_extraction(self):
        for names in [
            [("../escaped", b"x")], [("/absolute", b"x")], [("C:/escape", b"x")],
            [("A/file", b"x"), ("a/other", b"x")], [("file", b"x"), ("FILE", b"x")],
            [("a", b"x"), ("a/file", b"x")], [("same", b"x"), ("same", b"x")],
            [("NUL.txt", b"x")], [("dir/name.", b"x")], [("a\\b", b"x")],
            [("dir//file", b"x")], [("dir/./file", b"x")], [("a:stream", b"x")],
        ]:
            with self.subTest(names=names):
                self.write(names)
                result = self.invoke()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.destination.exists())
                self.assertFalse((self.root.parent / "escaped").exists())

    def test_artifact_mode_rejects_links_and_special_files(self):
        for mode in [stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o600, stat.S_IFCHR | 0o600]:
            with self.subTest(mode=mode):
                self.write([("link", b"../outside")], mode=mode)
                self.assertNotEqual(self.invoke().returncode, 0)
                self.assertFalse(self.destination.exists())

    def test_digest_mismatch_and_malformed_digest_never_create_destination(self):
        self.write([("trace.json", b"{}")])
        for digest in ["sha256:" + "0" * 64, "a" * 64, "sha256:" + "A" * 64]:
            with self.subTest(digest=digest):
                self.assertNotEqual(self.invoke(digest=digest).returncode, 0)
                self.assertFalse(self.destination.exists())

    def test_archive_entry_and_expansion_limits_share_the_suite_validator(self):
        self.write([("one", b"12"), ("two", b"34")])
        for constant, maximum in [("MAX_ARCHIVE", 1), ("MAX_ENTRIES", 1), ("MAX_EXPANDED", 3)]:
            with self.subTest(constant=constant), patch.object(archive_helper, constant, maximum):
                with self.assertRaises(ValueError):
                    archive_helper.prepare(self.archive, self.digest(), None, self.destination)
                self.assertFalse(self.destination.exists())

    def test_bad_member_crc_is_rejected_and_partial_tree_is_removed(self):
        self.write([("first", b"safe first file"), ("second", b"corrupt me")])
        data = bytearray(self.archive.read_bytes())
        # Corrupt the expected CRC in both records, preserving the complete ZIP
        # and matching its new transport digest. The member reader must still fail.
        central = data.index(b"PK\x01\x02", data.index(b"PK\x01\x02") + 1)
        local_offset = struct.unpack_from("<I", data, central + 42)[0]
        for offset in [central + 16, local_offset + 14]:
            struct.pack_into("<I", data, offset, 0)
        self.archive.write_bytes(data)
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CRC", result.stderr)
        self.assertFalse(self.destination.exists())

    def test_preexisting_output_is_never_merged_or_deleted(self):
        self.write([("trace.json", b"{}")])
        self.destination.mkdir()
        keep = self.destination / "keep"
        keep.write_bytes(b"original")
        self.assertNotEqual(self.invoke().returncode, 0)
        self.assertEqual(keep.read_bytes(), b"original")
        self.assertEqual(list(self.destination.iterdir()), [keep])

    def test_dos_origin_workflow_zip_is_allowed_but_reparse_attributes_are_not(self):
        for attributes, expected in [(0x20, 0), (0x400, 1)]:
            with self.subTest(attributes=attributes):
                with zipfile.ZipFile(self.archive, "w") as bundle:
                    entry = zipfile.ZipInfo("trace.json")
                    entry.create_system = 0
                    entry.external_attr = attributes
                    bundle.writestr(entry, b"{}")
                result = self.invoke()
                self.assertEqual(result.returncode, expected, result.stderr)
                if self.destination.exists():
                    import shutil
                    shutil.rmtree(self.destination)

    def test_empty_archive_nuls_encryption_and_compression_are_rejected(self):
        with zipfile.ZipFile(self.archive, "w"):
            pass
        self.assertNotEqual(self.invoke().returncode, 0)
        self.assertFalse(self.destination.exists())
        for mutation in ("nul", "encrypted", "compression"):
            with self.subTest(mutation=mutation):
                self.write([("trace.json", b"{}")])
                data = bytearray(self.archive.read_bytes())
                central = data.index(b"PK\x01\x02")
                if mutation == "nul":
                    data = data.replace(b"trace.json", b"tra\x00e.json")
                elif mutation == "encrypted":
                    for offset in [6, central + 8]:
                        struct.pack_into("<H", data, offset, struct.unpack_from("<H", data, offset)[0] | 1)
                else:
                    for offset in [8, central + 10]:
                        struct.pack_into("<H", data, offset, 99)
                self.archive.write_bytes(data)
                self.assertNotEqual(self.invoke().returncode, 0)
                self.assertFalse(self.destination.exists())
