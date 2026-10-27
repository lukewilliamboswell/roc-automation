"""Repository-source trust boundaries, separate from standalone utility tests."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless((ROOT / ".github/workflows/nightly-compatibility.yml").is_file(), "workflow source is not part of the standalone artifact")
class CompatibilityWorkflowSecurityTests(unittest.TestCase):
    def test_aggregate_finds_single_and_multiple_target_artifacts(self):
        import fnmatch

        workflow = (ROOT / ".github/workflows/nightly-compatibility.yml").read_text()
        aggregate = workflow.split("\n  aggregate:\n", 1)[1].split("\n  report:\n", 1)[0]
        download = aggregate.split("pattern:", 1)[1].split("      - id: aggregate", 1)[0]
        pattern = download.splitlines()[0].strip().strip("'\"")
        # The pinned action uses Minimatch brace alternatives. These fixtures
        # use its simple name globs and cardinality-dependent extraction layout.
        alternatives = pattern.strip("{}").split(",")
        for runners in (("ubuntu-24.04",), ("ubuntu-24.04", "macos-15")):
            for missing in (False, True):
                with self.subTest(runners=runners, missing=missing), tempfile.TemporaryDirectory() as temporary:
                    names = ["nightly-selection", "unrelated-artifact"]
                    names += [f"compatibility-{runner}" for runner in runners if not missing or runner != runners[-1]]
                    selected = [name for name in names if any(fnmatch.fnmatchcase(name, glob) for glob in alternatives)]
                    self.assertNotIn("unrelated-artifact", selected)
                    for name in selected:
                        # download-artifact v8 flattens exactly one match.
                        destination = Path(temporary) if len(selected) == 1 else Path(temporary) / name
                        destination.mkdir(exist_ok=True)
                        filename = "selection.json" if name == "nightly-selection" else "result.json"
                        (destination / filename).write_text("{}")
                    discovered = [(Path(temporary) / f"compatibility-{runner}" / "result.json").is_file()
                                  for runner in runners]
                    self.assertEqual(all(discovered), not missing)

    def test_consumer_checkout_requires_default_branch_schedule_or_dispatch(self):
        import ast
        import itertools
        import re

        def evaluate(node):
            if isinstance(node, ast.Constant):
                return node.value
            if isinstance(node, ast.BoolOp):
                values = [evaluate(value) for value in node.values]
                return all(values) if isinstance(node.op, ast.And) else any(values)
            if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
                return evaluate(node.left) == evaluate(node.comparators[0])
            raise AssertionError("unsupported job-guard expression")

        workflow = (ROOT / ".github/workflows/nightly-compatibility.yml").read_text()
        job = workflow.split("\n  test:\n", 1)[1].split("\n  aggregate:\n", 1)[0]
        condition = job.split("    if: >-\n", 1)[1].split("    strategy:", 1)[0]
        condition = " ".join(condition.split()).replace("&&", " and ").replace("||", " or ")
        for event, ref_type, ref_name, ready in itertools.product(
                ("schedule", "workflow_dispatch", "pull_request", "pull_request_target", "push", "workflow_run"),
                ("branch", "tag"), ("trunk", "feature"), ("true", "false", "")):
            with self.subTest(event=event, ref_type=ref_type, ref_name=ref_name, ready=ready):
                context = {"github.event_name": event, "github.ref_type": ref_type,
                           "github.ref_name": ref_name, "github.event.repository.default_branch": "trunk",
                           "needs.select.outputs.ready": ready}
                expression = re.sub(r"\b(?:github|needs)\.[A-Za-z_.]+",
                                    lambda match: repr(context[match.group()]), condition)
                actual = evaluate(ast.parse(expression, mode="eval").body)
                expected = (event in ("schedule", "workflow_dispatch") and ref_type == "branch"
                            and ref_name == "trunk" and ready == "true")
                self.assertEqual(actual, expected)
        checkout = job.split("uses: actions/checkout@", 1)[1].split("      - name:", 1)[0]
        self.assertNotRegex(checkout, r"(?m)^\s+(ref|repository):")
        self.assertIn("persist-credentials: false", checkout)
        self.assertIn('run: test "$(git rev-parse HEAD)" = "$GITHUB_SHA"', job)

    def test_tooling_installation_cannot_shadow_package_nightly(self):
        import importlib.util
        from unittest.mock import patch
        source = ROOT / "actions/release-compatibility/bootstrap.py"
        spec = importlib.util.spec_from_file_location("compatibility_bootstrap", source)
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            compiler = root / "roc_nightly-windows_x86_64-tooling"
            compiler.mkdir()
            executable = compiler / ("roc.exe" if os.name == "nt" else "roc")
            executable.write_bytes(b"fixture")
            outputs = root / "outputs"
            with patch.dict(os.environ, {"GITHUB_WORKSPACE": str(root), "RUNNER_TEMP": str(root),
                                          "GITHUB_OUTPUT": str(outputs)}), \
                    patch.object(bootstrap.shutil, "which", return_value=str(executable)), \
                    patch.object(bootstrap.subprocess, "run") as build:
                bootstrap.main()
            values = dict(line.split("=", 1) for line in outputs.read_text().splitlines())
            directory = Path(values["directory"])
            self.assertTrue((directory / "tooling" / executable.name).is_file())
            self.assertEqual(list(root.glob("roc_nightly-*")), [])
            self.assertTrue(Path(values["archive-helper"]).is_file())
            build.assert_called_once()

    def test_report_job_has_no_consumer_execution_or_artifact_download(self):
        workflow = (ROOT / ".github/workflows/nightly-compatibility.yml").read_text()
        report = workflow.split("\n  report:\n", 1)[1]
        self.assertIn("issues: write", report)
        self.assertNotIn("checkout@", report)
        self.assertNotIn("download-artifact@", report)
        self.assertNotIn("run:", report)
        self.assertNotIn("setup-command:", report)
        self.assertIn("inputs.nightly-tag == '' && inputs.release-tag == ''", report)

    def test_every_remote_reference_is_immutable_and_only_report_can_write(self):
        import re
        workflow = (ROOT / ".github/workflows/nightly-compatibility.yml").read_text()
        action = (ROOT / "actions/release-compatibility/action.yml").read_text()
        for source in (workflow, action):
            for reference in re.findall(r"uses:\s+(\S+)", source):
                self.assertRegex(reference, r"^[^@]+@[0-9a-f]{40}$")
        unprivileged = workflow.split("\n  report:\n", 1)[0]
        self.assertNotIn(": write", unprivileged)
        self.assertNotIn("pull-requests:", workflow)
        self.assertNotIn("statuses:", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("fail-fast: false", workflow)


if __name__ == "__main__":
    unittest.main()
