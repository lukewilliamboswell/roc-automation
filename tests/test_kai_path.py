"""The Kai path: release selection, kai sbom sidecars, pins, evidence and wiring."""
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


setup_kai = load("setup_kai_under_test", "actions/setup-kai/setup_kai.py")
sidecars = load("sbom_sidecars_kai_under_test", "actions/sbom-sidecars/sbom_sidecars.py")
fetcher = load("fetch_dependency_sboms_kai_under_test", "actions/fetch-dependency-sboms/fetch_dependency_sboms.py")
sys.path.insert(0, str(ROOT / "actions/sbom-sidecars"))
verifier = load("verify_transferred_kai_under_test", "actions/sbom-sidecars/verify_transferred.py")

KAI = "0.0.12"
PLATFORM = f"https://github.com/thebrandonlucas/kai/releases/download/v{KAI}/G3CQ6Ed3h7vyvV4eDvwH1J8UqBv2wEtkb9jLJhFkU5ny.tar.zst"
PLUGIN = f"https://github.com/thebrandonlucas/kai/releases/download/v{KAI}/sbom-HzyZunZdK6VM3Ve3WK2cVtnFtRcTH6qUU6T65ZNG17TF.tar.zst"
RECORD = {"plugin": PLUGIN, "generator": f"Tool: kai-sbom-{KAI}", "features": ["members", "manifest"], "profile": "blueprint-spdx-3.0-v2"}


def kaifile(platform=PLATFORM, plugin=PLUGIN):
    return (
        "# Kaifile for a test; a comment naming https://example.org/x.tar.zst selects nothing.\n"
        "app [kaifile] {\n"
        f'\tpf: platform "{platform}",\n'
        f'\tstd: "https://github.com/thebrandonlucas/kai/releases/download/v{KAI}/std-6VuNBrdv6tT7ZAVD4bWGBYYwk1KJoXP7gCnFip5VSK11.tar.zst",\n'
        f'\tsbom: "{plugin}",\n'
        "}\n\nimport std.Std\n\nkaifile = Std.kaifile([Name(\"example\")])\n"
    )


def tools_file(directory: Path, **kai) -> Path:
    record = json.loads((ROOT / "actions/setup-blueprint/sbom-tools.json").read_text())
    record["kai"] = kai if kai else {KAI: RECORD}
    path = directory / "sbom-tools.json"
    path.write_text(json.dumps(record))
    return path


