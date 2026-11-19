"""Admission, caching and wiring of actions/setup-roc-nightly."""

import hashlib
import importlib.util
import io
import json
import os
import re
import tarfile
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "actions/setup-roc-nightly"
spec = importlib.util.spec_from_file_location("setup_roc_nightly", ACTION / "setup_roc_nightly.py")
nightly = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nightly)

TAG = "nightly-2026-11-18-a520eed"
TOP = "roc_nightly-linux_x86_64-2026-11-18-a520eed"
ASSET = TOP + ".tar.gz"
URL = f"https://github.com/roc-lang/nightlies/releases/download/{TAG}/{ASSET}"


def tar_gz(members):
    """members: name -> bytes for a file, None for a directory, ("link", target) for a symlink."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            if content is None:
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                bundle.addfile(info)
            elif isinstance(content, tuple):
                info.type, info.linkname = tarfile.SYMTYPE, content[1]
                bundle.addfile(info)
            else:
                info.size, info.mode = len(content), 0o755
                bundle.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


GOOD = tar_gz({TOP: None, f"{TOP}/roc": b"#!/bin/sh\necho roc\n", f"{TOP}/LICENSE": b"UPL"})


def release(content=GOOD, **changes):
    asset = {"id": 762558296, "name": ASSET, "size": len(content),
             "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
             "url": "https://api.github.com/repos/roc-lang/nightlies/releases/assets/762558296",
             "browser_download_url": URL}
    other = dict(asset, id=1, name="roc_nightly-source-2026-11-18-a520eed.tar.gz",
                 browser_download_url=URL.replace(ASSET, "roc_nightly-source-2026-11-18-a520eed.tar.gz"))
    metadata = {"id": 1, "tag_name": TAG, "draft": False, "immutable": True, "assets": [other, asset]}
    for key, value in changes.items():
        if key.startswith("asset_"):
            asset[key[6:]] = value
        else:
            metadata[key] = value
    return metadata


def selection_for(content=GOOD):
    return nightly.select(release(content), "linux_x86_64", "tar.gz", TAG)


def serving(content, calls=None):
    def download(url, destination, size):
        if calls is not None:
            calls.append(url)
        destination.write_bytes(content)
    return download


class Selection(unittest.TestCase):
    def test_selects_the_one_platform_archive_with_its_published_identity(self):
        selected = selection_for()
        self.assertEqual(selected["asset"], ASSET)
        self.assertEqual(selected["url"], URL)
        self.assertEqual(selected["directory"], TOP)
        self.assertEqual(selected["sha256"], hashlib.sha256(GOOD).hexdigest())

    def test_refuses_releases_that_are_not_published_and_immutable(self):
        for changes in ({"draft": True}, {"immutable": False}, {"immutable": None}, {"id": "1"}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "published immutable"):
                nightly.select(release(**changes), "linux_x86_64", "tar.gz", TAG)

    def test_refuses_another_tag_than_the_one_asked_for(self):
        with self.assertRaisesRegex(ValueError, "asked for"):
            nightly.select(release(), "linux_x86_64", "tar.gz", "nightly-2026-11-17-0000000")

    def test_refuses_missing_or_ambiguous_assets(self):
        metadata = release()
        metadata["assets"].append(dict(metadata["assets"][1], id=5, name=TOP + "-copy.tar.gz"))
        with self.assertRaisesRegex(ValueError, "2 compiler assets"):
            nightly.select(metadata, "linux_x86_64", "tar.gz", TAG)
        with self.assertRaisesRegex(ValueError, "0 compiler assets"):
            nightly.select(release(), "linux_arm64", "tar.gz", TAG)

    def test_refuses_an_asset_without_a_sha256_digest(self):
        for digest in (None, "", "sha1:" + "0" * 40, "sha256:" + "A" * 64, "sha256:" + "0" * 63):
            with self.subTest(digest=digest), self.assertRaisesRegex(ValueError, "no SHA-256"):
                nightly.select(release(asset_digest=digest), "linux_x86_64", "tar.gz", TAG)

    def test_refuses_an_asset_served_from_another_address(self):
        with self.assertRaisesRegex(ValueError, "is not served from"):
            nightly.select(release(asset_browser_download_url="https://example.com/" + ASSET), "linux_x86_64", "tar.gz", TAG)

    def test_refuses_unbounded_or_malformed_sizes(self):
        for size in (0, -1, "95010092", 1.5, nightly.MAX_ARCHIVE_BYTES + 1, True):
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, "invalid compiler asset"):
                nightly.select(release(asset_size=size), "linux_x86_64", "tar.gz", TAG)

    def test_refuses_malformed_tags_before_asking(self):
        for tag in ("latest", "nightly-../x", "nightly-a/b", "v0.1.0", "nightly-" + "a" * 130):
            with self.subTest(tag=tag), self.assertRaisesRegex(ValueError, "not a Roc nightly tag"):
                nightly.resolve({"RUNNER_OS": "Linux", "RUNNER_ARCH": "X64", "INPUT_NIGHTLY_TAG": tag},
                                request=lambda url, token: self.fail("asked GitHub"))

    def test_asks_for_the_requested_tag_or_the_latest_with_the_token(self):
        asked = []

        def answer(url, token):
            asked.append((url, token))
            return json.dumps(release()).encode()

        nightly.resolve({"RUNNER_OS": "Linux", "RUNNER_ARCH": "X64", "INPUT_NIGHTLY_TAG": TAG, "INPUT_TOKEN": "t"}, answer)
        nightly.resolve({"RUNNER_OS": "Linux", "RUNNER_ARCH": "X64", "INPUT_NIGHTLY_TAG": ""}, answer)
        self.assertEqual(asked, [(f"{nightly.API}/tags/{TAG}", "t"), (f"{nightly.API}/latest", "")])

    def test_unsupported_runner_is_refused(self):
        with self.assertRaisesRegex(ValueError, "no Roc nightly is published"):
            nightly.platform_for("Windows", "ARM64")


class Installation(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.store = self.root / "store"
        self.installs = self.root / "temp"

    def test_downloads_verifies_extracts_and_keeps_only_the_archive(self):
        calls = []
        compiler, hit = nightly.install(selection_for(), self.installs, self.store, serving(GOOD, calls))
        self.assertFalse(hit)
        self.assertEqual(calls, [URL])
        self.assertEqual((compiler / "roc").read_bytes(), b"#!/bin/sh\necho roc\n")
        self.assertTrue(os.access(compiler / "roc", os.X_OK))
        self.assertEqual(compiler.name, TOP)
        self.assertEqual(sorted(path.name for path in self.store.iterdir()), [ASSET])
        self.assertEqual((self.store / ASSET).read_bytes(), GOOD)
        self.assertEqual(list(compiler.parent.glob("*.tar.gz")), [])

    def test_verified_cached_archive_avoids_the_download(self):
        nightly.install(selection_for(), self.installs, self.store, serving(GOOD))
        compiler, hit = nightly.install(selection_for(), self.installs, self.store,
                                        lambda *_: self.fail("downloaded despite a verified archive"))
        self.assertTrue(hit)
        self.assertTrue((compiler / "roc").is_file())

    def test_altered_cached_archive_is_replaced_by_a_verified_download(self):
        self.store.mkdir()
        (self.store / ASSET).write_bytes(GOOD[:-1] + bytes([GOOD[-1] ^ 1]))
        calls = []
        compiler, hit = nightly.install(selection_for(), self.installs, self.store, serving(GOOD, calls))
        self.assertFalse(hit)
        self.assertEqual(calls, [URL])
        self.assertEqual((self.store / ASSET).read_bytes(), GOOD)
        self.assertTrue((compiler / "roc").is_file())

    def test_cached_symlink_is_never_followed(self):
        self.store.mkdir()
        (self.root / "elsewhere").write_bytes(GOOD)
        (self.store / ASSET).symlink_to(self.root / "elsewhere")
        calls = []
        _, hit = nightly.install(selection_for(), self.installs, self.store, serving(GOOD, calls))
        self.assertFalse(hit)
        self.assertEqual(calls, [URL])

    def test_downloaded_bytes_that_differ_from_the_published_digest_install_nothing(self):
        other = tar_gz({TOP: None, f"{TOP}/roc": b"something else entirely"})
        selected = selection_for()
        with self.assertRaisesRegex(ValueError, "does not match"):
            nightly.install(selected, self.installs, self.store, serving(other[: selected["size"]].ljust(selected["size"], b"\0")))
        self.assertEqual(list(self.installs.rglob("roc")), [])
        self.assertEqual(list(self.store.iterdir()), [])

    def test_without_a_store_nothing_is_kept(self):
        compiler, hit = nightly.install(selection_for(), self.installs, None, serving(GOOD))
        self.assertFalse(hit)
        self.assertTrue((compiler / "roc").is_file())
        self.assertFalse(self.store.exists())

    def test_refuses_members_outside_the_compiler_directory(self):
        for members in ({TOP: None, f"{TOP}/roc": b"x", "elsewhere": b"x"},
                        {TOP: None, f"{TOP}/roc": b"x", f"{TOP}/../escape": b"x"},
                        {TOP: None, f"{TOP}/roc": b"x", "/etc/escape": b"x"},
                        {TOP: None, f"{TOP}/roc": ("link", "/bin/sh")}):
            content = tar_gz(members)
            with self.subTest(members=list(members)), self.assertRaisesRegex(ValueError, "outside|not a relative path|neither a file"):
                nightly.install(selection_for(content), self.installs, None, serving(content))
        self.assertFalse((self.root / "escape").exists())

    def test_refuses_an_archive_without_its_compiler(self):
        content = tar_gz({TOP: None, f"{TOP}/LICENSE": b"UPL"})
        with self.assertRaisesRegex(ValueError, "does not contain"):
            nightly.install(selection_for(content), self.installs, None, serving(content))

    def test_windows_zip_is_checked_the_same_way(self):
        top = "roc_nightly-windows_x86_64-2026-11-18-a520eed"
        for names, error in (([f"{top}/roc.exe"], None), ([f"{top}/roc.exe", "../escape"], "outside"),
                             ([f"{top}/LICENSE"], "does not contain")):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as bundle:
                for name in names:
                    bundle.writestr(name, b"MZ")
            content = buffer.getvalue()
            selected = {"tag": TAG, "asset": top + ".zip", "size": len(content), "directory": top, "extension": "zip",
                        "sha256": hashlib.sha256(content).hexdigest(), "url": URL}
            with self.subTest(names=names):
                if error:
                    with self.assertRaisesRegex(ValueError, error):
                        nightly.install(selected, self.installs, None, serving(content))
                else:
                    compiler, _ = nightly.install(selected, self.installs, None, serving(content))
                    self.assertEqual((compiler / "roc.exe").read_bytes(), b"MZ")

    def test_glue_paths_and_only_the_named_variables_are_exported(self):
        content = tar_gz({TOP: None, f"{TOP}/roc": b"x", f"{TOP}/glue": None, f"{TOP}/glue/ZigGlue.roc": b"",
                          f"{TOP}/glue/env": b"ROC_GLUE_PLATFORM_URL=https://example.com/p.tar.zst\nPATH=/evil\n"})
        compiler, _ = nightly.install(selection_for(content), self.installs, None, serving(content))
        self.assertEqual(nightly.glue_environment(compiler), {
            "ROC_GLUE_DIR": str(compiler / "glue"), "ROC_ZIG_GLUE": str(compiler / "glue/ZigGlue.roc"),
            "ROC_GLUE_PLATFORM_URL": "https://example.com/p.tar.zst"})


class Transport(unittest.TestCase):
    def test_archive_download_sends_no_credential_and_stops_past_the_published_size(self):
        seen = []

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        def open_(request, timeout):
            seen.append(dict(request.header_items()))
            return Response(b"x" * 11)

        with tempfile.TemporaryDirectory() as temporary, patch.object(nightly.OPENER, "open", open_), \
                patch.dict(os.environ, {"GITHUB_TOKEN": "secret", "GH_TOKEN": "secret"}):
            destination = Path(temporary) / "archive"
            nightly.fetch(URL, destination, 11)
            with self.assertRaisesRegex(ValueError, "more than the 10 bytes"):
                nightly.fetch(URL, destination, 10)
        self.assertTrue(seen)
        for headers in seen:
            self.assertNotIn("Authorization", headers)

    def test_redirect_away_from_https_is_refused(self):
        handler = nightly.HttpsOnly()
        with self.assertRaisesRegex(urllib.error.URLError, "away from HTTPS"):
            handler.redirect_request(None, None, 302, "Found", {}, "http://example.com/archive")

    def test_refusals_are_reported_at_once_and_transient_failures_retried(self):
        attempts = []

        def refused():
            attempts.append(1)
            raise urllib.error.HTTPError(URL, 403, "Forbidden", {}, None)

        with patch.object(nightly.time, "sleep"), self.assertRaisesRegex(ValueError, "HTTP 403 \\(1 attempt\\)"):
            nightly.retrying(refused, "download")
        self.assertEqual(len(attempts), 1)

        def flaky():
            attempts.append(1)
            if len(attempts) < 3:
                raise urllib.error.HTTPError(URL, 502, "Bad Gateway", {}, None)
            return b"ok"

        attempts.clear()
        with patch.object(nightly.time, "sleep"):
            self.assertEqual(nightly.retrying(flaky, "download"), b"ok")
        self.assertEqual(len(attempts), 3)


class Steps(unittest.TestCase):
    """The prepare and install steps, as action.yml runs them."""

    def test_prepare_then_install_publishes_path_outputs_and_cache_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            files = {name: root / name for name in ("output", "path", "env", "install-output")}
            for path in files.values():
                path.touch()
            env = {"RUNNER_OS": "Linux", "RUNNER_ARCH": "X64", "RUNNER_TEMP": str(root / "temp"),
                   "XDG_CACHE_HOME": str(root / "cache"), "INPUT_NIGHTLY_TAG": TAG, "INPUT_CACHE": "true",
                   "GITHUB_OUTPUT": str(files["output"]), "GITHUB_PATH": str(files["path"]), "GITHUB_ENV": str(files["env"])}
            selected = dict(selection_for(), platform="linux_x86_64")
            with patch.dict(os.environ, env, clear=True), patch.object(nightly, "resolve", lambda env: selected):
                nightly.main(prepare=True)
            prepared = dict(line.split("=", 1) for line in files["output"].read_text().splitlines())
            sha = hashlib.sha256(GOOD).hexdigest()
            self.assertEqual(prepared["cache-key"], f"roc-nightly-v1-linux_x86_64-{sha}")
            self.assertEqual(prepared["store"], str(root / "cache/roc-automation/roc-nightly/v1/sha256" / sha))
            install_env = env | {"GITHUB_OUTPUT": str(files["install-output"])} | {
                "SELECTED_" + key.upper(): value for key, value in prepared.items() if key != "cache-key"}
            with patch.dict(os.environ, install_env, clear=True), patch.object(nightly, "fetch", serving(GOOD)):
                nightly.main(prepare=False)
            installed = dict(line.split("=", 1) for line in files["install-output"].read_text().splitlines())
            self.assertEqual(installed["cache-hit"], "false")
            self.assertEqual(files["path"].read_text(), installed["directory"] + "\n")
            self.assertTrue(Path(installed["roc"]).is_file())
            self.assertTrue(Path(installed["directory"]).is_relative_to(root / "temp"))

    def test_install_rechecks_what_prepare_selected(self):
        sha = hashlib.sha256(GOOD).hexdigest()
        good = {"SELECTED_TAG": TAG, "SELECTED_PLATFORM": "linux_x86_64", "SELECTED_ASSET": ASSET,
                "SELECTED_SIZE": str(len(GOOD)), "SELECTED_SHA256": sha, "SELECTED_URL": URL,
                "SELECTED_DIRECTORY": TOP, "SELECTED_EXTENSION": "tar.gz", "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64"}
        self.assertEqual(nightly.selection_from(good)["size"], len(GOOD))
        for key, value in (("SELECTED_URL", "https://example.com/" + ASSET), ("SELECTED_SIZE", "-1"),
                           ("SELECTED_DIRECTORY", "../" + TOP), ("SELECTED_SHA256", "0" * 63),
                           ("SELECTED_PLATFORM", "linux_arm64"), ("SELECTED_TAG", "latest")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                nightly.selection_from(good | {key: value})


class Wiring(unittest.TestCase):
    def test_every_compiler_install_in_this_repository_uses_the_verified_installer(self):
        sources = [*ROOT.glob(".github/workflows/*.yml"), *ROOT.glob("actions/*/action.yml")]
        for source in sources:
            with self.subTest(source=source.name):
                self.assertNotIn("roc-lang/setup-roc@", source.read_text())

    def test_cache_is_restored_before_admission_and_saved_straight_after_it(self):
        action = (ACTION / "action.yml").read_text()
        restore = action.index("uses: actions/cache/restore@")
        install = action.index("    - id: install\n")
        save = action.index("uses: actions/cache/save@")
        self.assertLess(restore, install)
        self.assertLess(install, save)
        pins = set(re.findall(r"uses: actions/cache/(?:restore|save)@([0-9a-f]{40})", action))
        self.assertEqual(len(pins), 1)
        self.assertNotIn("uses: actions/cache@", action)
        # The token reaches only the metadata step.
        self.assertEqual(action.count("inputs.token"), 1)
        self.assertLess(action.index("inputs.token"), restore)


if __name__ == "__main__":
    unittest.main()
