import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sbom_sidecars", ROOT / "actions/sbom-sidecars/sbom_sidecars.py")
sidecars = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sidecars)

TOOL = "https://github.com/lukewilliamboswell/roc-blueprint/releases/download/0.7.0/roc-blueprint-sbom-2pLqc6Kr6KE3fw5JE4z17TMnhKYP3npqDMBTVm9K2oKP.tar.zst"
COMPILER = "nightly-2026-10-21-5349569"
PLATFORM = "https://github.com/lukewilliamboswell/roc-blueprint/releases/download/0.7.0/2zwBq8cAkEPfRNMaWgtwCsVwxSJ3z9FncazjNofBXspG.tar.zst"

# A pretend tool: every subcommand records its arguments, and `bind` and
# `check` behave as the real tool's filesystem contract says they do.
FAKE_ROC = r'''#!/usr/bin/env python3
import json, os, sys, hashlib
log = open(os.environ["TOOL_LOG"], "a")
args = sys.argv[1:]
log.write(json.dumps({"cwd": os.getcwd(), "args": args}) + "\n")
if args == ["version"]:
    print(os.environ.get("FAKE_ROC_VERSION", "Roc compiler version nightly-2026-10-21-5349569")); sys.exit(0)
assert args[:2] == ["run", os.environ["EXPECTED_TOOL"]] and args[2] == "--", args
command, rest = args[3], args[4:]
if command == "inspect":
    spec_path, lock, composition, target, output = rest
    comp = json.load(open(composition))
    if os.environ.get("INSPECT_REFUSES"):
        sys.stderr.write("SBOM: Invalid(\"" + os.environ["INSPECT_REFUSES"] + "\")\n"); sys.exit(1)
    with open(output, "x") as out:
        json.dump({"target": target, "coverage": comp.get("coverage", []) + [{"scope": "sbom:" + e["component"], "status": "incomplete", "reason": "No supplier SBOM: " + e["reason"], "contact": e["contact"]} for e in comp.get("unavailable_sboms", [])], "imports": [e["id"] for e in comp.get("imported_documents", [])]}, out)
    print(output); sys.exit(0)
if command == "bind":
    inventory, subject, context, artifact = rest[:4]
    subject = json.load(open(subject)); context = json.load(open(context))
    assert os.path.basename(artifact) == subject["filename"], "subject filename differs from final artifact"
    digest = hashlib.sha256(open(artifact, "rb").read()).hexdigest()
    outputs = [(artifact + ".spdx.json", "SPDX-3.0.1"), (artifact + ".cdx.json", "CycloneDX-1.7")] if len(rest) == 4 else [(rest[4], {"--spdx-2.3": "SPDX-2.3", "--cyclonedx": "CycloneDX-1.7"}.get(rest[5] if len(rest) > 5 else "", "SPDX-3.0.1"))]
    for path, fmt in outputs:
        with open(path, "x") as out:
            json.dump({"format": fmt, "subject": {**subject, "sha256": digest}, "context": context, "inventory": os.path.basename(inventory)}, out)
        print(path)
    sys.exit(0)
if command == "check":
    options = {"--artifacts": "", "--inventory": ""}
    while rest and rest[0] in options:
        options[rest[0]] = rest[1]; rest = rest[2:]
    for document in rest:
        doc = json.load(open(document))
        actual = hashlib.sha256(open(os.path.join(options["--artifacts"], doc["subject"]["filename"]), "rb").read()).hexdigest()
        assert actual == doc["subject"]["sha256"], document + ": subject bytes differ"
        print(document + ": " + doc["format"] + " sidecar of " + doc["subject"]["filename"] + " checks")
    sys.exit(0)
sys.exit(2)
'''

FAKE_BLUEPRINT = "#!/bin/sh\n[ \"$1\" = spec ] && { printf '(spec (name example))\\n'; exit 0; }\nexit 3\n"


