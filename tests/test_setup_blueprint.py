import hashlib
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("setup_blueprint", Path(__file__).resolve().parents[1] / "actions/setup-blueprint/setup_blueprint.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)

BINARY = b"#!/bin/sh\necho blueprint\n"
DIGEST = hashlib.sha256(BINARY).hexdigest()
OTHER = "1" * 64
KNOWN = {"0.4.0": {"x86_64-linux": DIGEST, "aarch64-darwin": DIGEST}}
UNIX_SYSTEMS = {"x86_64-linux", "aarch64-linux", "aarch64-darwin"}
PLATFORM = "https://github.com/lukewilliamboswell/roc-blueprint/releases/download/{}/4jmnCGtQeuXTJJYc1rPrnA1pQFPRqUecY5oau1te8cYn.tar.zst"


def config(version):
    return f'# Tasks for a repository.\napp [config] {{ pf: platform "{PLATFORM.format(version)}" }}\n\nconfig = []\n'


def serving(content, seen=None):
    def download(url, destination):
        if seen is not None:
            seen.append(url)
        destination.write_bytes(content)
    return download


class SetupBlueprintTests(unittest.TestCase):
    def test_runner_maps_to_released_system(self):
        self.assertEqual(setup.system_for("Linux", "X64"), "x86_64-linux")
        self.assertEqual(setup.system_for("Linux", "ARM64"), "aarch64-linux")
        self.assertEqual(setup.system_for("macOS", "ARM64"), "aarch64-darwin")
        self.assertEqual(setup.system_for("macOS", "X64"), "x86_64-darwin")
        self.assertEqual(setup.system_for("Windows", "X64"), "x86_64-windows")

    def test_unreleased_systems_are_rejected(self):
        for runner in (("Windows", "ARM64"), ("Linux", "ARM"), ("", "")):
            with self.subTest(runner=runner), self.assertRaisesRegex(ValueError, "no blueprint binary"):
                setup.system_for(*runner)

    def test_url_names_only_the_release_asset(self):
        self.assertEqual(
            setup.asset_url("0.4.0", "x86_64-linux"),
            "https://github.com/lukewilliamboswell/roc-blueprint/releases/download/0.4.0/blueprint-x86_64-linux",
        )
        self.assertTrue(setup.asset_url("0.5.0-rc1", "aarch64-darwin").endswith("/0.5.0-rc1/blueprint-aarch64-darwin"))
        self.assertTrue(setup.asset_url("0.9.0", "x86_64-windows").endswith("/0.9.0/blueprint-x86_64-windows.exe"))

    def test_versions_that_could_redirect_the_download_are_rejected(self):
        for version in ("", "latest", "v0.4.0", "0.4", "0.4.0/../../evil", "0.4.0?x=1", "0.4.0 ", "0.4.0\n0.3.0", "main", "0.04.0"):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "version must be"):
                setup.asset_url(version, "x86_64-linux")

    def test_recorded_checksum_is_used_when_none_is_supplied(self):
        self.assertEqual(setup.expected_sha256({}, KNOWN, "0.4.0", "x86_64-linux"), DIGEST)

    def test_supplied_checksum_overrides_the_recorded_one(self):
        self.assertEqual(setup.expected_sha256({"x86_64-linux": OTHER}, KNOWN, "0.4.0", "x86_64-linux"), OTHER)
        self.assertEqual(setup.expected_sha256({"x86_64-linux": OTHER}, KNOWN, "0.5.0", "x86_64-linux"), OTHER)

    def test_checksum_supplied_for_another_system_is_not_used(self):
        with self.assertRaisesRegex(ValueError, "set the sha256-aarch64-linux input"):
            setup.expected_sha256({"x86_64-linux": OTHER}, KNOWN, "0.4.0", "aarch64-linux")

    def test_unrecorded_release_without_a_checksum_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "records no SHA-256 for roc-blueprint 0.5.0"):
            setup.expected_sha256({}, KNOWN, "0.5.0", "x86_64-linux")

    def test_malformed_checksums_are_rejected(self):
        for value in (DIGEST.upper(), DIGEST[:-1], DIGEST + "0", "sha256:" + DIGEST, "g" * 64):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "64 lowercase hexadecimal"):
                setup.expected_sha256({"x86_64-linux": value}, KNOWN, "0.4.0", "x86_64-linux")
            with self.subTest(recorded=value), self.assertRaisesRegex(ValueError, "no reviewed SHA-256 is recorded"):
                setup.expected_sha256({}, {"0.4.0": {"x86_64-linux": value}}, "0.4.0", "x86_64-linux")

    def test_recorded_releases_are_well_formed(self):
        releases = setup.known_releases()
        self.assertIn("0.4.0", releases)
        for version, checksums in releases.items():
            with self.subTest(version=version):
                self.assertTrue(setup.VERSION.fullmatch(version))
                # Windows binaries start with roc-blueprint 0.9.0; none records Intel macOS yet.
                self.assertLessEqual(UNIX_SYSTEMS, set(checksums))
                self.assertLessEqual(set(checksums), set(setup.SYSTEMS.values()))
                for checksum in checksums.values():
                    self.assertTrue(setup.SHA256.fullmatch(checksum))
        self.assertEqual(set(releases["0.9.0"]), UNIX_SYSTEMS | {"x86_64-windows"})

    def test_version_is_read_from_the_platform_url(self):
        self.assertEqual(setup.version_in(config("0.4.0")), "0.4.0")
        self.assertEqual(setup.version_in(config("0.5.0-rc1")), "0.5.0-rc1")

    def test_only_the_platform_url_names_the_version(self):
        other = config("0.4.0") + f'# was {PLATFORM.format("0.3.0")}\npackages = ["{PLATFORM.format("core-0.1.0")}"]\n'
        self.assertEqual(setup.version_in(other), "0.4.0")

    def test_config_without_one_release_is_rejected(self):
        for text in ("", 'app [config] { pf: platform "../platform/main.roc" }\n', config("0.4.0").replace("lukewilliamboswell", "someone-else")):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "names no roc-blueprint release"):
                setup.version_in(text)
        with self.assertRaisesRegex(ValueError, "more than one"):
            setup.version_in(config("0.4.0") + config("0.3.0"))

    def test_version_read_from_the_config_cannot_redirect_the_download(self):
        for version in ("latest", "v0.4.0", "0.4", "main", "0.04.0"):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "version must be"):
                setup.version_in(config(version))

    def test_matching_binary_is_installed_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            seen = []
            installed = setup.install("0.4.0", "x86_64-linux", DIGEST, Path(temporary) / "bin", serving(BINARY, seen))
            self.assertEqual(installed, Path(temporary) / "bin" / "blueprint")
            self.assertEqual(installed.read_bytes(), BINARY)
            self.assertTrue(os.access(installed, os.X_OK))
            self.assertEqual(seen, ["https://github.com/lukewilliamboswell/roc-blueprint/releases/download/0.4.0/blueprint-x86_64-linux"])
            self.assertEqual([entry.name for entry in installed.parent.iterdir()], ["blueprint"])

    def test_mismatched_binary_is_never_installed(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "bin"
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                setup.install("0.4.0", "x86_64-linux", DIGEST, directory, serving(BINARY + b"tampered"))
            self.assertEqual(list(directory.iterdir()), [])

    def test_mismatch_does_not_replace_an_earlier_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "bin"
            setup.install("0.4.0", "x86_64-linux", DIGEST, directory, serving(BINARY))
            with self.assertRaises(ValueError):
                setup.install("0.4.0", "x86_64-linux", DIGEST, directory, serving(b"other"))
            self.assertEqual((directory / "blueprint").read_bytes(), BINARY)
            self.assertEqual([entry.name for entry in directory.iterdir()], ["blueprint"])

    def test_failed_download_leaves_nothing_behind(self):
        def failing(url, destination):
            destination.write_bytes(b"partial")
            raise OSError("connection reset")

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "bin"
            with self.assertRaises(OSError):
                setup.install("0.4.0", "x86_64-linux", DIGEST, directory, failing)
            self.assertEqual(list(directory.iterdir()), [])

    def test_main_uses_only_the_checksum_for_the_runner_and_exports_the_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {
                "RUNNER_OS": "macOS",
                "RUNNER_ARCH": "ARM64",
                "RUNNER_TEMP": str(root),
                "GITHUB_PATH": str(root / "path"),
                "GITHUB_OUTPUT": str(root / "output"),
                "INPUT_VERSION": "0.4.0",
                "INPUT_SHA256_X86_64_LINUX": "0" * 64,
                "INPUT_SHA256_AARCH64_DARWIN": DIGEST,
            }
            seen = []
            with patch.dict(os.environ, env, clear=True), patch.object(setup, "fetch", serving(BINARY, seen)):
                setup.main()
            directory = root / "roc-automation-blueprint"
            self.assertEqual((root / "path").read_text(), f"{directory}\n")
            self.assertIn(f"blueprint-path={directory / 'blueprint'}\n", (root / "output").read_text())
            self.assertIn("system=aarch64-darwin\n", (root / "output").read_text())
            self.assertIn("version=0.4.0\n", (root / "output").read_text())
            self.assertTrue(seen[0].endswith("/blueprint-aarch64-darwin"))

    def run_main(self, root, extra, known=KNOWN, content=BINARY):
        env = {
            "RUNNER_OS": "Linux",
            "RUNNER_ARCH": "X64",
            "RUNNER_TEMP": str(root / "temp"),
            "GITHUB_WORKSPACE": str(root / "workspace"),
            "GITHUB_PATH": str(root / "path"),
            "GITHUB_OUTPUT": str(root / "output"),
            **extra,
        }
        (root / "temp").mkdir(exist_ok=True)
        (root / "workspace").mkdir(exist_ok=True)
        seen = []
        with patch.dict(os.environ, env, clear=True), patch.object(setup, "fetch", serving(content, seen)):
            with patch.object(setup, "known_releases", lambda: known):
                setup.main()
        return seen

    def test_main_needs_no_input_when_blueprint_roc_names_a_recorded_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "workspace").mkdir()
            (root / "workspace" / "Blueprint.roc").write_text(config("0.4.0"))
            seen = self.run_main(root, {})
            self.assertEqual(seen, ["https://github.com/lukewilliamboswell/roc-blueprint/releases/download/0.4.0/blueprint-x86_64-linux"])
            self.assertIn("version=0.4.0\n", (root / "output").read_text())

    def test_main_prefers_the_version_input_to_blueprint_roc(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "workspace").mkdir()
            (root / "workspace" / "Blueprint.roc").write_text(config("0.3.0"))
            seen = self.run_main(root, {"INPUT_VERSION": "0.4.0"})
            self.assertTrue(seen[0].endswith("/0.4.0/blueprint-x86_64-linux"))

    def test_main_fails_before_downloading_without_a_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "check out the repository first or set the version input"):
                self.run_main(Path(temporary), {})
            self.assertFalse((Path(temporary) / "path").exists())

    def test_main_fails_before_downloading_an_unrecorded_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "workspace").mkdir()
            (root / "workspace" / "Blueprint.roc").write_text(config("0.5.0"))
            with self.assertRaisesRegex(ValueError, "records no SHA-256 for roc-blueprint 0.5.0"):
                self.run_main(root, {})
            self.assertFalse((root / "path").exists())

    def test_blueprint_roc_cannot_admit_a_binary_the_recorded_hash_rejects(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "workspace").mkdir()
            (root / "workspace" / "Blueprint.roc").write_text(config("0.4.0"))
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                self.run_main(root, {}, content=BINARY + b"tampered")
            self.assertFalse((root / "path").exists())

    def test_windows_installs_the_exe_of_a_release_that_has_one(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seen = self.run_main(root, {"RUNNER_OS": "Windows", "INPUT_VERSION": "0.9.0"},
                                 known={"0.9.0": {"x86_64-windows": DIGEST}})
            self.assertEqual(seen, ["https://github.com/lukewilliamboswell/roc-blueprint/releases/download/0.9.0/blueprint-x86_64-windows.exe"])
            installed = root / "temp" / "roc-automation-blueprint" / "blueprint.exe"
            self.assertEqual(installed.read_bytes(), BINARY)
            self.assertIn(f"blueprint-path={installed}\n", (root / "output").read_text())
            self.assertIn("system=x86_64-windows\n", (root / "output").read_text())

    def test_windows_fails_before_downloading_a_release_without_a_windows_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "no reviewed SHA-256 is recorded for roc-blueprint 0.4.0 on x86_64-windows"):
                self.run_main(root, {"RUNNER_OS": "Windows", "INPUT_VERSION": "0.4.0"})
            self.assertFalse((root / "path").exists())

    def test_windows_refuses_nix(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "Nix does not run on Windows"):
                self.run_main(root, {"RUNNER_OS": "Windows", "INPUT_VERSION": "0.9.0", "INPUT_INSTALL_NIX": "true"},
                              known={"0.9.0": {"x86_64-windows": DIGEST}})
            self.assertFalse((root / "path").exists())

    def test_intel_macos_installs_only_with_a_supplied_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "on x86_64-darwin; set the sha256-x86_64-darwin input"):
                self.run_main(root, {"RUNNER_OS": "macOS", "INPUT_VERSION": "0.9.0"}, known=setup.known_releases())
            self.assertFalse((root / "path").exists())
            seen = self.run_main(root, {"RUNNER_OS": "macOS", "INPUT_VERSION": "0.9.0", "INPUT_SHA256_X86_64_DARWIN": DIGEST})
            self.assertEqual(seen, ["https://github.com/lukewilliamboswell/roc-blueprint/releases/download/0.9.0/blueprint-x86_64-darwin"])
            self.assertIn("system=x86_64-darwin\n", (root / "output").read_text())

    def test_main_fails_before_downloading_on_an_unsupported_runner(self):
        seen = []
        with tempfile.TemporaryDirectory() as temporary:
            env = {"RUNNER_OS": "Linux", "RUNNER_ARCH": "ARM", "RUNNER_TEMP": temporary, "INPUT_VERSION": "0.4.0"}
            with patch.dict(os.environ, env, clear=True), patch.object(setup, "fetch", serving(BINARY, seen)):
                with self.assertRaisesRegex(ValueError, "no blueprint binary"):
                    setup.main()
        self.assertEqual(seen, [])


class ArtifactCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "Blueprint.lock").write_text('{"version":1}')
        self.env = {"INPUT_CACHE":"true", "INPUT_CACHE_WORKLOAD":"docs", "GITHUB_WORKSPACE":str(self.root), "HOME":str(self.root), "GITHUB_WORKFLOW":"Package CI", "GITHUB_JOB":"cold"}

    def settings(self, **extra):
        return setup.cache_settings({**self.env, **extra}, "0.6.0-rc1", "x86_64-linux")

    def test_only_artifact_bytes_are_cached_and_same_tool_set_can_warm_on_another_job(self):
        path, key = self.settings()
        self.assertEqual(Path(path), self.root / ".cache/blueprint/v1/artifacts")
        self.assertEqual(self.settings(GITHUB_JOB="warm"), (path, key))
        self.assertNotIn("trees", path)
        self.assertNotIn(".blueprint/run", path)

    def test_different_tool_subsets_and_lock_updates_cannot_claim_each_others_cache(self):
        _, docs = self.settings()
        _, checks = self.settings(INPUT_CACHE_WORKLOAD="fixed-check-tools")
        self.assertNotEqual(docs, checks)
        (self.root / "Blueprint.lock").write_text('{"version":2}')
        self.assertNotEqual(docs, self.settings()[1])
        self.assertNotEqual(self.settings()[1], self.settings(GITHUB_WORKFLOW="Release")[1])

    def test_windows_store_is_under_localappdata_unless_xdg_names_one(self):
        local = self.root / "local"
        path, _ = setup.cache_settings({**self.env, "LOCALAPPDATA": str(local)}, "0.9.0", "x86_64-windows")
        self.assertEqual(Path(path), local / "blueprint/v1/artifacts")
        path, _ = setup.cache_settings({**self.env, "LOCALAPPDATA": str(local), "XDG_CACHE_HOME": str(self.root / "xdg")}, "0.9.0", "x86_64-windows")
        self.assertEqual(Path(path), self.root / "xdg/blueprint/v1/artifacts")
        path, _ = self.settings(LOCALAPPDATA=str(local))
        self.assertEqual(Path(path), self.root / ".cache/blueprint/v1/artifacts")

    def test_xdg_path_and_disabled_cache(self):
        path, _ = self.settings(XDG_CACHE_HOME=str(self.root / "xdg"))
        self.assertEqual(Path(path), self.root / "xdg/blueprint/v1/artifacts")
        self.assertEqual(setup.cache_settings({}, "0.6.0-rc1", "x86_64-linux"), ("", ""))

    def test_invalid_flags_missing_scope_and_symlinked_lock_fail_before_use(self):
        for extra in [{"INPUT_CACHE":"yes"}, {"INPUT_INSTALL_NIX":"yes"}, {"INPUT_CACHE_WORKLOAD":""}, {"INPUT_CACHE_WORKLOAD":"bad\nkey"}, {"XDG_CACHE_HOME":"relative"}]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.settings(**extra)
        lock = self.root / "Blueprint.lock"
        lock.unlink()
        target = self.root / "elsewhere"; target.write_text("outside")
        lock.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "regular checked-in"):
            self.settings()

    def test_rc1_version_selection_and_reviewed_registry_cover_native_runners(self):
        self.assertEqual(setup.version_in(config("0.6.0-rc1")), "0.6.0-rc1")
        for system in UNIX_SYSTEMS:
            self.assertRegex(setup.expected_sha256({}, setup.known_releases(), "0.6.0-rc1", system), r"^[0-9a-f]{64}$")
        for system in UNIX_SYSTEMS | {"x86_64-windows"}:
            self.assertRegex(setup.expected_sha256({}, setup.known_releases(), "0.9.0", system), r"^[0-9a-f]{64}$")


class ExecutableCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / "install"
        self.cache = self.root / "cache" / "identity"

    def install(self, content=BINARY, seen=None):
        return setup.install("0.4.0", "x86_64-linux", DIGEST, self.directory,
                             serving(content, seen), cache_directory=self.cache)

    def test_cold_cache_downloads_then_verified_hit_needs_no_network(self):
        seen = []
        installed = self.install(seen=seen)
        self.assertEqual(len(seen), 1)
        installed.unlink()
        with patch.object(setup, "fetch", side_effect=AssertionError("unexpected network")):
            admitted = setup.install("0.4.0", "x86_64-linux", DIGEST, self.directory,
                                     cache_directory=self.cache)
        self.assertEqual(admitted.read_bytes(), BINARY)
        self.assertEqual((self.cache / "blueprint").read_bytes(), BINARY)

    def test_cached_bytes_are_rehashed_on_each_use_and_corruption_is_refetched(self):
        self.install()
        cached = self.cache / "blueprint"
        cached.write_bytes(b"corrupt")
        seen = []
        self.assertEqual(self.install(seen=seen).read_bytes(), BINARY)
        self.assertEqual(len(seen), 1)
        self.assertEqual(cached.read_bytes(), BINARY)

    def test_corruption_and_bad_download_cannot_install_or_publish_path(self):
        self.cache.mkdir(parents=True)
        (self.cache / "blueprint").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.install(content=b"also corrupt")
        self.assertEqual(list(self.directory.iterdir()), [])
        self.assertEqual(list(self.cache.iterdir()), [self.cache / "blueprint"])

    def test_cache_file_permissions_are_never_used_as_execution_authority(self):
        installed = self.install()
        cached = self.cache / "blueprint"
        self.assertEqual(cached.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.cache.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        cached.chmod(0o777)
        installed = self.install()
        self.assertEqual(cached.stat().st_mode & 0o777, 0o600)
        self.assertEqual(installed.stat().st_mode & 0o777, 0o755)
        self.assertNotEqual(installed.stat().st_ino, cached.stat().st_ino)

    def test_symlink_cache_file_is_rejected_without_download_or_target_mutation(self):
        self.cache.mkdir(parents=True)
        outside = self.root / "outside"
        outside.write_bytes(BINARY)
        (self.cache / "blueprint").symlink_to(outside)
        seen = []
        with self.assertRaisesRegex(ValueError, "never a symlink"):
            self.install(seen=seen)
        self.assertEqual(seen, [])
        self.assertEqual(outside.read_bytes(), BINARY)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_symlink_cache_or_install_directory_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        for path in (self.directory, self.cache.parent, self.cache):
            with self.subTest(path=path):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to(outside, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, "must not be a symlink"):
                    self.install()
                path.unlink()
        self.assertEqual(list(outside.iterdir()), [])

    def test_nonregular_cache_entry_is_rejected(self):
        self.cache.mkdir(parents=True)
        os.mkfifo(self.cache / "blueprint")
        with self.assertRaisesRegex(ValueError, "regular file"):
            self.install()

    def test_oversized_cache_is_not_admitted_and_is_refetched(self):
        self.cache.mkdir(parents=True)
        (self.cache / "blueprint").write_bytes(b"x" * (len(BINARY) + 1))
        seen = []
        with patch.object(setup, "MAX_BYTES", len(BINARY)):
            self.assertEqual(self.install(seen=seen).read_bytes(), BINARY)
        self.assertEqual(len(seen), 1)

    def test_keys_isolate_versions_systems_and_reviewed_digests(self):
        env = {"RUNNER_TEMP": str(self.root)}
        variants = [("0.4.0", "x86_64-linux", DIGEST),
                    ("0.5.0", "x86_64-linux", DIGEST),
                    ("0.4.0", "aarch64-linux", DIGEST),
                    ("0.4.0", "x86_64-linux", OTHER)]
        identities = [setup.binary_cache_settings(env, *variant) for variant in variants]
        self.assertEqual(len(set(identities)), 4)
        self.assertEqual(setup.binary_cache_settings({**env, "GITHUB_WORKFLOW": "another"}, *variants[0]), identities[0])
        self.assertIn(DIGEST, identities[0][1])

    def test_opt_out_and_invalid_boolean(self):
        self.assertEqual(setup.binary_cache_settings({"INPUT_CACHE_BINARY": "false"}, "0.4.0", "x86_64-linux", DIGEST), ("", ""))
        with self.assertRaisesRegex(ValueError, "cache-binary must"):
            setup.binary_cache_settings({"INPUT_CACHE_BINARY": "yes"}, "0.4.0", "x86_64-linux", DIGEST)

    def test_preparation_exports_only_cache_identity_without_network_or_path(self):
        env = {"RUNNER_OS": "Linux", "RUNNER_ARCH": "X64", "INPUT_VERSION": "0.4.0",
               "INPUT_SHA256_X86_64_LINUX": DIGEST, "RUNNER_TEMP": str(self.root),
               "GITHUB_OUTPUT": str(self.root / "output"), "GITHUB_PATH": str(self.root / "path")}
        with patch.dict(os.environ, env, clear=True), patch.object(setup, "fetch", side_effect=AssertionError("network during preparation")):
            setup.main(prepare=True)
        self.assertIn("binary-cache-key=blueprint-executable-v1-x86_64-linux-0.4.0-" + DIGEST, (self.root / "output").read_text())
        self.assertFalse((self.root / "path").exists())
        self.assertFalse((self.root / "roc-automation-blueprint").exists())

    def test_action_restores_exact_executable_key_before_verification(self):
        action = (Path(setup.__file__).parent / "action.yml").read_text()
        self.assertLess(action.index("- id: prepare"), action.index("- id: executable"))
        self.assertLess(action.index("- id: executable"), action.index("- id: install"))
        executable_step = action.split("- id: executable", 1)[1].split("- id: install", 1)[0]
        self.assertIn("key: ${{ steps.prepare.outputs.binary-cache-key }}", executable_step)
        self.assertNotIn("restore-keys", executable_step)
        self.assertIn("INPUT_CACHE_BINARY: ${{ inputs.cache-binary }}", action.split("- id: install", 1)[1])
        self.assertIn("cache-binary:\n", action)


if __name__ == "__main__":
    unittest.main()