def tar_zst(path: Path, members: list[tuple[str, bytes]], kinds: dict[str, bytes] | None = None) -> None:
    """A .tar.zst of regular files, with `kinds` naming members of another tar type."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size, info.mtime = len(data), 0
            if kinds and name in kinds:
                info.type, info.size = kinds[name], 0
                archive.addfile(info)
            else:
                archive.addfile(info, io.BytesIO(data))
    subprocess.run(["zstd", "-q", "-o", str(path)], input=buffer.getvalue(), check=True)


class SetupKaiTests(unittest.TestCase):
    def test_the_release_is_the_platform_url_of_the_header(self):
        self.assertEqual(setup_kai.version_in(kaifile()), KAI)
        self.assertEqual(setup_kai.version_in(kaifile(PLATFORM.replace("v0.0.12", "v1.2.3-rc.1"))), "1.2.3-rc.1")
        self.assertEqual(setup_kai.plugin_url(kaifile(), "sbom"), PLUGIN)

    def test_a_path_or_other_url_names_no_release(self):
        for platform in ("../kai/platform/main.roc", PLATFORM.replace("thebrandonlucas/kai", "someone/kai"), PLATFORM.replace("/v0.0.12/", "/0.0.12/"), PLATFORM + "?x"):
            with self.subTest(platform=platform), self.assertRaisesRegex(ValueError, "not a Kai release bundle"):
                setup_kai.version_in(kaifile(platform))
        with self.assertRaisesRegex(ValueError, "no pf: platform"):
            setup_kai.version_in('app [kaifile] {\n\tstd: "x",\n}\n')
        with self.assertRaisesRegex(ValueError, "no app header"):
            setup_kai.version_in(f'# pf: platform "{PLATFORM}"\n')
        with self.assertRaisesRegex(ValueError, "names pf twice"):
            setup_kai.version_in(f'app [kaifile] {{\n\tpf: platform "{PLATFORM}",\n\tpf: platform "{PLATFORM}",\n}}\n')
        with self.assertRaisesRegex(ValueError, "does not name the sbom plugin"):
            setup_kai.plugin_url('app [kaifile] {\n\tpf: platform "x",\n}\n', "sbom")

    def test_only_the_header_counts(self):
        body = f'app [kaifile] {{\n\t# pf: platform "{PLATFORM}",\n}}\n\nx = {{ pf: platform "{PLATFORM}" }}\n'
        with self.assertRaisesRegex(ValueError, "no pf: platform"):
            setup_kai.version_in(body)

    def test_a_repository_with_blueprint_stays_on_blueprint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(setup_kai.project_tool(root), "blueprint")
            (root / "Kaifile.roc").write_text(kaifile())
            self.assertEqual(setup_kai.project_tool(root), "kai")
            (root / "Blueprint.roc").write_text("app [config] {}\n")
            self.assertEqual(setup_kai.project_tool(root), "blueprint")

    def test_the_action_writes_the_release_and_the_tool(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Kaifile.roc").write_text(kaifile())
            output = root / "output"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
                setup_kai.main(["version", str(root)])
                setup_kai.main(["tool", str(root)])
            self.assertEqual(output.read_text(), f"version={KAI}\ntool=kai\n")

    def test_the_committed_fixture_names_a_published_release(self):
        self.assertEqual(setup_kai.version_in((ROOT / "tests/fixtures/kai/Kaifile.roc").read_text()), "0.0.12")


class KaiToolRecordTests(unittest.TestCase):
    def test_a_recorded_plugin_is_the_release_bundle_and_generator(self):
        with tempfile.TemporaryDirectory() as temporary:
            tools = tools_file(Path(temporary))
            entry = sidecars.kai_sbom_tool(KAI, tools)
            self.assertEqual((entry["url"], entry["generator"], entry["compiler"], entry["profile"]), (PLUGIN, f"Tool: kai-sbom-{KAI}", "", "blueprint-spdx-3.0-v2"))
            with self.assertRaisesRegex(ValueError, "records no sbom plugin for Kai 0.0.13"):
                sidecars.kai_sbom_tool("0.0.13", tools)
            for field, value, reason in (
                ("plugin", PLUGIN.replace("v0.0.12", "v0.0.11"), "not an immutable Kai release bundle"),
                ("plugin", PLUGIN.replace("sbom-", "std-"), "not an immutable Kai release bundle"),
                ("generator", "Tool: blueprint-sbom-0.11.0", "must be Tool: kai-sbom-0.0.12"),
                ("features", ["members"], "must be members, manifest"),
                ("profile", "other", "unsupported"),
            ):
                with self.subTest(field=field):
                    bad = tools_file(Path(temporary), **{KAI: {**RECORD, field: value}})
                    with self.assertRaisesRegex(ValueError, reason):
                        sidecars.kai_sbom_tool(KAI, bad)

    def test_the_shipped_record_is_well_formed(self):
        record = json.loads(sidecars.SBOM_TOOLS.read_text())["kai"]
        for version in record:
            with self.subTest(version=version):
                self.assertEqual(sidecars.kai_sbom_tool(version)["generator"], f"Tool: kai-sbom-{version}")


@unittest.skipUnless(shutil.which("zstd"), "zstd lists .tar.zst bundles")
class ArchiveMembersTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_members_are_listed_in_archive_order_with_their_digests(self):
        bundle = self.root / "bundle.tar.zst"
        tar_zst(bundle, [("main.roc", b"main"), ("src/Lib.roc", b"lib"), ("empty", b"")])
        listing = sidecars.archive_members(bundle)
        self.assertEqual(listing["archive"], "bundle.tar.zst")
        self.assertEqual(listing["sha256"], hashlib.sha256(bundle.read_bytes()).hexdigest())
        self.assertEqual(listing["members"], [{"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in (("main.roc", b"main"), ("src/Lib.roc", b"lib"), ("empty", b""))])

    def test_only_regular_files_with_portable_unique_names_are_accepted(self):
        for members, kinds, reason in (
            ([("dir", b"")], {"dir": tarfile.DIRTYPE}, "not a plain regular file"),
            ([("link", b"")], {"link": tarfile.SYMTYPE}, "not a plain regular file"),
            ([("a b.roc", b"x")], None, "unsafe member name"),
            ([("../x.roc", b"x")], None, "unsafe member name"),
            ([("-x.roc", b"x")], None, "unsafe member name"),
            ([("a.roc", b"x"), ("a.roc", b"y")], None, "duplicate member"),
        ):
            with self.subTest(members=members):
                bundle = self.root / "bundle.tar.zst"
                bundle.unlink(missing_ok=True)
                tar_zst(bundle, members, kinds)
                with self.assertRaisesRegex(ValueError, reason):
                    sidecars.archive_members(bundle)
        broken = self.root / "broken.tar.zst"
        broken.write_bytes(b"not zstd")
        with self.assertRaisesRegex(ValueError, "zstd could not decompress|not a readable tar"):
            sidecars.archive_members(broken)


# A pretend kai: it records its arguments and keeps `kai sbom`'s file contract:
# it reads only the files named, never the artifact, and writes beside it.
FAKE_KAI = r'''#!/usr/bin/env python3
import hashlib, json, os, sys
args = sys.argv[1:]
open(os.environ["TOOL_LOG"], "a").write(json.dumps({"cwd": os.getcwd(), "args": args}) + "\n")
if args == ["--version"]:
    print(os.environ.get("FAKE_KAI_VERSION", "0.0.12")); sys.exit(0)
if args == ["sbom", "pins"]:
    sys.stdout.write(open(os.environ["LOCK_REPORT"]).read()); sys.exit(0)
assert args[0] == "sbom", args
command, rest = args[1], args[2:]
words = rest[rest.index("--") + 1:] if "--" in rest else []
options = {}
flags = []
head = rest[:rest.index("--")] if "--" in rest else rest
index = 0
while index < len(head):
    if head[index] in ("--spdx-2-3", "--cyclonedx"):
        flags.append(head[index]); index += 1
    else:
        options[head[index]] = head[index + 1]; index += 2
if command == "inspect":
    comp = json.load(open(options["--composition"]))
    if "--members" in options:
        listing = json.load(open(options["--members"]))
        stated = {c.get("sha256") for c in comp["components"]}
        for member in listing["members"]:
            if member["sha256"] not in stated:
                sys.stderr.write("kai sbom: the archive holds %s, which no component states\n" % member["name"]); sys.exit(1)
    if os.environ.get("INSPECT_REFUSES"):
        sys.stderr.write(os.environ["INSPECT_REFUSES"] + "\n"); sys.exit(1)
    with open(options["--output"], "x") as out:
        json.dump({"profile": "blueprint-spdx-3.0-v2", "target": words[0], "components": [c["id"] for c in comp["components"]], "coverage": comp.get("coverage", [])}, out)
    print(options["--output"]); sys.exit(0)
if command == "bind":
    subject = json.load(open(options["--subject"])); context = json.load(open(options["--context"]))
    artifact = options["--artifact"]
    assert os.path.basename(artifact) == subject["filename"]
    if "--output" in options:
        outputs = [(options["--output"], "SPDX-2.3" if "--spdx-2-3" in flags else "SPDX-3.0.1")]
    else:
        outputs = [(artifact + ".spdx.json", "SPDX-3.0.1"), (artifact + ".cdx.json", "CycloneDX-1.7")]
    for path, fmt in outputs:
        with open(path, "x") as out:
            json.dump({"format": fmt, "subject": subject, "context": context}, out)
        print(path)
    if "--output" not in options:
        listed = [{"filename": os.path.basename(p), "format": f.lower(), "sha256": hashlib.sha256(open(p, "rb").read()).hexdigest(), "size": os.path.getsize(p)} for p, f in outputs]
        stated = {key: subject[key] for key in ("filename", "version", "target", "sha256", "size")}
        if os.environ.get("FAKE_STATED_SHA256"):
            stated["sha256"] = os.environ["FAKE_STATED_SHA256"]
        with open(artifact + ".sbom-manifest.json", "x") as out:
            json.dump({"profile": "blueprint-spdx-3.0-v2", "subject": stated, "inventory_sha256": hashlib.sha256(open(options["--inventory"], "rb").read()).hexdigest(), "sidecars": listed}, out)
        print(artifact + ".sbom-manifest.json")
    sys.exit(0)
if command == "check":
    manifest = json.load(open(options["--manifest"]))
    assert manifest["inventory_sha256"] == hashlib.sha256(open(options["--inventory"], "rb").read()).hexdigest()
    for document in words:
        assert json.load(open(document))["subject"]["sha256"] == manifest["subject"]["sha256"], document
    sys.stderr.write("kai: kai sbom: %s itself was not read; compare its SHA-256 with %s\n" % (manifest["subject"]["filename"], manifest["subject"]["sha256"]))
    sys.exit(0)
sys.exit(2)
'''


def composition(**overrides):
    base = {
        "profile": "blueprint-spdx-3.0-v2",
        "components": [{"id": "dep", "name": "dep-abc.tar.zst", "version": "1.2.0", "producer": "Organization: Example", "license": "MIT", "license_url": "", "identity": "", "download": "", "sha256": "", "modified": "", "target": "", "origin": "source:dep"}],
        "relationships": [],
        "coverage": [],
        "inputs": [],
        "imported_documents": [],
        "unavailable_sboms": [{"component": "dep", "reason": "The supplier publishes no SBOM", "contact": "https://example.org/security"}],
    }
    base.update(overrides)
    return base


class KaiWorkspace(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "repo"
        (self.workspace / "sbom").mkdir(parents=True)
        (self.workspace / "Kaifile.roc").write_text(kaifile())
        (self.workspace / ".kai").mkdir()
        (self.workspace / ".kai/lock.json").write_text("{}\n")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "kai").write_text(FAKE_KAI)
        (self.bin / "kai").chmod(0o755)
        self.log = self.root / "kai.log"
        self.tools = tools_file(self.root)

    def calls(self):
        return [json.loads(line)["args"] for line in self.log.read_text().splitlines()] if self.log.exists() else []


@unittest.skipUnless(shutil.which("zstd") and shutil.which("git"), "the Kai path lists bundles with zstd and reads the commit with git")
class KaiSidecarsTests(KaiWorkspace):
    def setUp(self):
        super().setUp()
        (self.workspace / "sbom/composition.json").write_text(json.dumps(composition()))
        (self.workspace / "dist").mkdir()
        (self.workspace / "dist/app").write_bytes(b"final app bytes")
        self.members = [("main.roc", b"main"), ("Lib.roc", b"lib")]
        tar_zst(self.workspace / "dist/bundle.tar.zst", self.members)
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "commit.gpgsign=false"]
        subprocess.run(git + ["init", "-q"], cwd=self.workspace, check=True)
        subprocess.run(git + ["add", "."], cwd=self.workspace, check=True)
        subprocess.run(git + ["commit", "-q", "-m", "fixture"], cwd=self.workspace, check=True, env={**os.environ, "GIT_COMMITTER_DATE": "2026-10-21T01:02:03Z", "GIT_AUTHOR_DATE": "2026-10-21T01:02:03Z"})
        self.commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.workspace, text=True).strip()
        self.runner_temp = self.root / "runner-temp"
        self.runner_temp.mkdir()
        self.env = {
            "GITHUB_WORKSPACE": str(self.workspace), "RUNNER_TEMP": str(self.runner_temp), "GITHUB_OUTPUT": str(self.root / "outputs"),
            "GITHUB_STEP_SUMMARY": str(self.root / "summary.md"), "GITHUB_SHA": self.commit, "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_WORKFLOW_REF": "owner/repo/.github/workflows/release.yml@refs/heads/main", "GITHUB_RUN_ID": "77", "GITHUB_RUN_ATTEMPT": "2",
            "INPUT_FILES": "dist/app\ndist/bundle.tar.zst\n", "INPUT_VERSION": "1.2.0", "INPUT_TARGET": "source", "INPUT_AUTHOR": "Organization: Example Ltd",
            "INPUT_KAI_BINARY": str(self.bin / "kai"), "INPUT_BLUEPRINT_BINARY": str(self.root / "no-blueprint"), "TOOL_LOG": str(self.log),
        }

    def run_action(self, **extra):
        # Every member is a reviewed component, as a prepare-sbom task writes them.
        stated = [{**composition()["components"][0], "id": f"member-{index}", "name": name, "sha256": hashlib.sha256(data).hexdigest(), "origin": "reviewed"} for index, (name, data) in enumerate(self.members)]
        (self.workspace / "sbom/composition.json").write_text(json.dumps(composition(components=composition()["components"] + stated)))
        with patch.object(sidecars, "SBOM_TOOLS", self.tools), patch.dict(os.environ, {**self.env, **extra}, clear=False):
            sidecars.main()
        lines = (self.root / "outputs").read_text().splitlines()
        values, index = {}, 0
        while index < len(lines):
            if "<<" in lines[index] and "=" not in lines[index].split("<<", 1)[0]:
                key, delimiter = lines[index].split("<<", 1)
                index, collected = index + 1, []
                while lines[index] != delimiter:
                    collected.append(lines[index])
                    index += 1
                values[key] = "\n".join(collected)
            else:
                key, _, value = lines[index].partition("=")
                values[key] = value
            index += 1
        return values

    def test_kai_sbom_inspects_binds_and_checks_each_file_with_its_bytes_stated(self):
        values = self.run_action(INPUT_SPDX_2_3="true", INPUT_ARCHIVE_MEMBERS="reviewed")
        names = [f"dist/{file}{suffix}" for file in ("app", "bundle.tar.zst") for suffix in ("", ".cdx.json", ".sbom-manifest.json", ".spdx-2.3.json", ".spdx.json")]
        self.assertEqual(values["assets"].splitlines(), sorted(names))
        self.assertEqual((values["tool-url"], values["tool-sha256"], values["compiler"]), (PLUGIN, "", ""))
        calls = self.calls()
        self.assertEqual([call[1] if call[0] == "sbom" else call[0] for call in calls], ["--version", "inspect", "bind", "bind", "inspect", "bind", "bind", "check", "check"])
        staging = Path(calls[1][3]).parent
        self.assertEqual(staging.parent, self.runner_temp)
        self.assertEqual(calls[1], ["sbom", "inspect", "--composition", str(staging / "composition.json"), "--output", str(staging / "inventory.json"), "--", "source"])
        listing = staging / "subject-1/members.json"
        self.assertEqual(calls[4], ["sbom", "inspect", "--composition", str(staging / "subject-1/composition.json"), "--output", str(staging / "subject-1/inventory.json"), "--members", str(listing), "--", "source"])
        self.assertEqual(json.loads(listing.read_text())["members"], [{"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in self.members])
        bundle = self.workspace / "dist/bundle.tar.zst"
        self.assertEqual(calls[5], ["sbom", "bind", "--inventory", str(staging / "subject-1/inventory.json"), "--subject", str(staging / "subject-1/subject.json"), "--context", str(staging / "subject-1/context.json"), "--artifact", str(bundle)])
        self.assertEqual(calls[6], calls[5] + ["--output", str(bundle) + ".spdx-2.3.json", "--spdx-2-3"])
        self.assertEqual(calls[8], ["sbom", "check", "--manifest", str(bundle) + ".sbom-manifest.json", "--inventory", str(staging / "subject-1/inventory.json"), "--", str(bundle) + ".spdx-2.3.json"])
        self.assertTrue(all(json.loads(line)["cwd"] == str(self.workspace) for line in self.log.read_text().splitlines()))
        subject = json.loads((staging / "subject-1/subject.json").read_text())
        self.assertEqual(subject, {"filename": "bundle.tar.zst", "version": "1.2.0", "target": "source", "sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(), "size": bundle.stat().st_size, "url": "https://github.com/owner/repo/releases/download/1.2.0/bundle.tar.zst"})
        context = json.loads((staging / "subject-1/context.json").read_text())
        self.assertEqual(context["generator"], f"Tool: kai-sbom-{KAI}")
        evidence = json.loads(Path(values["evidence"]).read_text())
        self.assertEqual(evidence["generator"], {"tool": PLUGIN, "tool_sha256": "", "generator": f"Tool: kai-sbom-{KAI}", "kai": KAI, "created": "2026-10-21T01:02:03Z"})
        self.assertEqual([sidecar["format"] for sidecar in evidence["subjects"][1]["sidecars"]], ["SPDX-3.0.1", "CycloneDX-1.7", "SPDX-2.3", "manifest"])
        self.assertIn("built by kai", (self.root / "summary.md").read_text())
        # The privileged recheck admits the Kai generator by the reviewed record.
        with patch.object(verifier.sbom_sidecars, "kai_sbom_tool", lambda version: sidecars.kai_sbom_tool(version, self.tools)):
            self.assertEqual(verifier.check_generator(evidence["generator"])["url"], PLUGIN)

    def test_generated_members_need_no_reviewed_components(self):
        (self.workspace / "sbom/composition.json").write_text(json.dumps(composition()))
        with patch.object(sidecars, "SBOM_TOOLS", self.tools), patch.dict(os.environ, {**self.env, "INPUT_FILES": "dist/bundle.tar.zst"}, clear=False):
            sidecars.main()
        staged = json.loads(next(self.runner_temp.glob("*/subject-0/composition.json")).read_text())
        self.assertEqual(sorted(c["name"] for c in staged["components"] if c["id"].startswith("member-")), ["Lib.roc", "main.roc"])

    def test_the_file_must_be_the_bytes_the_manifest_states(self):
        with self.assertRaisesRegex(ValueError, "differs from the subject its manifest states"):
            self.run_action(INPUT_FILES="dist/app", FAKE_STATED_SHA256="0" * 64)
        self.assertFalse((self.root / "outputs").exists())

    def test_the_kaifile_must_name_the_reviewed_plugin_of_its_release(self):
        (self.workspace / "Kaifile.roc").write_text(kaifile(plugin=PLUGIN.replace("HzyZ", "JzyZ")))
        with self.assertRaisesRegex(ValueError, "names the sbom plugin .*reviewed sbom plugin is"):
            self.run_action()
        self.assertEqual(self.calls(), [])
        (self.workspace / "Kaifile.roc").write_text(kaifile(platform=PLATFORM.replace("v0.0.12", "v0.0.13")))
        with self.assertRaisesRegex(ValueError, "records no sbom plugin for Kai 0.0.13"):
            self.run_action()

    def test_the_installed_kai_must_be_the_release_the_kaifile_names(self):
        with self.assertRaisesRegex(ValueError, "reports '0.0.11', but Kaifile.roc names Kai 0.0.12"):
            self.run_action(FAKE_KAI_VERSION="0.0.11")
        self.assertEqual(self.calls(), [["--version"]])
        self.assertEqual(list(self.runner_temp.iterdir()), [])

    def test_a_refused_composition_points_at_the_fetch_action(self):
        with self.assertRaisesRegex(ValueError, "inspect refused .*has no supplier SBOM.*fetch-dependency-sboms"):
            self.run_action(INSPECT_REFUSES="kai sbom: dependency dep has no supplier SBOM")
        self.assertEqual(sorted(p.name for p in (self.workspace / "dist").iterdir()), ["app", "bundle.tar.zst"])

    def test_a_missing_kai_is_refused(self):
        with self.assertRaisesRegex(ValueError, "kai-binary is not an executable"):
            self.run_action(INPUT_KAI_BINARY=str(self.root / "missing"))

    def test_blueprint_roc_keeps_the_blueprint_path(self):
        (self.workspace / "Blueprint.roc").write_text("app [config] {}\n")
        with self.assertRaisesRegex(ValueError, "blueprint-binary is not an executable"):
            self.run_action()
        self.assertEqual(self.calls(), [])


class KaiGeneratorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        tools = tools_file(Path(temporary.name))
        reviewed = sidecars.kai_sbom_tool
        patcher = patch.object(verifier.sbom_sidecars, "kai_sbom_tool", lambda version: reviewed(version, tools))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.generator = {"tool": PLUGIN, "tool_sha256": "", "generator": f"Tool: kai-sbom-{KAI}", "kai": KAI, "created": "2026-10-21T01:02:03Z"}

    def test_the_reviewed_plugin_is_accepted_and_changes_are_refused(self):
        self.assertEqual(verifier.check_generator(self.generator)["profile"], "blueprint-spdx-3.0-v2")
        for change, reason in (
            ({"tool": PLUGIN.replace("HzyZ", "JzyZ")}, "differs from the reviewed identity"),
            ({"tool_sha256": "a" * 64}, "differs from the reviewed identity"),
            ({"generator": "Tool: blueprint-sbom-0.11.0"}, "differs from the reviewed Kai sbom plugin"),
            ({"kai": "0.0.13"}, "records no sbom plugin for Kai 0.0.13"),
            ({"created": "2026-10-21 01:02:03"}, "must be UTC"),
            ({"compiler": ""}, "unsupported generator record"),
            ({"blueprint": "0.11.0"}, "unsupported generator record"),
        ):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, reason):
                verifier.check_generator({**self.generator, **change})


class KaiPinsTests(KaiWorkspace):
    def test_pins_come_from_kai_sbom_pins(self):
        url = "https://example.org/releases/1.2.0/dep-abc.tar.zst"
        digest = "3945bb64314f2f25732bb36f3bc7f52a2087cfde17439086cf3acd785e3fdfca"
        report = {"format": 2, "sources": {"dep": {"name": "dep", "provider": "file", "ref": url, "rev": "", "digest": "sha256:" + digest, "digestKind": "file", "flatSha256": digest, "content_identity": "sha256:" + digest}}, "fetched": []}
        (self.root / "pins.json").write_text(json.dumps(report))
        (self.workspace / "sbom/composition.json").write_text(json.dumps(composition()))
        requests = []

        def network(address):
            requests.append(address)
            return None

        env = {"GITHUB_WORKSPACE": str(self.workspace), "GITHUB_OUTPUT": str(self.root / "outputs"), "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "owner/repo",
               "INPUT_TARGET": "source", "INPUT_KAI_BINARY": str(self.bin / "kai"), "LOCK_REPORT": str(self.root / "pins.json"), "TOOL_LOG": str(self.log)}
        with patch.dict(os.environ, env, clear=False):
            fetcher.main(download=network)
        self.assertEqual(self.calls(), [["sbom", "pins"]])
        self.assertEqual(requests, [url + ".spdx.json", url + ".cdx.json"])
        outputs = dict(line.split("=", 1) for line in (self.root / "outputs").read_text().splitlines())
        self.assertEqual((outputs["fetched"], outputs["missing"]), ("0", "0"))
        with patch.dict(os.environ, {**env, "INPUT_KAI_BINARY": str(self.root / "missing")}, clear=False), self.assertRaisesRegex(ValueError, "kai-binary is not an executable"):
            fetcher.main(download=network)


@unittest.skipUnless((ROOT / ".github/workflows/release-package.yml").is_file(), "workflow source is not part of the standalone artifact")
class WiringTests(unittest.TestCase):
    def test_release_package_selects_one_tool_and_runs_every_task_with_it(self):
        workflow = (ROOT / ".github/workflows/release-package.yml").read_text()
        for job in ("build", "test"):
            with self.subTest(job=job):
                body = workflow.split(f"\n  {job}:\n", 1)[1].split("\n  test:\n" if job == "build" else "\n  publish:\n", 1)[0]
                self.assertIn("run: python3 .roc-automation/actions/setup-kai/setup_kai.py tool .", body)
                self.assertIn("if: steps.tool.outputs.tool == 'blueprint'\n        uses: ./.roc-automation/actions/setup-blueprint", body)
                self.assertIn("if: steps.tool.outputs.tool == 'kai'\n        uses: ./.roc-automation/actions/setup-kai", body)
                self.assertNotIn("blueprint run", body)
                runs = re.findall(r"run: '\"\$TOOL\" run [^\n]*", body)
                self.assertEqual(len(runs), 6 if job == "build" else 1)
                self.assertEqual(body.count("TOOL: ${{ steps.tool.outputs.tool }}"), len(runs))
        self.assertIn("uses: ./.roc-automation/actions/kai-tool", workflow)
        self.assertIn("renderer-binary: ${{ steps.blueprint-renderer.outputs.binary || steps.kai-renderer.outputs.binary }}", workflow)
        inputs = workflow.split("    inputs:\n", 1)[1].split("    outputs:\n", 1)[0]
        self.assertNotIn("required: true", inputs)

    def test_kai_tool_reuses_the_blueprint_tool_script_with_kai(self):
        action = (ROOT / "actions/kai-tool/action.yml").read_text()
        self.assertIn("INPUT_BLUEPRINT_BINARY: ${{ inputs.kai-binary || 'kai' }}", action)
        self.assertIn("TOOL_SCRIPT: ${{ github.action_path }}/../blueprint-tool/blueprint_tool.py", action)
        tool = (ROOT / "actions/blueprint-tool/action.yml").read_text()
        for output in re.findall(r"^  ([a-z-]+):\n    description", tool.split("outputs:\n", 1)[1], re.MULTILINE):
            self.assertIn(f"  {output}:\n", action.split("outputs:\n", 1)[1])

    def test_setup_kai_pins_kais_action_by_full_commit(self):
        action = (ROOT / "actions/setup-kai/action.yml").read_text()
        self.assertRegex(action, r"uses: thebrandonlucas/kai/\.github/actions/setup-kai@[0-9a-f]{40}\n")
        self.assertIn("version: ${{ steps.version.outputs.version }}", action)

    def test_nightly_compatibility_installs_kai_on_request(self):
        workflow = (ROOT / ".github/workflows/nightly-compatibility.yml").read_text()
        self.assertIn("install-kai: ${{ inputs.install-kai }}", workflow)
        action = (ROOT / "actions/release-compatibility/action.yml").read_text()
        self.assertIn("if: inputs.phase == 'test' && inputs.install-kai == 'true'\n      uses: $/actions/setup-kai", action)


if __name__ == "__main__":
    unittest.main()