def composition(**overrides):
    base = {
        "profile": "blueprint-spdx-3.0-v1",
        "components": [
            {"id": "dep", "name": "dep-abc.tar.zst", "version": "1.2.0", "producer": "Organization: Example", "license": "MIT", "license_url": "", "identity": "", "download": "", "sha256": "", "modified": "", "target": "", "origin": "source:dep"},
            {"id": "compiler", "name": "roc", "version": "nightly", "producer": "Organization: Roc", "license": "UPL-1.0", "license_url": "", "identity": "https://example.org/roc", "download": "https://example.org/roc.tar.gz", "sha256": "", "modified": "", "target": "", "origin": "reviewed"},
        ],
        "relationships": [],
        "coverage": [{"scope": "native-closure", "status": "incomplete", "reason": "unlisted", "contact": "https://example.org/security"}],
        "inputs": [],
        "imported_documents": [],
        "unavailable_sboms": [{"component": "dep", "reason": "The supplier publishes no SBOM", "contact": "https://example.org/security"}],
    }
    base.update(overrides)
    return base


class PureTests(unittest.TestCase):
    def test_recorded_tools_are_well_formed_and_cover_the_releases_that_ship_a_tool(self):
        tools = json.loads(sidecars.SBOM_TOOLS.read_text())
        self.assertIn("0.7.0", tools)
        for version in tools:
            with self.subTest(version=version):
                entry = sidecars.sbom_tool(version)
                self.assertTrue(entry["url"].startswith("https://github.com/lukewilliamboswell/roc-blueprint/releases/download/" + version + "/roc-blueprint-sbom-"))
                self.assertEqual(entry["generator"], f"Tool: blueprint-sbom-{version}")

    def test_an_unrecorded_release_is_refused_with_the_remedy(self):
        with self.assertRaisesRegex(ValueError, "records no SBOM tool for roc-blueprint 0.6.0"):
            sidecars.sbom_tool("0.6.0")

    def test_a_tool_record_that_could_redirect_the_download_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            for url in ("https://example.org/tool.tar.zst", "http://github.com/lukewilliamboswell/roc-blueprint/releases/download/0.7.0/roc-blueprint-sbom-abc.tar.zst", TOOL + "?x=1", TOOL.replace("0.7.0/", "0.7.0/../")):
                path = Path(temporary) / "tools.json"
                path.write_text(json.dumps({"0.7.0": {"url": url, "compiler": COMPILER, "generator": "Tool: blueprint-sbom-0.7.0"}}))
                with self.subTest(url=url), self.assertRaisesRegex(ValueError, "not an immutable roc-blueprint release bundle"):
                    sidecars.sbom_tool("0.7.0", path)
            path.write_text(json.dumps({"0.7.0": {"url": TOOL, "compiler": "latest", "generator": "Tool: blueprint-sbom-0.7.0"}}))
            with self.assertRaisesRegex(ValueError, "not a Roc release tag"):
                sidecars.sbom_tool("0.7.0", path)

    def test_the_compiler_must_be_the_release_the_tool_was_built_for(self):
        self.assertTrue(sidecars.compiler_matches(f"Roc compiler version {COMPILER}\n", COMPILER))
        self.assertTrue(sidecars.compiler_matches(f"{COMPILER}\n", COMPILER))
        for output in ("", "Roc compiler version nightly-2026-10-20-7a8a800\n", f"roc {COMPILER}-dirty\n", f"other\nRoc compiler version {COMPILER}\n"):
            with self.subTest(output=output):
                self.assertFalse(sidecars.compiler_matches(output, COMPILER))

    def test_release_files_are_an_explicit_bounded_list_of_regular_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "dist").mkdir()
            (workspace / "dist/app").write_bytes(b"app")
            (workspace / "dist/bundle.tar.zst").write_bytes(b"bundle")
            (workspace / "outside").write_bytes(b"outside")
            (workspace / "dist/link").symlink_to(workspace / "dist/app")
            (workspace / "dist/app.spdx.json").write_text("{}")
            self.assertEqual([p.name for p in sidecars.release_files("dist/app\n\ndist/bundle.tar.zst\n", workspace)], ["app", "bundle.tar.zst"])
            for listing, reason in (
                ("", "at least one"),
                ("dist/*", "glob"),
                ("dist/app?", "glob"),
                ("-dist/app", "glob"),
                (str(workspace / "dist/app"), "relative"),
                ("dist/../outside", "relative"),
                ("dist/missing", "missing or not a regular"),
                ("dist/link", "missing or not a regular"),
                ("dist", "missing or not a regular"),
                ("dist/app.spdx.json", "not a release subject"),
                ("dist/app\ndist/app", "share the name"),
                ("dist/app\x00", "glob"),
            ):
                with self.subTest(listing=listing), self.assertRaisesRegex(ValueError, reason):
                    sidecars.release_files(listing, workspace)
            with self.assertRaisesRegex(ValueError, "more than"):
                sidecars.release_files("\n".join(["dist/app"] * (sidecars.MAX_FILES + 1)), workspace)

    def test_a_sidecar_left_from_an_earlier_build_is_stale_and_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / "app"
            file.write_bytes(b"app")
            sidecars.refuse_stale_sidecars([file])
            (Path(temporary) / "app.spdx-2.3.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "already exists and would be stale"):
                sidecars.refuse_stale_sidecars([file])
            (Path(temporary) / "app.spdx-2.3.json").unlink()
            (Path(temporary) / "app.cdx.json").symlink_to(Path(temporary) / "elsewhere")
            with self.assertRaisesRegex(ValueError, "already exists and would be stale"):
                sidecars.refuse_stale_sidecars([file])

    def test_creation_time_is_the_commit_time_in_utc(self):
        self.assertEqual(sidecars.created_from_commit("1761100800\n"), "2025-10-22T02:40:00Z")
        for value in ("", "now", "-5", "1761100800.5", "1" * 13):
            with self.subTest(value=value), self.assertRaises(ValueError):
                sidecars.created_from_commit(value)

    def test_namespace_and_subject_follow_blueprints_own_release(self):
        context = sidecars.context_for("https://github.com/o/r/sbom", "a" * 40, "app", "2", "2026-10-21T00:00:00Z", "Organization: O", "Tool: blueprint-sbom-0.7.0")
        self.assertEqual(context["namespace"], "https://github.com/o/r/sbom/" + "a" * 40 + "/app/revision-2")
        self.assertEqual(context["revision"], "2")
        subject = sidecars.subject_for("app", "1.0.0", "x86_64-linux", "https://github.com/o/r/releases/download/1.0.0")
        self.assertEqual(subject, {"filename": "app", "version": "1.0.0", "target": "x86_64-linux", "sha256": "", "url": "https://github.com/o/r/releases/download/1.0.0/app"})
        for base in ("http://example.org", "https://example.org/x?y", "https://user@example.org", "https://example.org/a b", ""):
            with self.subTest(base=base), self.assertRaises(ValueError):
                sidecars.https_base(base, "url-base")
        self.assertEqual(sidecars.https_base("https://example.org/x/", "url-base"), "https://example.org/x")

    def test_imports_file_adds_documents_but_never_opt_outs(self):
        own = composition(imported_documents=[{"id": "first", "subject": {"component": "x"}}])
        self.assertEqual(sidecars.merge_imports(own, None, ""), own)
        generated = {"profile": "roc-automation-sbom-imports-v1", "imported_documents": [{"id": "dep-sbom", "subject": {"component": "dep"}}], "unavailable_sboms_stub": [{"component": "other", "reason": "REPLACE", "contact": "REPLACE"}]}
        merged = sidecars.merge_imports(own, generated, "sbom/imports.json")
        self.assertEqual([entry["id"] for entry in merged["imported_documents"]], ["first", "dep-sbom"])
        self.assertEqual(merged["unavailable_sboms"], own["unavailable_sboms"])
        with self.assertRaisesRegex(ValueError, "repeats an import identifier"):
            sidecars.merge_imports(own, {"profile": "roc-automation-sbom-imports-v1", "imported_documents": [{"id": "first"}]}, "sbom/imports.json")
        with self.assertRaisesRegex(ValueError, "not a roc-automation-sbom-imports-v1"):
            sidecars.merge_imports(own, {"imported_documents": []}, "sbom/imports.json")
        with self.assertRaisesRegex(ValueError, "identifier"):
            sidecars.merge_imports(own, {"profile": "roc-automation-sbom-imports-v1", "imported_documents": [{"id": "../x"}]}, "sbom/imports.json")

    def test_bounded_json_refuses_duplicate_keys_symlinks_and_oversize(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "c.json"
            path.write_text('{"a": 1, "a": 2}')
            with self.assertRaisesRegex(ValueError, "repeats the key 'a'"):
                sidecars.read_bounded_json(path, "composition")
            path.write_text("[]")
            with self.assertRaisesRegex(ValueError, "JSON object"):
                sidecars.read_bounded_json(path, "composition")
            path.write_text("{" + " " * sidecars.MAX_JSON_BYTES + "}")
            with self.assertRaisesRegex(ValueError, "1 MiB"):
                sidecars.read_bounded_json(path, "composition")
            link = Path(temporary) / "l.json"
            link.symlink_to(path)
            with self.assertRaisesRegex(ValueError, "regular file"):
                sidecars.read_bounded_json(link, "composition")

    def test_dependency_report_names_bound_unavailable_missing_and_incomplete(self):
        comp = composition(components=composition()["components"] + [{"id": "tool", "origin": "program:tool:x86_64-linux"}, {"id": "lib", "origin": "source:lib"}], imported_documents=[{"id": "lib-sbom", "url": "https://e/l.spdx.json", "sha256": "f" * 64, "subject": {"component": "lib"}}])
        rows = sidecars.dependency_report(comp, {"coverage": [{"scope": "sbom:dep", "status": "incomplete", "reason": "No supplier SBOM: none", "contact": "https://e"}, {"scope": "x", "status": "complete"}]})
        self.assertEqual([(row["component"], row["state"]) for row in rows], [("dep", "unavailable"), ("tool", "missing"), ("lib", "bound"), ("sbom:dep", "incomplete")])


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.workspace = self.root / "repo"
        self.workspace.mkdir()
        (self.workspace / "Blueprint.roc").write_text(f'app [config] {{ pf: platform "{PLATFORM}" }}\n\nconfig = []\n')
        (self.workspace / "Blueprint.lock").write_text("(lock)\n")
        (self.workspace / "sbom").mkdir()
        (self.workspace / "sbom/composition.json").write_text(json.dumps(composition()))
        (self.workspace / "dist").mkdir()
        (self.workspace / "dist/app").write_bytes(b"final app bytes")
        (self.workspace / "dist/bundle.tar.zst").write_bytes(b"bundle bytes")
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "commit.gpgsign=false"]
        subprocess.run(git + ["init", "-q"], cwd=self.workspace, check=True)
        subprocess.run(git + ["add", "."], cwd=self.workspace, check=True)
        subprocess.run(git + ["commit", "-q", "-m", "fixture"], cwd=self.workspace, check=True, env={**os.environ, "GIT_COMMITTER_DATE": "2026-10-21T01:02:03Z", "GIT_AUTHOR_DATE": "2026-10-21T01:02:03Z"})
        self.commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.workspace, text=True).strip()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "roc").write_text(FAKE_ROC)
        (self.bin / "blueprint").write_text(FAKE_BLUEPRINT)
        for name in ("roc", "blueprint"):
            (self.bin / name).chmod(0o755)
        self.log = self.root / "tool.log"
        self.runner_temp = self.root / "runner-temp"
        self.runner_temp.mkdir()
        self.env = {
            "GITHUB_WORKSPACE": str(self.workspace), "RUNNER_TEMP": str(self.runner_temp), "GITHUB_OUTPUT": str(self.root / "outputs"),
            "GITHUB_STEP_SUMMARY": str(self.root / "summary.md"), "GITHUB_SHA": self.commit, "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_WORKFLOW_REF": "owner/repo/.github/workflows/release.yml@refs/heads/main", "GITHUB_RUN_ID": "77", "GITHUB_RUN_ATTEMPT": "2",
            "INPUT_FILES": "dist/app\ndist/bundle.tar.zst\n", "INPUT_VERSION": "1.2.0", "INPUT_TARGET": "x86_64-linux", "INPUT_AUTHOR": "Organization: Example Ltd",
            "INPUT_ROC_BINARY": str(self.bin / "roc"), "INPUT_BLUEPRINT_BINARY": str(self.bin / "blueprint"),
            "TOOL_LOG": str(self.log), "EXPECTED_TOOL": TOOL,
        }

    def run_action(self, **extra):
        with patch.dict(os.environ, {**self.env, **extra}, clear=False):
            sidecars.main()
        return self.outputs()

    def outputs(self):
        values, lines = {}, (self.root / "outputs").read_text().splitlines()
        index = 0
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

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_sidecars_are_bound_to_final_bytes_checked_and_listed_with_the_files(self):
        values = self.run_action()
        self.assertEqual(values["assets"].splitlines(), ["dist/app", "dist/app.cdx.json", "dist/app.spdx.json", "dist/bundle.tar.zst", "dist/bundle.tar.zst.cdx.json", "dist/bundle.tar.zst.spdx.json"])
        self.assertEqual(values["sidecars"].splitlines(), ["dist/app.cdx.json", "dist/app.spdx.json", "dist/bundle.tar.zst.cdx.json", "dist/bundle.tar.zst.spdx.json"])
        self.assertEqual(values["tool-url"], TOOL)
        self.assertEqual(values["compiler"], COMPILER)
        self.assertEqual(values["commit"], self.commit)
        self.assertEqual(values["created"], "2026-10-21T01:02:03Z")
        document = json.loads((self.workspace / "dist/app.spdx.json").read_text())
        self.assertEqual(document["subject"], {"filename": "app", "version": "1.2.0", "target": "x86_64-linux", "sha256": hashlib.sha256(b"final app bytes").hexdigest(), "url": "https://github.com/owner/repo/releases/download/1.2.0/app"})
        self.assertEqual(document["context"], {"namespace": f"https://github.com/owner/repo/sbom/{self.commit}/app/revision-1", "revision": "1", "created": "2026-10-21T01:02:03Z", "author": "Organization: Example Ltd", "generator": "Tool: blueprint-sbom-0.7.0"})
        bundle = json.loads((self.workspace / "dist/bundle.tar.zst.cdx.json").read_text())
        self.assertEqual(bundle["context"]["namespace"], f"https://github.com/owner/repo/sbom/{self.commit}/bundle.tar.zst/revision-1")
        commands = [call["args"][3] if call["args"][0] == "run" else call["args"][0] for call in self.calls()]
        self.assertEqual(commands, ["version", "inspect", "bind", "bind", "check"])
        inspect = self.calls()[1]["args"]
        self.assertEqual(inspect[7], "x86_64-linux")
        staging = Path(inspect[4]).parent
        self.assertEqual(staging.parent, self.runner_temp)
        self.assertEqual((staging / "Spec").read_text(), "(spec (name example))\n")
        self.assertEqual((staging / "Lock").read_text(), "(lock)\n")
        self.assertEqual(json.loads((staging / "composition.json").read_text()), composition())
        check = self.calls()[4]["args"]
        self.assertEqual(check[4:8], ["--inventory", str(staging / "inventory.json"), "--artifacts", str(self.workspace / "dist")])
        self.assertEqual(sorted(Path(p).name for p in check[8:]), ["app.cdx.json", "app.spdx.json", "bundle.tar.zst.cdx.json", "bundle.tar.zst.spdx.json"])
        digests = Path(values["digests"]).read_text().splitlines()
        self.assertEqual(len(digests), 4)
        self.assertTrue(all(line.endswith(("app.spdx.json", "app.cdx.json", "bundle.tar.zst.spdx.json", "bundle.tar.zst.cdx.json")) for line in digests))
        evidence = json.loads(Path(values["evidence"]).read_text())
        self.assertEqual(evidence["format"], "roc-automation-sbom-evidence-v1")
        self.assertEqual(evidence["source"], {"server": "https://github.com", "repository": "owner/repo", "commit": self.commit})
        self.assertEqual(evidence["producer"], {"workflow": "owner/repo/.github/workflows/release.yml@refs/heads/main", "run_id": "77", "run_attempt": "2"})
        self.assertEqual(evidence["generator"]["tool"], TOOL)
        self.assertEqual(evidence["generator"]["blueprint"], "0.7.0")
        self.assertEqual([subject["name"] for subject in evidence["subjects"]], ["app", "bundle.tar.zst"])
        app = evidence["subjects"][0]
        self.assertEqual((app["path"], app["size"], app["sha256"], app["target"], app["version"], app["url"]), ("dist/app", len(b"final app bytes"), hashlib.sha256(b"final app bytes").hexdigest(), "x86_64-linux", "1.2.0", "https://github.com/owner/repo/releases/download/1.2.0/app"))
        self.assertEqual([(sidecar["name"], sidecar["format"]) for sidecar in app["sidecars"]], [("app.spdx.json", "SPDX-3.0.1"), ("app.cdx.json", "CycloneDX-1.7")])
        for sidecar in app["sidecars"]:
            self.assertEqual(sidecar["sha256"], hashlib.sha256((self.workspace / sidecar["path"]).read_bytes()).hexdigest())
            self.assertEqual(sidecar["size"], (self.workspace / sidecar["path"]).stat().st_size)
        self.assertEqual([(row["component"], row["state"]) for row in evidence["dependencies"]], [("dep", "unavailable")])
        self.assertEqual([gap["scope"] for gap in evidence["coverage"]], ["native-closure", "sbom:dep"])
        summary = (self.root / "summary.md").read_text()
        self.assertIn("| `dep` | `source:dep` | unavailable | The supplier publishes no SBOM (contact https://example.org/security) |", summary)
        self.assertIn("| `sbom:dep` | `coverage` | incomplete |", summary)
        self.assertIn("| `app` | `app.spdx.json` |", summary)

    def test_spdx_2_3_is_an_extra_rendering_from_the_same_inputs(self):
        values = self.run_action(INPUT_SPDX_2_3="true", INPUT_FILES="dist/app")
        self.assertEqual(values["assets"].splitlines(), ["dist/app", "dist/app.cdx.json", "dist/app.spdx-2.3.json", "dist/app.spdx.json"])
        document = json.loads((self.workspace / "dist/app.spdx-2.3.json").read_text())
        self.assertEqual(document["format"], "SPDX-2.3")
        evidence = json.loads(Path(values["evidence"]).read_text())
        self.assertEqual([sidecar["format"] for sidecar in evidence["subjects"][0]["sidecars"]], ["SPDX-3.0.1", "CycloneDX-1.7", "SPDX-2.3"])
        binds = [call["args"] for call in self.calls() if call["args"][0] == "run" and call["args"][3] == "bind"]
        self.assertEqual(len(binds), 2)
        self.assertEqual(binds[0][4:7], binds[1][4:7])
        self.assertEqual(binds[1][-2:], [str(self.workspace / "dist/app.spdx-2.3.json"), "--spdx-2.3"])

    def test_imports_file_joins_the_composition_before_inspect(self):
        (self.workspace / "sbom/imports.json").write_text(json.dumps({"profile": "roc-automation-sbom-imports-v1", "imported_documents": [{"id": "lib-sbom", "subject": {"component": "lib"}}], "unavailable_sboms_stub": []}))
        self.run_action(INPUT_IMPORTS="sbom/imports.json", INPUT_FILES="dist/app")
        staged = json.loads((Path(self.calls()[1]["args"][6])).read_text())
        self.assertEqual([entry["id"] for entry in staged["imported_documents"]], ["lib-sbom"])
        self.assertEqual(staged["unavailable_sboms"], composition()["unavailable_sboms"])

    def test_a_refused_composition_stops_before_any_sidecar_and_points_at_the_fetch_action(self):
        with self.assertRaisesRegex(ValueError, "dependency dep .*has no supplier SBOM.*actions/fetch-dependency-sboms"):
            self.run_action(INSPECT_REFUSES="dependency dep (source:dep) has no supplier SBOM. Import one in imported_documents")
        self.assertEqual(sorted(p.name for p in (self.workspace / "dist").iterdir()), ["app", "bundle.tar.zst"])
        self.assertFalse((self.root / "outputs").exists())

    def test_wrong_compiler_is_refused_before_anything_runs(self):
        with self.assertRaisesRegex(ValueError, f"needs compiler {COMPILER}"):
            self.run_action(FAKE_ROC_VERSION="Roc compiler version nightly-2026-10-09-258ab27")
        self.assertEqual([call["args"] for call in self.calls()], [["version"]])
        self.assertEqual(list(self.runner_temp.iterdir()), [])

    def test_the_blueprint_release_must_record_a_tool(self):
        (self.workspace / "Blueprint.roc").write_text(f'app [config] {{ pf: platform "{PLATFORM.replace("0.7.0", "0.6.0")}" }}\n')
        with self.assertRaisesRegex(ValueError, "records no SBOM tool for roc-blueprint 0.6.0"):
            self.run_action()
        self.assertFalse(self.log.exists())

    def test_checkout_must_be_the_event_commit(self):
        with self.assertRaisesRegex(ValueError, "differs from the event commit"):
            self.run_action(GITHUB_SHA="b" * 40)
        self.assertFalse((self.root / "outputs").exists())

    def test_a_stale_sidecar_fails_before_the_tool_runs(self):
        (self.workspace / "dist/app.cdx.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "would be stale"):
            self.run_action()
        self.assertFalse(self.log.exists())

    def test_malformed_inputs_are_refused(self):
        for name, value, reason in (
            ("INPUT_VERSION", "v1.2.0", "version must be"),
            ("INPUT_TARGET", "linux", "target must be"),
            ("INPUT_AUTHOR", "Example Ltd", "author must be"),
            ("INPUT_REVISION", "0", "revision must be"),
            ("INPUT_SPDX_2_3", "yes", "spdx-2-3 must be"),
            ("INPUT_URL_BASE", "http://example.org", "url-base must be"),
            ("INPUT_NAMESPACE_BASE", "https://example.org/?x", "namespace-base must be"),
            ("INPUT_ROC_BINARY", str(self.root / "missing"), "roc-binary is not an executable"),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, reason):
                self.run_action(**{name: value})
        self.assertFalse(self.log.exists())
        self.assertFalse((self.root / "outputs").exists())

    def test_source_target_and_explicit_bases_are_passed_through(self):
        values = self.run_action(INPUT_TARGET="source", INPUT_FILES="dist/bundle.tar.zst", INPUT_URL_BASE="https://example.org/releases/1.2.0/", INPUT_NAMESPACE_BASE="https://example.org/sbom", INPUT_REVISION="3")
        document = json.loads((self.workspace / "dist/bundle.tar.zst.spdx.json").read_text())
        self.assertEqual(document["subject"]["target"], "source")
        self.assertEqual(document["subject"]["url"], "https://example.org/releases/1.2.0/bundle.tar.zst")
        self.assertEqual(document["context"]["namespace"], f"https://example.org/sbom/{self.commit}/bundle.tar.zst/revision-3")
        self.assertEqual(self.calls()[1]["args"][7], "source")
        self.assertEqual(values["assets"].splitlines(), ["dist/bundle.tar.zst", "dist/bundle.tar.zst.cdx.json", "dist/bundle.tar.zst.spdx.json"])


if __name__ == "__main__":
    unittest.main()
