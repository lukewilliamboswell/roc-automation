"""Cross-platform ZIP security checks, including Windows name semantics."""
import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("compatibility_archive", Path(os.environ.get("ROC_COMPATIBILITY_ARCHIVE_HELPER", ROOT / "actions/release-compatibility/archive.py")))
archive_helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive_helper)


class ArchiveAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.archive = self.root / "examples.zip"
        self.output = self.root / "unpacked"

    def make_archive(self, additional=(), dos=False):
        with zipfile.ZipFile(self.archive, "w") as archive:
            for name, content, mode in [("root/spec.json", b"{}", stat.S_IFREG | 0o644), *additional]:
                entry = zipfile.ZipInfo(name.replace("\0", "!"))
                entry.create_system = 0 if dos else 3
                entry.external_attr = 0x20 if dos else mode << 16
                archive.writestr(entry, content)
        if any("\0" in name for name, _, _ in additional):
            self.archive.write_bytes(self.archive.read_bytes().replace(b"!", b"\0"))

    def prepare(self):
        digest = "sha256:" + hashlib.sha256(self.archive.read_bytes()).hexdigest()
        archive_helper.prepare(self.archive, digest, "root", self.output)

    def test_unix_and_windows_created_regular_archives_are_portable(self):
        for dos in (False, True):
            with self.subTest(dos=dos):
                self.make_archive(dos=dos)
                self.prepare()
                self.assertEqual((self.output / "root/spec.json").read_bytes(), b"{}")
                import shutil
                shutil.rmtree(self.output)

    def test_windows_aliases_traversal_and_reserved_names_are_rejected_before_extraction(self):
        names = ["root/CON", "root/nul.txt", "root/COM1.log", "root/lpt9",
                 "root/a.", "root/a ", "root/C:drive", "root/one\\two", "root/../escape",
                 "/root/absolute", "root/new\nline", "root/name\0hidden", "root//empty"]
        for name in names:
            with self.subTest(name=name):
                self.make_archive([(name, b"bad", stat.S_IFREG | 0o644)])
                with self.assertRaises(ValueError):
                    self.prepare()
                self.assertFalse(self.output.exists())

    def test_case_collisions_include_implicit_parent_directories(self):
        for names in [("root/SPEC.json",), ("root/A/one", "root/a/two"),
                      ("root/file", "root/FILE/child"), ("root/a", "root/a/child")]:
            with self.subTest(names=names):
                self.make_archive([(name, b"bad", stat.S_IFREG | 0o644) for name in names])
                with self.assertRaises(ValueError):
                    self.prepare()
                self.assertFalse(self.output.exists())

    def test_links_and_special_files_are_never_extracted(self):
        for kind in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR, stat.S_IFSOCK):
            with self.subTest(kind=kind):
                self.make_archive([("root/extra", b"target", kind | 0o777)])
                with self.assertRaises(ValueError):
                    self.prepare()
                self.assertFalse(self.output.exists())

    def test_digest_and_preexisting_destinations_fail_closed(self):
        self.make_archive()
        with self.assertRaises(ValueError):
            archive_helper.prepare(self.archive, "sha256:" + "0" * 64, "root", self.output)
        self.assertFalse(self.output.exists())
        self.output.mkdir()
        sentinel = self.output / "sentinel"
        sentinel.write_text("keep")
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual(sentinel.read_text(), "keep")

    def test_expansion_and_entry_limits_are_checked_before_extraction(self):
        from unittest.mock import patch
        self.make_archive()
        for setting in ("MAX_EXPANDED", "MAX_ENTRIES"):
            with self.subTest(setting=setting), patch.object(archive_helper, setting, 0):
                with self.assertRaises(ValueError):
                    self.prepare()
                self.assertFalse(self.output.exists())

    def test_encrypted_and_unknown_metadata_are_rejected_before_extraction(self):
        import struct
        self.make_archive()
        data = bytearray(self.archive.read_bytes())
        for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            position = data.index(signature) + offset
            struct.pack_into("<H", data, position, struct.unpack_from("<H", data, position)[0] | 1)
        self.archive.write_bytes(data)
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.output.exists())
        self.make_archive()
        data = bytearray(self.archive.read_bytes())
        data[data.index(b"PK\x01\x02") + 5] = 19
        self.archive.write_bytes(data)
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_duplicate_entries_are_rejected_before_extraction(self):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            self.make_archive([("root/spec.json", b"bad", stat.S_IFREG | 0o644)])
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_crc_corruption_never_returns_success(self):
        self.make_archive()
        content = self.archive.read_bytes()
        self.archive.write_bytes(content.replace(b"{}", b"[]", 1))
        with self.assertRaises(zipfile.BadZipFile):
            self.prepare()


if __name__ == "__main__":
    unittest.main()
