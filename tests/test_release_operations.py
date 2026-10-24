"""Run shared package operations as a native executable with traced tool substitutes."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BINARY = os.environ.get("ROC_RELEASE_OPERATIONS_BINARY")
PLANNER = os.environ.get("ROC_RELEASE_PLAN_BINARY")


@unittest.skipUnless(BINARY and PLANNER, "set ROC_RELEASE_OPERATIONS_BINARY and ROC_RELEASE_PLAN_BINARY")
class ReleaseOperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        for directory in ("package", "examples", "docs/releases", "bin"):
            (self.workspace / directory).mkdir(parents=True)
        (self.workspace / "package/main.roc").write_text("package []\n")
        (self.workspace / "examples/main.roc").write_text('app [main!] { pf: platform "../package/main.roc" }\n')
        (self.workspace / "examples/data.txt").write_bytes(b"fixture\r\nexact")
        self.spec = dict(schema_version=1, release="working-tree", dependencies=[], apps=[dict(path="examples/main.roc", cases=[dict(name="hello", args=[], stdin="", stdout="hello\n", stderr="", exit_code=0)])])
        (self.workspace / "examples/spec.json").write_text(json.dumps(self.spec))
        (self.workspace / "docs/releases/1.2.0.adoc").write_text("= sample 1.2.0\n\nAuthored notes. Install {package-url}.\n")
        self.program("roc", '''import os,sys,json,urllib.request
from pathlib import Path
assert not os.environ.get('GH_TOKEN') and not os.environ.get('GITHUB_TOKEN')
args=sys.argv[1:]
with open(os.environ['TOOL_TRACE'],'a') as out: out.write(json.dumps(args)+'\\n')
if args==['version']: print('Roc compiler version nightly-2026-10-23-18478ff')
elif args[0]=='bundle':
    out=Path(args[args.index('--output-dir')+1]);out.mkdir(exist_ok=True)
    (out/('a'*43+'.tar.zst')).write_bytes(b'candidate bundle')
elif args[0]=='unbundle': pass
elif args[0] in ('check','build'):
    for i,arg in enumerate(args):
        if arg=='--replace-dep':
            assert urllib.request.urlopen(args[i+2]).read()==b'candidate bundle'
    if args[0]=='build':
        path=Path(next(arg.split('=',1)[1] for arg in args if arg.startswith('--output=')))
        path.write_text('#!'+sys.executable+'\\nprint("hello")\\n');path.chmod(0o755)
elif args[0] in ('fmt','bump'): pass
else: raise AssertionError(args)
''')
        self.program("blueprint", "import os,sys,json\nassert sys.argv[1:]==['run','check-unit']\nassert not os.environ.get('GH_TOKEN')\nif os.environ.get('TASK_CONTEXT'): open(os.environ['TASK_CONTEXT'],'w').write(json.dumps({name:os.environ[name] for name in ('RO_TARGET','RO_TARGET_IDS','RO_OUTPUTS','RO_COMPILER','RO_OUTPUT_DIRECTORY')}))\n")
        self.program("roc-policy", "import sys\nassert sys.argv[1]=='check'\n")
        self.program("roc-docs", '''import sys
from pathlib import Path
args=sys.argv[1:]
import os,json
with open(os.environ['TOOL_TRACE'],'a') as log: log.write(json.dumps(['render']+args)+'\\n')
out=Path(args[args.index('--workspace')+1])/args[args.index('--output-directory')+1];out.mkdir()
source=(Path(args[args.index('--workspace')+1])/args[args.index('--docs-directory')+1]/args[args.index('--entrypoint')+1]).read_text()
(out/'site').mkdir()
(out/'site/index.md').write_text(source.partition('\\n')[2])
(out/'site/index.html').write_text('<p>Manual</p>')
(out/'manual.pdf').write_bytes(b'%PDF fixture')
''')
        subprocess.run(["git", "init", "-q"], cwd=self.workspace, check=True)
        subprocess.run(["git", "add", "."], cwd=self.workspace, check=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"], cwd=self.workspace, check=True)
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.workspace, text=True).strip()
        self.context = dict(event="pull_request", ref="refs/pull/1/merge", default_branch="main", repository="owner/sample", sha=sha, run_id="100", run_attempt="1", compiler="nightly-2026-10-23-18478ff", version="", full_dry_run=True, topology_sha256="b"*64)
        self.topology = json.loads((ROOT / "tests/fixtures/lifecycle/topology-package.json").read_text())
        self.directory = self.root / "candidate"
        self.scratch = self.root / "evidence"
        self.trace = self.root / "tools.jsonl"
        self.env = {**os.environ, "GITHUB_WORKSPACE": str(self.workspace), "RO_WORKSPACE": str(self.workspace), "RO_DIRECTORY": str(self.scratch), "RO_CANDIDATE": str(self.directory), "RO_RUNNER": "ubuntu-24.04", "RO_COMPILER": str(self.workspace / "bin/roc"), "RO_BLUEPRINT": str(self.workspace / "bin/blueprint"), "RO_POLICY": str(self.workspace / "bin/roc-policy"), "RO_DOCS": str(self.workspace / "bin/roc-docs"), "RO_SELF": str(BINARY), "RO_TOOLING_COMPILER_VERSION": "Roc compiler version nightly-2026-10-09-258ab27", "RO_PLAN": str(self.root / "plan.json"), "RA_PYTHON": sys.executable, "RA_ARCHIVE_HELPER": str(ROOT / "actions/release-compatibility/archive.py"), "TOOL_TRACE": str(self.trace), "GH_TOKEN": "not-for-children", "GITHUB_TOKEN": "not-for-children", "GITHUB_OUTPUT": str(self.root / "outputs")}
        (self.root / "outputs").write_text("")
        self.make_plan()

    def program(self, name, source):
        path = self.workspace / "bin" / name
        path.write_text("#!"+sys.executable+"\n"+source)
        path.chmod(0o755)

    def make_plan(self):
        for name, value in (("context", self.context), ("topology", self.topology), ("changes", dict(complete=False, files=[]))):
            (self.root / (name+".json")).write_text(json.dumps(value))
        ran = subprocess.run([PLANNER, "plan", str(self.root / "context.json"), str(self.root / "topology.json"), str(self.root / "changes.json"), self.env["RO_PLAN"]], capture_output=True, text=True)
        self.assertEqual(ran.returncode, 0, ran.stderr)

    def run_stage(self, stage, **extra):
        return subprocess.run([BINARY, stage], env={**self.env, **extra}, capture_output=True, text=True, timeout=30)

    def test_prepare_builds_once_and_candidate_uses_unchanged_archive_and_fresh_cache(self):
        ran = self.run_stage("prepare")
        self.assertEqual(ran.returncode, 0, ran.stderr+ran.stdout)
        archive = self.directory / "sample-examples-0.0.1-dry-run.zip"
        before = archive.read_bytes()
        with zipfile.ZipFile(archive) as zipped:
            self.assertIsNone(zipped.testzip())
            root = "sample-examples-0.0.1-dry-run/"
            source = zipped.read(root+"examples/main.roc").decode()
            self.assertIn("releases/download/0.0.1-dry-run/sample-", source)
            self.assertEqual(zipped.read(root+"examples/data.txt"), b"fixture\r\nexact")
        ran = self.run_stage("candidate")
        self.assertEqual(ran.returncode, 0, ran.stderr+ran.stdout)
        self.assertEqual(archive.read_bytes(), before)
        calls = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertEqual(sum(call[0]=="bundle" for call in calls), 1)
        self.assertEqual(sum(call[0]=="build" for call in calls), 1)
        self.assertEqual(sum(call[0]=="check" for call in calls), 1)
        trace = json.loads((self.scratch / "candidate-trace.json").read_text())
        self.assertEqual(trace["obligations"], [dict(id="archive-examples-linux", outcome="passed")])
        ran = self.run_stage("prepare")
        self.assertNotEqual(ran.returncode, 0)
        self.assertIn("build once", ran.stderr)

    def test_source_executes_working_tree_cases_without_archive_rehearsal(self):
        ran = self.run_stage("source")
        self.assertEqual(ran.returncode, 0, ran.stderr+ran.stdout)
        calls = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertFalse(any(call[0]=="bundle" for call in calls))
        self.assertEqual(sum(call[0]=="build" for call in calls), 1)

    def test_two_bundles_are_built_once_in_dependency_order(self):
        (self.workspace / "derived").mkdir()
        (self.workspace / "derived/main.roc").write_text('package [] { core: "../package/main.roc" }\n')
        subprocess.run(["git", "add", "derived"], cwd=self.workspace, check=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "-c", "commit.gpgsign=false", "commit", "-qm", "derived"], cwd=self.workspace, check=True)
        self.context["sha"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.workspace, text=True).strip()
        self.topology["bundles"].append(dict(id="derived", root="derived/main.roc", prefix="derived", placeholder="derived-url", depends_on=["package"]))
        self.topology["examples"][0]["bundles"].append("derived")
        self.topology["sboms"].append(dict(subject="derived", target="source", composition="sbom/derived.json", supplier_imports=""))
        self.make_plan()
        ran = self.run_stage("prepare")
        self.assertEqual(ran.returncode, 0, ran.stderr+ran.stdout)
        bundles = json.loads((self.scratch / "bundle-map.json").read_text())
        self.assertEqual([bundle["id"] for bundle in bundles], ["package", "derived"])
        staged = (self.scratch / "work/derived/main.roc").read_text()
        self.assertIn(bundles[0]["url"], staged)
        calls = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertEqual(sum(call[0]=="bundle" for call in calls), 2)
        self.assertEqual(sum(call[0]=="unbundle" for call in calls), 2)

    def test_deterministic_archive_is_independent_of_staging_directory(self):
        ran = self.run_stage("prepare")
        self.assertEqual(ran.returncode, 0, ran.stderr+ran.stdout)
        expected = (self.directory / "sample-examples-0.0.1-dry-run.zip").read_bytes()
        other_scratch, other_assets = self.root / "other-evidence", self.root / "other-assets"
        ran = self.run_stage("prepare", RO_DIRECTORY=str(other_scratch), RO_CANDIDATE=str(other_assets))
        self.assertEqual(ran.returncode, 0, ran.stderr+ran.stdout)
        self.assertEqual((other_assets / "sample-examples-0.0.1-dry-run.zip").read_bytes(), expected)

    def test_swapped_bundle_is_rejected_before_candidate_compilation(self):
        ran = self.run_stage("prepare")
        self.assertEqual(ran.returncode, 0, ran.stderr+ran.stdout)
        bundle = json.loads((self.scratch / "bundle-map.json").read_text())[0]
        (self.directory / bundle["path"]).write_bytes(b"swapped")
        ran = self.run_stage("candidate")
        self.assertNotEqual(ran.returncode, 0)
        self.assertIn("candidate bytes", ran.stderr)
        self.assertFalse((self.scratch / "candidate-trace.json").exists())

    def test_explicit_scratch_and_candidate_directories_cannot_overlap(self):
        ran = self.run_stage("prepare", RO_CANDIDATE=str(self.scratch / "assets"))
        self.assertNotEqual(ran.returncode, 0)
        self.assertIn("must not overlap", ran.stderr)
        self.assertFalse(self.trace.exists())

    def test_seal_includes_all_sidecars_and_renders_authored_notes_once(self):
        ran = self.run_stage("prepare")
        self.assertEqual(ran.returncode, 0, ran.stderr+ran.stdout)
        plan = json.loads(Path(self.env["RO_PLAN"]).read_text())
        bundle = json.loads((self.scratch / "bundle-map.json").read_text())[0]
        for rule in plan["assets"]:
            if rule["role"] in ("spdx.json", "cdx.json", "sbom-manifest.json"):
                path = rule["path"].replace("{hash}", "a"*43)
                (self.directory / path).write_text('{"inert":"sidecar"}')
        ran = self.run_stage("seal")
        self.assertEqual(ran.returncode, 0, ran.stderr+ran.stdout)
        inventory = json.loads((self.directory / "release-inventory.json").read_text())
        self.assertEqual(len(inventory["files"]), 6)
        for record in inventory["files"]:
            content = (self.directory / record["path"]).read_bytes()
            self.assertEqual((len(content), hashlib.sha256(content).hexdigest()), (record["size"], record["sha256"]))
        notes = (self.directory / "release-notes.md").read_text()
        self.assertIn("Authored notes", notes)
        self.assertIn("## Compilers", notes)
        self.assertIn("Roc compiler version nightly-2026-10-23-18478ff", notes)
        self.assertIn("Roc compiler version nightly-2026-10-09-258ab27", notes)
        self.assertNotIn("{package-url}", notes)
        self.assertIn("{package-url}", (self.workspace / "docs/releases/1.2.0.adoc").read_text())
        self.assertIn(bundle["url"], notes)
        calls = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertEqual(sum(call[0]=="render" for call in calls), 1)
        self.assertEqual({path.name for path in self.directory.iterdir()}, {record["path"] for record in inventory["files"]} | {"release-inventory.json"})
        ran = self.run_stage("seal")
        self.assertNotEqual(ran.returncode, 0)

    def fill_sidecars(self):
        plan = json.loads(Path(self.env["RO_PLAN"]).read_text())
        for rule in plan["assets"]:
            if rule["role"] in ("spdx.json", "cdx.json", "sbom-manifest.json"):
                (self.directory / rule["path"].replace("{hash}", "a"*43)).write_text('{"inert":"sidecar"}')

    def test_invalid_authored_note_title_or_placeholders_cannot_be_sealed(self):
        for source, error in (("= Other 1.2.0\n{package-url}\n", "title"), ("= sample 1.2.0\nNo install URL.\n", "must use"), ("= sample 1.2.0\n{package-url} {unknown-url}\n", "undeclared")):
            with self.subTest(error=error):
                if self.scratch.exists(): shutil.rmtree(self.scratch)
                if self.directory.exists(): shutil.rmtree(self.directory)
                (self.workspace / "docs/releases/1.2.0.adoc").write_text(source)
                ran = self.run_stage("prepare")
                self.assertEqual(ran.returncode, 0, ran.stderr+ran.stdout)
                self.fill_sidecars()
                ran = self.run_stage("seal")
                self.assertNotEqual(ran.returncode, 0)
                self.assertIn(error, ran.stderr)
                self.assertFalse((self.directory / "release-notes.md").exists())
                self.assertFalse((self.scratch / "seal-trace.json").exists())

    def test_task_receives_explicit_target_compiler_and_bounded_output_context(self):
        self.topology["checks"][0]["target_ids"] = ["linux"]
        self.make_plan()
        context_file = self.root / "task-context.json"
        ran = self.run_stage("source", TASK_CONTEXT=str(context_file))
        self.assertEqual(ran.returncode, 0, ran.stderr+ran.stdout)
        context = json.loads(context_file.read_text())
        self.assertEqual(context["RO_TARGET"], "linux")
        self.assertEqual(json.loads(context["RO_TARGET_IDS"]), ["linux"])
        self.assertEqual(json.loads(context["RO_OUTPUTS"]), [])
        self.assertEqual(context["RO_COMPILER"], self.env["RO_COMPILER"])
        self.assertEqual(Path(context["RO_OUTPUT_DIRECTORY"]), self.scratch / "adapter-check-unit-linux")

    @unittest.skipUnless(os.environ.get("ROC_DOCS_BINARY"), "set ROC_DOCS_BINARY to verify the real notes renderer")
    def test_real_renderer_resolves_authored_note_urls_and_emits_index_markdown(self):
        ran = self.run_stage("prepare")
        self.assertEqual(ran.returncode, 0, ran.stderr+ran.stdout)
        self.fill_sidecars()
        original = (self.workspace / "docs/releases/1.2.0.adoc").read_bytes()
        ran = self.run_stage("seal", RO_DOCS=os.environ["ROC_DOCS_BINARY"])
        self.assertEqual(ran.returncode, 0, ran.stderr+ran.stdout)
        notes = (self.directory / "release-notes.md").read_text()
        self.assertIn("Authored notes", notes)
        self.assertIn("Install", notes)
        self.assertIn("https://github.com/owner/sample/releases/download/0.0.1-dry-run/sample-", notes)
        self.assertNotIn("{package-url}", notes)
        self.assertTrue((self.scratch / "notes-work/.release-notes-100-1/site/index.md").is_file())
        self.assertEqual((self.workspace / "docs/releases/1.2.0.adoc").read_bytes(), original)

    @unittest.skipUnless(os.environ.get("ROC_DOCS_BINARY"), "set ROC_DOCS_BINARY to verify the real documentation archive")
    def test_real_documentation_archive_packs_site_root_and_exact_pdf(self):
        (self.workspace / "docs/manual").mkdir()
        (self.workspace / "docs/manual/index.adoc").write_text("= Sample manual\n\nA complete fixture manual.\n")
        subprocess.run(["git", "add", "docs/manual"], cwd=self.workspace, check=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "-c", "commit.gpgsign=false", "commit", "-qm", "manual"], cwd=self.workspace, check=True)
        self.context["sha"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.workspace, text=True).strip()
        self.topology["documentation"] = [dict(id="manual", directory="docs/manual", pdf=True)]
        self.make_plan()
        ran = self.run_stage("prepare", RO_DOCS=os.environ["ROC_DOCS_BINARY"])
        self.assertEqual(ran.returncode, 0, ran.stderr+ran.stdout)
        archive = self.directory / "sample-docs-0.0.1-dry-run.zip"
        with zipfile.ZipFile(archive) as zipped:
            self.assertIsNone(zipped.testzip())
            self.assertIn("sample-docs-0.0.1-dry-run/index.html", zipped.namelist())
            self.assertNotIn("sample-docs-0.0.1-dry-run/site/index.html", zipped.namelist())
        self.assertEqual((self.directory / "sample-0.0.1-dry-run.pdf").read_bytes(), (self.scratch / "work/.release-docs-manual/manual.pdf").read_bytes())

    def test_seal_requires_observed_tooling_compiler_output(self):
        ran = self.run_stage("prepare")
        self.assertEqual(ran.returncode, 0, ran.stderr+ran.stdout)
        self.fill_sidecars()
        ran = self.run_stage("seal", RO_TOOLING_COMPILER_VERSION="")
        self.assertNotEqual(ran.returncode, 0)
        self.assertIn("observed shared utility compiler", ran.stderr)
        self.assertFalse((self.directory / "release-notes.md").exists())

    def test_dispatch_rejects_bad_notes_before_version_bump_or_preparation(self):
        self.context.update(event="workflow_dispatch", ref="refs/heads/main", version="1.2.0")
        self.make_plan()
        (self.workspace / "docs/releases/1.2.0.adoc").write_text("= Wrong title\n{package-url}\n")
        previous = self.root / "previous.json"
        previous.write_text(json.dumps(dict(first_release=False, bundles=[dict(id="package", url="https://github.com/owner/sample/releases/download/1.1.0/sample-"+"a"*43+".tar.zst")])))
        ran = self.run_stage("validate", RO_PREVIOUS=str(previous))
        self.assertNotEqual(ran.returncode, 0)
        self.assertIn("title", ran.stderr)
        calls = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertFalse(any(call[0] in ("bundle", "bump") for call in calls))
        self.assertFalse((self.scratch / "validate-trace.json").exists())

    def test_dispatch_requires_explicit_previous_release_evidence_and_checks_bump(self):
        self.context.update(event="workflow_dispatch", ref="refs/heads/main", version="1.2.0")
        self.make_plan()
        ran = self.run_stage("validate")
        self.assertNotEqual(ran.returncode, 0)
        self.assertIn("RO_PREVIOUS", ran.stderr)
        shutil.rmtree(self.scratch)
        previous = self.root / "previous.json"
        previous.write_text(json.dumps(dict(first_release=False, bundles=[dict(id="package", url="https://github.com/owner/sample/releases/download/1.1.0/sample-"+"a"*43+".tar.zst")])))
        ran = self.run_stage("validate", RO_PREVIOUS=str(previous))
        self.assertEqual(ran.returncode, 0, ran.stderr+ran.stdout)
        calls = [json.loads(line) for line in self.trace.read_text().splitlines()]
        bump = next(call for call in calls if call[0]=="bump")
        self.assertEqual(bump[3:5], ["--expect", "1.2.0"])

    def test_wrong_event_checkout_cannot_execute_candidate_tools(self):
        plan = json.loads(Path(self.env["RO_PLAN"]).read_text())
        plan["context"]["sha"] = "a"*40
        Path(self.env["RO_PLAN"]).write_text(json.dumps(plan))
        ran = self.run_stage("source")
        self.assertNotEqual(ran.returncode, 0)
        self.assertIn("event source", ran.stderr)
        self.assertFalse((self.scratch / "source-trace.json").exists())

    def test_unknown_selected_operation_cannot_emit_pass(self):
        plan = json.loads(Path(self.env["RO_PLAN"]).read_text())
        plan["obligations"][0]["operation"] = "pretend-pass"
        Path(self.env["RO_PLAN"]).write_text(json.dumps(plan))
        ran = self.run_stage("source")
        self.assertNotEqual(ran.returncode, 0)
        self.assertIn("unsupported operation", ran.stderr)
        self.assertFalse((self.scratch / "source-trace.json").exists())

    def test_archive_expectation_tampering_fails_before_execution(self):
        ran = self.run_stage("prepare")
        self.assertEqual(ran.returncode, 0, ran.stderr+ran.stdout)
        archive = self.directory / "sample-examples-0.0.1-dry-run.zip"
        with zipfile.ZipFile(archive) as zipped:
            entries = [(entry, zipped.read(entry)) for entry in zipped.infolist()]
        with zipfile.ZipFile(archive,"w") as zipped:
            for info, content in entries:
                if info.filename.endswith("/spec.json") and "/examples/" not in info.filename:
                    suite = json.loads(content)
                    suite["apps"][0]["cases"][0]["stdout"] = "wrong\n"
                    content = json.dumps(suite).encode()
                zipped.writestr(info, content)
        ran = self.run_stage("candidate")
        self.assertNotEqual(ran.returncode, 0)
        self.assertIn("expectations", ran.stderr)
        self.assertFalse((self.scratch / "candidate-trace.json").exists())
