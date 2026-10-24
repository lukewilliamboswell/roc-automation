"""Run the distributed planner against authority and selection failures."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

BINARY = os.environ.get("ROC_RELEASE_PLAN_BINARY")
FIXTURES = Path(__file__).parent / "fixtures/lifecycle"


@unittest.skipUnless(BINARY, "set ROC_RELEASE_PLAN_BINARY to test the native planner")
class LifecyclePlannerTests(unittest.TestCase):
    def setUp(self):
        self.context = json.loads((FIXTURES / "context-pr.json").read_text())
        self.topology = json.loads((FIXTURES / "topology-package.json").read_text())
        self.changes = json.loads((FIXTURES / "changes.json").read_text())

    def invoke(self, *, topology=None, context=None, changes=None, raw_topology=None):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            values = {"context": context or self.context,
                      "topology": topology or self.topology,
                      "changes": changes or self.changes}
            for name, value in values.items():
                (directory / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
            if raw_topology is not None:
                (directory / "topology.json").write_text(raw_topology, encoding="utf-8")
            result = subprocess.run([BINARY, "plan", str(directory / "context.json"),
                                     str(directory / "topology.json"), str(directory / "changes.json"),
                                     str(directory / "plan.json")], capture_output=True, timeout=15)
            output = directory / "plan.json"
            return result, json.loads(output.read_text()) if output.exists() else None

    def test_ordinary_source_still_has_source_obligations_and_no_archive_claim(self):
        result, plan = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(plan["full_rehearsal"])
        self.assertEqual(plan["assets"], [])
        self.assertEqual({item["id"] for item in plan["obligations"]},
                         {"source-policy", "source-format", "check-unit", "working-examples-linux"})

    def test_dispatch_builds_once_and_tests_every_declared_target(self):
        topology = json.loads((FIXTURES / "topology-platform.json").read_text())
        context = {**self.context, "event": "workflow_dispatch", "ref": "refs/heads/main", "version": "1.0.0"}
        result, plan = self.invoke(topology=topology, context=context)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(plan["full_rehearsal"])
        self.assertEqual(plan["bundle_order"], ["core", "platform"])
        host = [item for item in plan["obligations"] if item["stage"] == "host"]
        self.assertEqual(len(host), 1)
        self.assertEqual(host[0]["runner"], "ubuntu-24.04")
        self.assertEqual({item["runner"] for item in plan["obligations"] if item["stage"] == "candidate"},
                         {"ubuntu-24.04", "windows-2025"})
        paths = [asset["path"] for asset in plan["assets"]]
        self.assertEqual(len(paths), len(set(paths)))
        self.assertFalse(any(path.startswith("platform/targets/") for path in paths))
        self.assertEqual({item["path"] for item in plan["intermediates"]}, {"platform/targets/linux.a", "platform/targets/windows.a"})
        self.assertIn("platform-1.0.0.pdf", paths)
        self.assertEqual([item["id"] for item in plan["obligations"] if item["stage"] == "sbom"],
                         ["sbom-0", "sbom-1"])

    def test_unknown_mixed_rename_delete_and_incomplete_changes_force_rehearsal(self):
        changes = [
            {"complete": False, "files": self.changes["files"]},
            {"complete": True, "files": []},
            {"complete": True, "files": [{"status":"renamed", "old_path":"examples/a.roc", "new_path":"package/a.roc"}]},
            {"complete": True, "files": [{"status":"removed", "old_path":"Blueprint.lock", "new_path":""}]},
            {"complete": True, "files": [{"status":"other", "old_path":"package/a.roc", "new_path":"package/a.roc"}]},
            {"complete": True, "files": self.changes["files"] + [{"status":"added", "old_path":"", "new_path":"unknown.txt"}]},
        ]
        for inventory in changes:
            with self.subTest(inventory=inventory):
                result, plan = self.invoke(changes=inventory)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(plan["full_rehearsal"])

    def test_source_prefixes_cannot_exempt_mandatory_categories(self):
        topology = {**self.topology, "source_paths":[".github", "scripts", "examples", "package"]}
        for path in [".github/workflows/release.yml", "scripts/build.roc", "examples/spec.json", "package/Cargo.lock"]:
            with self.subTest(path=path):
                result, plan = self.invoke(topology=topology, changes={"complete":True,"files":[{"status":"modified","old_path":path,"new_path":path}]})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(plan["full_rehearsal"])

    def test_wrong_event_source_and_version_fail_without_output(self):
        for extra in [{"event":"push"}, {"sha":"short"}, {"repository":"../other"},
                      {"run_attempt":"0"}, {"event":"workflow_dispatch","version":"1.0.0","ref":"refs/heads/topic"},
                      {"event":"workflow_dispatch","version":"0.0.1-dry-run","ref":"refs/heads/main"},
                      {"version":"1.0.0"}]:
            with self.subTest(extra=extra):
                result, plan = self.invoke(context={**self.context, **extra})
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(plan)

    def test_strict_structure_rejects_duplicate_escaped_unknown_and_null_fields(self):
        source = json.dumps(self.topology)
        bad = [source.replace('"schema": 1', '"schema": 1, "schema": 1'),
               source.replace('"schema": 1', '"schema": 1, "\\u0073chema": 1'),
               source.replace('"schema": 1', '"schema": 1.0'),
               source.replace('"schema": 1', '"schema": 1, "unknown": true'),
               source.replace('"prefix": "sample"', '"prefix": "sample", "command": "evil"'),
               source.replace('"checks": [', '"checks": null, "discard": ['),
               source + '{}', ' ' * 65537]
        for raw in bad:
            with self.subTest(raw=raw[:100]):
                result, plan = self.invoke(raw_topology=raw)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(plan)

    def test_unknown_edges_cycles_case_collisions_and_unsafe_paths_fail(self):
        modifications = []
        for root in ["../main.roc", "C:/main.roc", "NUL.roc", "a\\main.roc"]:
            topology = copy.deepcopy(self.topology)
            topology["bundles"][0]["root"] = root
            modifications.append(topology)
        for dependencies in [["absent"], ["package"]]:
            topology = copy.deepcopy(self.topology)
            topology["bundles"][0]["depends_on"] = dependencies
            modifications.append(topology)
        topology = copy.deepcopy(self.topology)
        topology["bundles"].append({**topology["bundles"][0], "id":"other", "root":"PACKAGE/main.roc", "prefix":"other", "placeholder":"other-url"})
        modifications.append(topology)
        for topology in modifications:
            with self.subTest(topology=topology):
                result, plan = self.invoke(topology=topology)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(plan)

    def test_unknown_or_missing_native_subject_and_ambiguous_output_fail(self):
        base = json.loads((FIXTURES / "topology-platform.json").read_text())
        modifications = []
        topology = copy.deepcopy(base)
        topology["sboms"].pop()
        modifications.append(topology)
        topology = copy.deepcopy(base)
        topology["adapters"][0]["outputs"][1]["path"] = "platform/targets/LINUX.a"
        modifications.append(topology)
        topology = copy.deepcopy(base)
        topology["adapters"][0]["builder"] = "self-hosted"
        modifications.append(topology)
        topology = copy.deepcopy(base)
        topology["adapters"][0]["outputs"][1]["target"] = "absent"
        modifications.append(topology)
        for topology in modifications:
            with self.subTest(topology=topology):
                result, plan = self.invoke(topology=topology)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(plan)
