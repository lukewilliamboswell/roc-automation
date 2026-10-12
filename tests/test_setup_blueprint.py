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

    def test_unreleased_systems_are_rejected(self):
        for runner in (("Windows", "X64"), ("macOS", "X64"), ("Linux", "ARM"), ("", "")):
            with self.subTest(runner=runner), self.assertRaisesRegex(ValueError, "no blueprint binary"):
                setup.system_for(*runner)

    def test_url_names_only_the_release_asset(self):
        self.assertEqual(
            setup.asset_url("0.4.0", "x86_64-linux"),
            "https://github.com/lukewilliamboswell/roc-blueprint/releases/download/0.4.0/blueprint-x86_64-linux",
        )
        self.assertTrue(setup.asset_url("0.5.0-rc1", "aarch64-darwin").endswith("/0.5.0-rc1/blueprint-aarch64-darwin"))

    def test_versions_that_could_redirect_the_download_are_rejected(self):
        for version in ("", "latest", "v0.4.0", "0.4", "0.4.0/../../evil", "0.4.0?x=1", "0.4.0 ", "0.4.0\n0.3.0", "main", "0.04.0"):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "version must be"):
                setup.asset_url(version, "x86_64-linux")

    def test_checksum_for_this_system_is_required(self):
        with self.assertRaisesRegex(ValueError, "set the sha256-aarch64-linux input"):
            setup.expected_sha256({"x86_64-linux": DIGEST, "aarch64-linux": ""}, "aarch64-linux")

    def test_malformed_checksums_are_rejected(self):
        for value in (DIGEST.upper(), DIGEST[:-1], DIGEST + "0", "sha256:" + DIGEST, "g" * 64):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "64 lowercase hexadecimal"):
                setup.expected_sha256({"x86_64-linux": value}, "x86_64-linux")

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
            self.assertTrue(seen[0].endswith("/blueprint-aarch64-darwin"))

    def test_main_fails_before_downloading_on_an_unsupported_runner(self):
        seen = []
        with tempfile.TemporaryDirectory() as temporary:
            env = {"RUNNER_OS": "Windows", "RUNNER_ARCH": "X64", "RUNNER_TEMP": temporary, "INPUT_VERSION": "0.4.0"}
            with patch.dict(os.environ, env, clear=True), patch.object(setup, "fetch", serving(BINARY, seen)):
                with self.assertRaisesRegex(ValueError, "no blueprint binary"):
                    setup.main()
        self.assertEqual(seen, [])


if __name__ == "__main__":
    unittest.main()
