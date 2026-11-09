"""Fault tests for the finite Core bootstrap and installer exposure boundary."""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

DIRECTORY = Path(__file__).resolve().parents[1] / "actions" / "setup-blueprint"


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, DIRECTORY / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core = load("blueprint_core_test", "core_sources.py")
setup = load("blueprint_core_installer_test", "setup_blueprint.py")


def tar_bytes(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, body, kind, mode, pax in entries:
            member = tarfile.TarInfo(name)
            member.type, member.mode, member.pax_headers = kind, mode, pax
            member.size = len(body) if kind == tarfile.REGTYPE else 0
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                member.linkname = "main.roc"
            archive.addfile(member, io.BytesIO(body) if member.isreg() else None)
    return output.getvalue()


class CoreFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.environment = {
            "HOME": str(self.root / "home"), "XDG_CACHE_HOME": str(self.root / "cache"),
            "ROC_CACHE_DIR": str(self.root / "ir"), "PATH": os.environ.get("PATH", ""),
            "RA_COMPILER": "/must/not/be/selected", "ROC": "/also/not/selected",
            "GH_TOKEN": "fixture-secret", "GITHUB_TOKEN": "fixture-secret",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "fixture-secret", "OTHER_SECRET": "fixture-secret",
        }
        self.payloads = {"main.roc": b"package [] {}\n", "Core.roc": b"module []\n"}
        self.members = tuple((name, len(data), core.sha256(data)) for name, data in self.payloads.items())
        self.entries = [(name, data, tarfile.REGTYPE, 0o644, {}) for name, data in self.payloads.items()]
        self.tar = tar_bytes(self.entries)
        self.raw = b"known compressed fixture"
        for name, value in (("MEMBERS", self.members), ("RAW_BYTES", len(self.raw)),
                            ("RAW_SHA256", core.sha256(self.raw)), ("TAR_BYTES", len(self.tar)),
                            ("TAR_SHA256", core.sha256(self.tar))):
            self.enterContext(patch.object(core, name, value))
        self.process = self.enterContext(patch.object(core, "run_bounded", side_effect=self.fake_process))
        self.which = self.enterContext(patch.object(core.shutil, "which", return_value="/trusted/zstd"))

    @property
    def packages(self):
        return self.root / "cache" / "roc" / "packages"

    @property
    def tree(self):
        return self.packages / core.CONTENT_HASH

    @property
    def sidecar(self):
        return self.packages / (core.CONTENT_HASH + ".deps")

    def write_tree(self, path):
        path.mkdir(mode=0o700)
        for name, data in self.payloads.items():
            file = path / name
            file.write_bytes(data)
            file.chmod(0o600)

    def fake_process(self, command, maximum, seconds, environment, label, cwd=None):
        if label == "Core API download":
            return self.raw
        if label == "Core stdlib zstd availability":
            return b"unavailable\n"
        if label == "Core data decode":
            return self.tar
        if label == "Core compiler identity":
            return b"Roc compiler version future-nightly\n"
        if label == "trusted Core unbundle":
            self.assertEqual(command[1:], ["unbundle", core.CONTENT_HASH + ".tar.zst"])
            self.write_tree(Path(cwd) / core.CONTENT_HASH)
            return b"Extracted\n"
        self.fail("unexpected data-reader invocation")

    def admit(self, compiler=None):
        return core.admit("0.12.0", self.environment, compiler=compiler)

    def assert_no_publication(self):
        self.assertFalse(os.path.lexists(self.tree))
        self.assertFalse(list(self.packages.glob(".core-admission-*")))

    def test_data_mode_fixed_transport_and_no_ambient_compiler(self):
        result = self.admit()
        self.assertEqual(result["method"], "data-zstd")
        self.assertTrue(result["tar_digest_recomputed"])
        self.assertIsNone(result["compiler"])
        self.assertEqual(result["inventory"]["files"], 2)
        command = self.process.call_args_list[0].args[0]
        self.assertEqual(command[:2], ["curl", "--disable"])
        self.assertEqual(command[-1], core.ASSET_URL)
        self.assertIn("725946981", command[-1])
        self.assertNotIn("Authorization", " ".join(command))
        self.assertFalse(any(call.args[4].startswith("Core compiler") for call in self.process.call_args_list))
        core.verify_tree(self.tree)

    def test_optional_stdlib_decoder_is_isolated_bounded_and_has_no_cli_fallback_on_failure(self):
        original = self.fake_process
        def process(command, maximum, seconds, environment, label, cwd=None):
            if label == "Core stdlib zstd availability":
                self.assertEqual(command[:3], [sys.executable, "-I", "-c"])
                self.assertIn("sys.stdout.buffer.write", command[3])
                return b"available\n"
            if label == "Core data decode":
                self.assertEqual(command[:3], [sys.executable, "-I", "-c"])
                self.assertEqual(command[-1], str(len(self.tar)))
                self.assertEqual(maximum, len(self.tar))
                self.assertIn("max_length=limit + 1", command[3])
                self.assertIn("window_log_max: 26", command[3])
            return original(command, maximum, seconds, environment, label, cwd)
        with patch.object(core.sys, "version_info", (3, 14)), patch.object(core, "run_bounded", side_effect=process):
            result = self.admit()
        self.assertEqual(result["decoder"], "python-stdlib-zstd")
        self.which.assert_not_called()
        def failed(command, maximum, seconds, environment, label, cwd=None):
            if label == "Core stdlib zstd availability":
                return b"available\n"
            raise ValueError("fixed decoder failure")
        with patch.object(core.sys, "version_info", (3, 14)), \
                patch.object(core, "run_bounded", side_effect=failed), self.assertRaises(ValueError):
            core.decode_data(self.root / "archive", self.environment)
        self.which.assert_not_called()

    def test_missing_optional_stdlib_uses_only_existing_host_zstd(self):
        original = self.fake_process
        def process(command, maximum, seconds, environment, label, cwd=None):
            if label == "Core stdlib zstd availability":
                return b"unavailable\n"
            return original(command, maximum, seconds, environment, label, cwd)
        with patch.object(core.sys, "version_info", (3, 14)), patch.object(core, "run_bounded", side_effect=process):
            result = self.admit()
        self.assertEqual(result["decoder"], "host-zstd")
        self.which.assert_called_once_with("zstd", path=self.environment["PATH"])

    def test_existing_tree_and_derived_sidecar_are_reverified_each_time(self):
        self.admit()
        self.sidecar.write_bytes(b"untrusted derived metadata")
        self.process.reset_mock()
        result = self.admit()
        self.assertEqual(result["method"], "verified-existing-tree")
        self.assertTrue(result["cache"]["reused"])
        self.assertTrue(result["sidecar"]["invalidated"])
        self.assertFalse(self.sidecar.exists())
        self.process.assert_not_called()
        result = self.admit()
        self.assertFalse(result["sidecar"]["invalidated"])

    def test_bad_existing_tree_never_invalidates_sidecar_or_repairs_bytes(self):
        self.admit()
        file = self.tree / "main.roc"
        file.write_bytes(b"X" * len(self.payloads["main.roc"]))
        self.sidecar.write_bytes(b"preserve")
        with self.assertRaises(ValueError):
            self.admit()
        self.assertEqual(self.sidecar.read_bytes(), b"preserve")
        self.assertEqual(file.read_bytes(), b"X" * len(self.payloads["main.roc"]))

    def test_partial_extra_or_bad_file_mode_refused(self):
        self.admit()
        file = self.tree / "Core.roc"
        file.unlink()
        with self.assertRaises(ValueError):
            self.admit()
        file.write_bytes(self.payloads["Core.roc"])
        file.chmod(0o600)
        extra = self.tree / "unexpected"
        extra.write_bytes(b"data")
        with self.assertRaises(ValueError):
            self.admit()
        extra.unlink()
        if os.name != "nt":
            file.chmod(0o644)
            with self.assertRaises(ValueError):
                self.admit()

    def test_orphan_sidecar_refused_without_network_or_deletion(self):
        core.packages_directory(self.environment)
        self.sidecar.write_bytes(b"orphan")
        with self.assertRaises(ValueError):
            self.admit()
        self.assertEqual(self.sidecar.read_bytes(), b"orphan")
        self.process.assert_not_called()
        self.assert_no_publication()

    @unittest.skipIf(os.name == "nt", "Unix link/pipe controls; Windows reparse behavior has a native gate")
    def test_symlink_fifo_and_hardlinked_sidecars_refused(self):
        self.admit()
        target = self.root / "outside"
        target.write_bytes(b"keep")
        for kind in ("link", "fifo", "hardlink"):
            with self.subTest(kind=kind):
                if kind == "link":
                    self.sidecar.symlink_to(target)
                elif kind == "fifo":
                    os.mkfifo(self.sidecar)
                else:
                    os.link(target, self.sidecar)
                with self.assertRaises(ValueError):
                    self.admit()
                self.assertTrue(os.path.lexists(self.sidecar))
                self.sidecar.unlink()
        self.assertEqual(target.read_bytes(), b"keep")

    @unittest.skipIf(os.name == "nt", "Native Windows reparse points have a separate gate")
    def test_cache_or_tree_links_are_never_followed(self):
        cache = self.root / "cache"
        outside = self.root / "outside"
        outside.mkdir()
        cache.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.admit()
        self.assertEqual(list(outside.iterdir()), [])
        cache.unlink()
        core.packages_directory(self.environment)
        self.tree.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.admit()
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "Real NTFS junction/link controls run under Windows native CI")
    def test_windows_actual_cache_tree_junction_and_sidecar_links_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel"
        sentinel.write_bytes(b"preserve outside bytes")

        def junction(alias):
            # mklink is a cmd builtin; bind only these generated fixture paths,
            # refusing command-expansion characters even in a caller temp root.
            for path in (alias, outside):
                self.assertFalse(any(char in str(path) for char in '&|<>^()%!"\r\n'))
            subprocess.run([os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "mklink", "/J",
                            str(alias), str(outside)], env=core.child_environment(os.environ),
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=10, check=True)
            self.assertTrue(alias.lstat().st_file_attributes & 0x400)
            self.assertEqual(alias.lstat().st_reparse_tag, 0xA0000003)  # actual NTFS mount-point junction

        cache = self.root / "cache"
        junction(cache)
        try:
            with self.assertRaises(ValueError):
                self.admit()
            self.assertEqual(sentinel.read_bytes(), b"preserve outside bytes")
            self.process.assert_not_called()
        finally:
            cache.rmdir()  # remove only the junction, never its target tree
        core.packages_directory(self.environment)
        junction(self.tree)
        try:
            with self.assertRaises(ValueError):
                self.admit()
            self.assertEqual(sentinel.read_bytes(), b"preserve outside bytes")
            self.process.assert_not_called()
        finally:
            self.tree.rmdir()
        self.admit()  # disposable two-file fixture; never the real admitted cache
        self.process.reset_mock()
        for kind in ("symlink", "hardlink"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    self.sidecar.symlink_to(sentinel)
                    self.assertTrue(self.sidecar.lstat().st_file_attributes & 0x400)
                else:
                    os.link(sentinel, self.sidecar)
                    self.assertEqual(self.sidecar.stat().st_nlink, 2)
                try:
                    with self.assertRaises(ValueError):
                        self.admit()
                    self.assertTrue(os.path.lexists(self.sidecar))
                    self.assertEqual(sentinel.read_bytes(), b"preserve outside bytes")
                    core.verify_tree(self.tree)
                finally:
                    self.sidecar.unlink()
        self.process.assert_not_called()

    def test_raw_and_tar_mismatches_never_publish(self):
        for phase, data in (("Core API download", self.raw[:-1]),
                            ("Core API download", b"x" * len(self.raw)),
                            ("Core data decode", self.tar + b"x"),
                            ("Core data decode", b"x" + self.tar[1:])):
            def bad(command, maximum, seconds, environment, label, cwd=None):
                return data if label == phase else self.fake_process(command, maximum, seconds, environment, label, cwd)
            with self.subTest(phase=phase), patch.object(core, "run_bounded", side_effect=bad), self.assertRaises(ValueError):
                self.admit()
            self.assert_no_publication()

    def test_unsafe_link_pax_duplicate_extra_and_missing_tar_entries_refuse(self):
        cases = [
            [("../main.roc", b"", tarfile.REGTYPE, 0o644, {})],
            [("/main.roc", b"", tarfile.REGTYPE, 0o644, {})],
            [("main.roc", b"", tarfile.SYMTYPE, 0o644, {})],
            [("main.roc", b"", tarfile.LNKTYPE, 0o644, {})],
            [("main.roc", b"", tarfile.DIRTYPE, 0o644, {})],
            [("main.roc", self.payloads["main.roc"], tarfile.REGTYPE, 0o644, {"comment": "extra"})],
            self.entries + [self.entries[0]],
            self.entries + [("extra.roc", b"", tarfile.REGTYPE, 0o644, {})],
            self.entries[:-1],
            [("main.roc", self.payloads["main.roc"], tarfile.REGTYPE, 0o755, {})],
        ]
        for entries in cases:
            data = tar_bytes(entries)
            with self.subTest(names=[entry[0] for entry in entries]), \
                    patch.object(core, "TAR_BYTES", len(data)), patch.object(core, "TAR_SHA256", core.sha256(data)), \
                    self.assertRaises(ValueError):
                core.inspect_tar(data)

    def test_corrupt_member_payload_is_checked_even_after_tar_gate(self):
        data = tar_bytes([("main.roc", b"x" * len(self.payloads["main.roc"]), tarfile.REGTYPE, 0o644, {}),
                          self.entries[1]])
        with patch.object(core, "TAR_BYTES", len(data)), patch.object(core, "TAR_SHA256", core.sha256(data)), \
                self.assertRaises(ValueError):
            core.inspect_tar(data)

    def test_failed_or_racing_atomic_publication_cleans_only_owned_staging(self):
        original = core.atomic_insert
        def collision(source, destination):
            destination.mkdir()
            (destination / "other-owner").write_bytes(b"keep")
            return original(source, destination)
        with patch.object(core, "atomic_insert", side_effect=ValueError("simulated atomic refusal")), \
                self.assertRaises(ValueError):
            self.admit()
        self.assert_no_publication()
        with patch.object(core, "atomic_insert", side_effect=collision), self.assertRaises((ValueError, OSError)):
            self.admit()
        self.assertEqual((self.tree / "other-owner").read_bytes(), b"keep")
        self.assertFalse(list(self.packages.glob(".core-admission-*")))

    def test_sidecar_race_before_publication_refuses_without_trusting_it(self):
        original = core.verify_tree
        def race(directory):
            original(directory)
            if directory != self.tree:
                self.sidecar.write_bytes(b"racing metadata")
        with patch.object(core, "verify_tree", side_effect=race), self.assertRaises(ValueError):
            self.admit()
        self.assert_no_publication()
        self.assertEqual(self.sidecar.read_bytes(), b"racing metadata")

    def test_post_extraction_corruption_refuses_before_publication(self):
        original = core.verify_tree
        def corrupt(directory):
            if directory != self.tree:
                (directory / "Core.roc").write_bytes(b"x" * len(self.payloads["Core.roc"]))
            return original(directory)
        with patch.object(core, "verify_tree", side_effect=corrupt), self.assertRaises(ValueError):
            self.admit()
        self.assert_no_publication()

    def test_changed_sidecar_inode_is_preserved_after_tree_verification(self):
        self.admit()
        self.sidecar.write_bytes(b"original derived state")
        original = core.verify_tree
        def replace_sidecar(directory):
            original(directory)
            replacement = self.packages / "replacement-sidecar"
            replacement.write_bytes(b"racing state")
            replacement.replace(self.sidecar)
        with patch.object(core, "verify_tree", side_effect=replace_sidecar), self.assertRaises(ValueError):
            self.admit()
        self.assertEqual(self.sidecar.read_bytes(), b"racing state")
        original(self.tree)

    def test_explicit_trusted_compiler_needs_no_zstd_and_is_recorded(self):
        self.which.reset_mock()
        result = self.admit(Path(sys.executable).resolve())
        self.which.assert_not_called()
        self.assertEqual(result["method"], "trusted-roc-unbundle")
        self.assertFalse(result["tar_digest_recomputed"])
        self.assertEqual(result["compiler"]["version"], "Roc compiler version future-nightly")
        core.verify_tree(self.tree)

    def test_failed_official_unbundle_or_extra_output_never_publishes(self):
        original = self.fake_process
        def failed(command, maximum, seconds, environment, label, cwd=None):
            if label == "trusted Core unbundle":
                self.write_tree(Path(cwd) / core.CONTENT_HASH)
                raise ValueError("official BLAKE3 refusal")
            return original(command, maximum, seconds, environment, label, cwd)
        with patch.object(core, "run_bounded", side_effect=failed), self.assertRaises(ValueError):
            self.admit(Path(sys.executable).resolve())
        self.assert_no_publication()
        def extra(command, maximum, seconds, environment, label, cwd=None):
            result = original(command, maximum, seconds, environment, label, cwd)
            if label == "trusted Core unbundle":
                (Path(cwd) / (core.CONTENT_HASH + ".deps")).write_bytes(b"never copy")
            return result
        with patch.object(core, "run_bounded", side_effect=extra), self.assertRaises(ValueError):
            self.admit(Path(sys.executable).resolve())
        self.assert_no_publication()

    def test_no_decoder_or_relative_compiler_does_not_select_ambient_roc(self):
        with patch.object(core.shutil, "which", return_value=None), self.assertRaises(ValueError):
            self.admit()
        self.assert_no_publication()
        with self.assertRaises(ValueError):
            self.admit(Path("roc"))

    def test_original_environment_and_artifact_ir_stores_unchanged(self):
        artifacts = self.root / "cache" / "blueprint" / "v1" / "artifacts"
        artifacts.mkdir(parents=True)
        (self.root / "cache").chmod(0o700)
        (artifacts / "sentinel").write_bytes(b"verified artifact")
        ir = self.root / "ir"
        ir.mkdir()
        (ir / "sentinel").write_bytes(b"retained IR")
        with patch.dict(os.environ, self.environment):
            before = dict(os.environ)
            self.admit()
            self.assertEqual(dict(os.environ), before)
        self.assertEqual((artifacts / "sentinel").read_bytes(), b"verified artifact")
        self.assertEqual((ir / "sentinel").read_bytes(), b"retained IR")

    def test_present_empty_cache_override_refuses_before_any_cache_or_network_action(self):
        environment = {**self.environment, "XDG_CACHE_HOME": ""}
        with patch.object(core, "safe_directory") as directory, self.assertRaisesRegex(ValueError, "must not be empty"):
            core.admit("0.12.0", environment)
        directory.assert_not_called()
        self.process.assert_not_called()
        self.assertFalse((self.root / "cache").exists())


class CoreContractTests(unittest.TestCase):
    def test_fixed_record_has_only_known_core(self):
        self.assertEqual(core.VERSION, "0.12.0")
        self.assertEqual(core.ASSET_ID, 725946981)
        self.assertEqual((core.RAW_BYTES, core.TAR_BYTES, len(core.MEMBERS)), (132723, 531968, 25))
        self.assertEqual(sum(size for _, size, _ in core.MEMBERS), 511858)
        self.assertEqual(len({name for name, _, _ in core.MEMBERS}), 25)
        with patch.object(core, "fetch_raw") as fetch:
            self.assertIsNone(core.admit("0.13.0", {}))
            fetch.assert_not_called()

    def test_cache_selection_matches_unix_and_windows_primary_source(self):
        self.assertEqual(core.cache_home({"XDG_CACHE_HOME": "/xdg", "LOCALAPPDATA": "/local", "HOME": "/home"}, windows=True), "/xdg")
        self.assertEqual(core.cache_home({"LOCALAPPDATA": "/local", "HOME": "/home"}, windows=True), "/local")
        self.assertEqual(core.cache_home({"LOCALAPPDATA": "/local", "HOME": "/home"}, windows=False), str(Path("/home") / ".cache"))
        with self.assertRaises(ValueError):
            core.physical_path("relative")
        for environment in ({"XDG_CACHE_HOME": "", "LOCALAPPDATA": "/local", "HOME": "/home"},
                            {"LOCALAPPDATA": "", "HOME": "/home"}):
            with self.subTest(environment=environment), self.assertRaisesRegex(ValueError, "must not be empty"):
                core.cache_home(environment, windows=True)

    def test_native_reparse_attribute_is_never_regular_admission(self):
        class Info:
            st_mode = stat.S_IFDIR | 0o777
            st_file_attributes = 1040
        self.assertTrue(core._link(Info()))

    @unittest.skipIf(os.name == "nt", "Darwin aliases use Unix links; Windows reparse points have a native gate")
    def test_only_verified_leading_darwin_aliases_are_followed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            physical = root / "private" / "var"
            physical.mkdir(parents=True)
            alias = root / "var"
            alias.symlink_to("private/var", target_is_directory=True)
            with patch.object(core.sys, "platform", "darwin"), patch.object(core, "_DARWIN_ALIASES", ((alias, physical),)):
                self.assertEqual(core.safe_directory(alias / "cache"), physical / "cache")
                (physical / "cache" / "nested").symlink_to(root, target_is_directory=True)
                with self.assertRaises(ValueError):
                    core.safe_directory(alias / "cache" / "nested" / "bad")
                alias.unlink()
                alias.symlink_to(root, target_is_directory=True)
                with self.assertRaises(ValueError):
                    core.safe_directory(alias / "cache")

    def test_real_children_get_only_host_settings_not_credentials_or_loader_hooks(self):
        environment = {**os.environ, "GH_TOKEN": "private", "GITHUB_TOKEN": "private",
                       "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "private", "UNRELATED_SECRET": "private",
                       "PYTHONPATH": "/injected", "BASH_ENV": "/injected",
                       "XDG_CACHE_HOME": "/fixed-source", "ROC_CACHE_DIR": "/fixed-ir"}
        data = core.run_bounded([sys.executable, "-c", "import os,json;print(json.dumps(dict(os.environ)))"],
                                65536, 10, environment, "test data reader")
        child = json.loads(data)
        for name in ("GH_TOKEN", "GITHUB_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
                     "UNRELATED_SECRET", "PYTHONPATH", "BASH_ENV"):
            self.assertNotIn(name, child)
        self.assertEqual(child["XDG_CACHE_HOME"], "/fixed-source")
        self.assertEqual(child["ROC_CACHE_DIR"], "/fixed-ir")

    def test_real_output_and_deadline_bounds(self):
        cases = [("import os;os.write(1,b'x'*100)", 10, 5),
                 ("import os;os.write(2,b'x'*5000)", 10, 5),
                 ("import time;time.sleep(5)", 10, 0.05)]
        for code, limit, seconds in cases:
            with self.subTest(code=code), self.assertRaises(ValueError):
                core.run_bounded([sys.executable, "-c", code], limit, seconds, os.environ, "test reader")

    @unittest.skipUnless(os.name == "nt", "Real Windows job containment is checked by the Windows native CI job")
    def test_windows_parent_exit_pipe_holder_is_killed_on_timeout_and_output_limit(self):
        import ctypes
        from ctypes import wintypes as W
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, arguments, result in (
                ("OpenProcess", [W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
                ("WaitForSingleObject", [W.HANDLE, W.DWORD], W.DWORD),
                ("TerminateProcess", [W.HANDLE, W.UINT], W.BOOL),
                ("CloseHandle", [W.HANDLE], W.BOOL)):
            function = getattr(kernel, name)
            function.argtypes, function.restype = arguments, result
        with tempfile.TemporaryDirectory() as temporary:
            for name, child, seconds in (
                    ("timeout", "import time;time.sleep(30)", 0.7),
                    ("output", "import os,time;time.sleep(0.2);os.write(1,b'x'*1000);time.sleep(30)", 5)):
                pidfile = Path(temporary) / name
                parent = ("import os,subprocess,sys;from pathlib import Path;"
                          "child=subprocess.Popen([sys.executable,'-c',sys.argv[2]]);"
                          "Path(sys.argv[1]).write_text(str(child.pid));os._exit(0)")
                start = time.monotonic()
                with self.subTest(reason=name), self.assertRaisesRegex(ValueError, "deadline|output bound"):
                    core.run_bounded([sys.executable, "-c", parent, str(pidfile), child],
                                     10, seconds, os.environ, "test parent-exit pipe holder")
                self.assertLess(time.monotonic() - start, seconds + 5)
                self.assertTrue(pidfile.exists(), "the parent must launch its actual pipe-holding descendant")
                handle = kernel.OpenProcess(0x100000 | 1, False, int(pidfile.read_text()))
                if not handle:
                    self.assertEqual(ctypes.get_last_error(), 87)  # already gone
                    continue
                try:
                    status = kernel.WaitForSingleObject(handle, 2000)
                    if status != 0:
                        kernel.TerminateProcess(handle, 1)  # clean up a failed negative control
                        kernel.WaitForSingleObject(handle, 2000)
                    self.assertEqual(status, 0, "the exited parent's descendant must be terminated")
                finally:
                    kernel.CloseHandle(handle)


class InstallerCoreBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.binary = b"reviewed synthetic Blueprint binary"
        self.digest = hashlib.sha256(self.binary).hexdigest()
        self.environment = {"RUNNER_OS": "Linux", "RUNNER_ARCH": "X64", "RUNNER_TEMP": str(self.root / "temp"),
                            "HOME": str(self.root), "INPUT_VERSION": "0.12.0",
                            "GITHUB_PATH": str(self.root / "path"), "GITHUB_OUTPUT": str(self.root / "output"),
                            "RA_COMPILER": "/must/not/be/selected"}
        self.fetches = []
        def fetch(url, path):
            self.fetches.append(url)
            path.write_bytes(self.binary)
        self.enterContext(patch.dict(os.environ, self.environment, clear=True))
        self.enterContext(patch.object(setup, "known_releases", return_value={"0.12.0": {"x86_64-linux": self.digest}}))
        self.enterContext(patch.object(setup, "fetch", side_effect=fetch))

    def test_verified_download_and_cached_binary_both_admit_core_before_outputs(self):
        def admit(version, environment, *, compiler):
            self.assertEqual(version, "0.12.0")
            self.assertIsNone(compiler)
            self.assertFalse(Path(environment["GITHUB_PATH"]).exists())
            self.assertFalse(Path(environment["GITHUB_OUTPUT"]).exists())
            installed = self.root / "temp" / "roc-automation-blueprint" / "blueprint"
            self.assertEqual(installed.read_bytes(), self.binary)
            return None
        with patch.object(setup.core_sources, "admit", side_effect=admit) as admission:
            setup.main()
            os.environ["GITHUB_PATH"] = str(self.root / "second-path")
            os.environ["GITHUB_OUTPUT"] = str(self.root / "second-output")
            setup.main()
            self.assertEqual(admission.call_count, 2)
        self.assertEqual(len(self.fetches), 1)
        self.assertIn("binary-cache-hit=true", (self.root / "second-output").read_text())

    def test_core_refusal_prevents_path_and_outputs_even_for_verified_binary(self):
        with patch.object(setup.core_sources, "admit", side_effect=ValueError("Core refusal")), self.assertRaises(ValueError):
            setup.main()
        self.assertFalse((self.root / "path").exists())
        self.assertFalse((self.root / "output").exists())

    def test_custom_digest_keeps_existing_install_behavior_on_download_and_cache_hit(self):
        custom = b"caller-selected different native binary"
        digest = hashlib.sha256(custom).hexdigest()
        os.environ["INPUT_SHA256_X86_64_LINUX"] = digest
        def fetch(url, path):
            self.fetches.append(url)
            path.write_bytes(custom)
        with patch.object(setup, "fetch", side_effect=fetch), patch.object(setup.core_sources, "admit") as admission:
            setup.main()
            os.environ["GITHUB_PATH"] = str(self.root / "second-path")
            os.environ["GITHUB_OUTPUT"] = str(self.root / "second-output")
            setup.main()
        admission.assert_not_called()
        self.assertEqual(len(self.fetches), 1)
        self.assertIn("binary-cache-hit=true", (self.root / "second-output").read_text())
        self.assertEqual((self.root / "temp/roc-automation-blueprint/blueprint").read_bytes(), custom)

    def test_explicit_builtin_digest_still_authorizes_core(self):
        os.environ["INPUT_SHA256_X86_64_LINUX"] = self.digest
        with patch.object(setup.core_sources, "admit", return_value=None) as admission:
            setup.main()
        admission.assert_called_once_with("0.12.0", os.environ, compiler=None)

    def test_older_reviewed_binary_does_not_admit_this_core(self):
        os.environ["INPUT_VERSION"] = "0.11.0"
        with patch.object(setup, "known_releases", return_value={"0.11.0": {"x86_64-linux": self.digest}}), \
                patch.object(setup.core_sources, "admit") as admission:
            setup.main()
        admission.assert_not_called()

    def test_binary_hash_refusal_never_calls_core(self):
        with patch.object(setup, "fetch", side_effect=lambda url, path: path.write_bytes(b"wrong")), \
                patch.object(setup.core_sources, "admit") as admission, self.assertRaises(ValueError):
            setup.main()
        admission.assert_not_called()
        self.assertFalse((self.root / "path").exists())

    def test_prepare_does_not_admit_or_install_sources(self):
        with patch.object(setup.core_sources, "admit") as admission:
            setup.main(prepare=True)
        admission.assert_not_called()
        self.assertFalse((self.root / "path").exists())


if __name__ == "__main__":
    unittest.main()
