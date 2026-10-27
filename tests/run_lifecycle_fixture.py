#!/usr/bin/env python3
"""Read-only integration TEST harness for the real shared lifecycle consumers.

This is deliberately test code, not a consumer controller. All release behavior
is invoked through the real shared utilities and existing SBOM actions. It
never publishes or invents GitHub job observations. Target execution is reported
only for this host; foreign host compilation is labelled as cross compilation.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path("tests/fixtures/lifecycle")
PROFILES = ("package", "multibundle", "platform")
VERSION = "0.0.1-dry-run"


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as source:
        while chunk := source.read(65536):
            result.update(chunk)
    return result.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def executable(value):
    found = shutil.which(value)
    if not found:
        raise ValueError(f"required real executable is unavailable: {value}")
    return Path(found).resolve()


def current_host(runner=None):
    machine = platform.machine().lower()
    if sys.platform == "win32" and machine in ("amd64", "x86_64"):
        expected = ("Windows", "X64", "x86_64-windows", "windows-2025")
        labels = {"windows-2022", "windows-2025"}
    elif sys.platform == "darwin" and machine in ("arm64", "aarch64"):
        expected = ("macOS", "ARM64", "aarch64-darwin", "macos-15")
        labels = {"macos-15", "macos-26"}
    elif sys.platform.startswith("linux") and machine in ("x86_64", "amd64"):
        expected = ("Linux", "X64", "x86_64-linux", "ubuntu-24.04")
        labels = {"ubuntu-22.04", "ubuntu-24.04"}
    elif sys.platform.startswith("linux") and machine in ("arm64", "aarch64"):
        expected = ("Linux", "ARM64", "aarch64-linux", "ubuntu-24.04-arm")
        labels = {"ubuntu-24.04-arm"}
    else:
        raise ValueError(f"no fixture execution claim for {sys.platform}/{machine}")
    if runner and runner not in labels:
        raise ValueError(f"runner {runner} does not describe this actual host {expected[2]}")
    return dict(os=expected[0], arch=expected[1], system=expected[2], runner=runner or expected[3],
                os_release=platform.release(), os_version=platform.version(),
                runner_label_observed=False, runner_label_scope="selection label matching local OS/architecture, not a GitHub image observation")


class Harness:
    def __init__(self, args):
        self.args = args
        self.host = current_host(args.runner)
        self.source = Path(args.source).resolve()
        self.output = Path(args.output).resolve()
        self.output.mkdir(parents=True, exist_ok=False)
        self.tools = {name: executable(getattr(args, name)) for name in
                      ("planner", "operations", "compiler", "tooling_compiler", "blueprint", "policy", "docs", "zstd")}
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith(("RO_", "RA_", "INPUT_", "GITHUB_", "FIXTURE_"))
                            and key not in ("GH_TOKEN", "REAL_FIXTURE_COMPILER", "FAKE_PYTHON")}
        self.environment.update(GH_TOKEN="", GITHUB_TOKEN="", NO_COLOR="1")
        self.commands = []
        self.report = dict(format="roc-automation-real-fixture-test-v1", host=self.host,
                           github_job_observations=False, publication_attempted=False,
                           profiles=[], tools={name: dict(path=str(path), sha256=sha256(path)) for name, path in self.tools.items()})
        self.version = self.run("package-compiler", [self.tools["compiler"], "version"]).stdout.decode().strip()
        match = re.search(r"nightly-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9a-f]{7,40}", self.version)
        if not match:
            raise ValueError("the real package compiler did not identify one nightly")
        self.compiler_tag = match.group()
        self.tooling_version = self.run("utility-build-compiler", [self.tools["tooling_compiler"], "version"]).stdout.decode().strip()
        blueprint_version = self.run("blueprint-version", [self.tools["blueprint"], "--version"]).stdout.decode().strip()
        if blueprint_version != "0.8.0":
            raise ValueError(f"these fixtures require reviewed Blueprint 0.8.0, got {blueprint_version!r}")
        self.report["tools"]["compiler"]["version"] = self.version
        self.report["tools"]["tooling_compiler"]["version"] = self.tooling_version
        self.report["tools"]["blueprint"]["version"] = blueprint_version
        self.clone = self.output / "checkout"
        self.source_sha = self.run("source-sha", ["git", "rev-parse", "HEAD"], cwd=self.source).stdout.decode().strip()
        self.run("clone-source", ["git", "clone", "--shared", "--no-checkout", self.source, self.clone])
        self.run("sparse-source", ["git", "sparse-checkout", "set", "--cone", *[(FIXTURES / profile).as_posix() for profile in PROFILES]], cwd=self.clone)
        self.run("checkout-source", ["git", "checkout", "--detach", self.source_sha], cwd=self.clone)
        self.report["source_sha"] = self.source_sha
        self.report["source_scope"] = "committed fixture subtrees in sparse clone; not a live caller repository or GitHub job observation"
        self.wrapper = self.make_compiler_wrapper()
        self.receipts = self.output / "compiler-invocations.jsonl"
        self.environment.update(REAL_FIXTURE_COMPILER=str(self.tools["compiler"]),
                                FIXTURE_COMPILER_RECEIPTS=str(self.receipts), FAKE_PYTHON=sys.executable)
        self.environment["PATH"] = str(self.wrapper.parent) + os.pathsep + str(self.tools["blueprint"].parent) + os.pathsep + self.environment.get("PATH", "")

    def run(self, label, argv, *, cwd=None, env=None, timeout=600):
        argv = [str(argument) for argument in argv]
        result = subprocess.run(argv, cwd=cwd, env=env or self.environment,
                                capture_output=True, timeout=timeout)
        logs = self.output / "commands"
        logs.mkdir(exist_ok=True)
        number = len(self.commands)
        prefix = logs / f"{number:04d}-{label}"
        Path(str(prefix) + ".stdout").write_bytes(result.stdout)
        Path(str(prefix) + ".stderr").write_bytes(result.stderr)
        record = dict(label=label, argv=argv, cwd=str(cwd or Path.cwd()), status=result.returncode)
        self.commands.append(record)
        write_json(Path(str(prefix) + ".json"), record)
        if result.returncode:
            raise RuntimeError(f"{label} failed ({result.returncode}); see {prefix}.stderr\n" + result.stderr.decode(errors="replace")[-4000:])
        return result

    def make_compiler_wrapper(self):
        """Test-only receipt shim delegates every argument to the REAL compiler."""
        directory = self.output / "tool-receipts"
        directory.mkdir()
        code = '''import json,os,sys
record = {"argv":sys.argv[1:],"cwd":os.getcwd(),"phase":os.environ.get("FIXTURE_PHASE",""),"profile":os.environ.get("FIXTURE_PROFILE","")}
with open(os.environ["FIXTURE_COMPILER_RECEIPTS"],"a",encoding="utf-8") as output:
    output.write(json.dumps(record)+"\\n")
program=os.environ["REAL_FIXTURE_COMPILER"]
os.execv(program,[program,*sys.argv[1:]])
'''
        if os.name == "nt":
            wrapper = directory / "roc.exe"
            self.run("compile-receipt-launcher", ["clang", ROOT / "tests/fixtures/compatibility_launcher.c", "-o", wrapper])
            wrapper.with_suffix(".py").write_text(code, encoding="utf-8")
        else:
            wrapper = directory / "roc"
            wrapper.write_text(f"#!{sys.executable}\n" + code, encoding="utf-8")
            wrapper.chmod(0o755)
        return wrapper

    def fixture_env(self, name, workspace, evidence):
        return {**self.environment, "FIXTURE_PROFILE": name, "FIXTURE_PHASE": "setup",
                "RO_PLAN": str(evidence / "plan.json"), "RO_WORKSPACE": str(workspace),
                "GITHUB_WORKSPACE": str(workspace), "GITHUB_SHA": self.source_sha,
                "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": self.args.repository,
                "GITHUB_RUN_ID": "1", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_WORKFLOW_REF": "local-test/no-github-job-observation",
                "GITHUB_OUTPUT": str(evidence / "action-output"), "GITHUB_PATH": str(evidence / "action-path"),
                "GITHUB_ENV": str(evidence / "action-env"), "GITHUB_STEP_SUMMARY": str(evidence / "action-summary"),
                "RUNNER_TEMP": str(evidence / "runner-temp"), "RUNNER_OS": self.host["os"], "RUNNER_ARCH": self.host["arch"],
                "RO_CANDIDATE": str(workspace / ".lifecycle-test-assets"),
                "RO_COMPILER": str(self.wrapper), "RO_SELF": str(self.tools["operations"]),
                "RO_BLUEPRINT": str(self.tools["blueprint"]), "RO_POLICY": str(self.tools["policy"]),
                "RO_DOCS": str(self.tools["docs"]), "RO_TOOLING_COMPILER_VERSION": self.tooling_version,
                "RA_PYTHON": sys.executable, "RA_ARCHIVE_HELPER": str(self.source / "actions/release-compatibility/archive.py"),
                "RA_ZSTD": str(self.tools["zstd"])}

    def stage(self, plan, phase, runner, workspace, evidence, env):
        selected = [item for item in plan["obligations"] if item["stage"] == phase and item["runner"] == runner]
        if not selected:
            return None
        directory = evidence / f"{phase}-{runner}"
        selected_env = {**env, "FIXTURE_PHASE": phase, "RO_RUNNER": runner, "RO_DIRECTORY": str(directory)}
        if phase == "host":
            selected_env["RO_CANDIDATE"] = str(evidence / "host-transport" / runner)
        self.run(f"{workspace.name}-{phase}-{runner}", [self.tools["operations"], phase], cwd=workspace, env=selected_env)
        trace = read_json(directory / f"{phase}-trace.json")
        ids = [item["id"] for item in trace["obligations"]]
        if ids != [item["id"] for item in selected] or len(ids) != len(set(ids)) or any(item["outcome"] != "passed" for item in trace["obligations"]):
            raise AssertionError(f"{phase} did not execute each selected obligation exactly once")
        for key, value in {"stage": phase, "runner": runner, "source_sha": self.source_sha,
                           "repository": self.args.repository, "run_id": "1", "run_attempt": "1"}.items():
            if trace[key] != value:
                raise AssertionError(f"wrong {key} in {phase} trace")
        return directory

    def common_source_preflight(self, plan, workspace, env):
        """Real local checks, explicitly not a foreign-runner source verdict."""
        records = []
        for obligation in plan["obligations"]:
            if obligation["stage"] != "source" or obligation["runner"] == self.host["runner"] or obligation["target"]:
                continue
            operation = obligation["operation"]
            if operation == "policy":
                command = [self.tools["policy"], "check", "--root", workspace]
            elif operation == "format":
                roots = sorted({str(Path(bundle["root"]).parent) for bundle in plan["topology"]["bundles"]} | {example["directory"] for example in plan["topology"]["examples"]})
                command = [self.wrapper, "fmt", "--check", *roots]
            elif operation in ("unit", "source", "native"):
                command = [self.tools["blueprint"], "run", obligation["task"]]
            else:
                raise AssertionError("unknown common-source test preflight")
            self.run(workspace.name + "-local-" + obligation["id"], command, cwd=workspace,
                     env={**env, "FIXTURE_PHASE": "common-source-local"})
            records.append(dict(obligation=obligation["id"], actual_host=self.host["system"], github_job_observed=False))
        return records

    def seed_sbom_cache(self, workspace, evidence, env):
        if not self.args.sbom_tool:
            return
        output = evidence / "sbom-cache-output"
        output.write_text("")
        self.run(f"{workspace.name}-sbom-cache-identity", [sys.executable, self.source / "actions/sbom-sidecars/sbom_sidecars.py", "--prepare-cache"],
                 cwd=workspace, env={**env, "GITHUB_OUTPUT": str(output), "INPUT_CACHE_BINARY": "true"})
        fields = dict(line.split("=", 1) for line in output.read_text().splitlines())
        source = executable(self.args.sbom_tool)
        registry = read_json(self.source / "actions/setup-blueprint/sbom-tools.json")
        expected = registry["0.8.0"]["binaries"][self.host["system"]]
        if sha256(source) != expected:
            raise ValueError("provided native SBOM binary does not match the reviewed release digest")
        directory = Path(fields["binary-cache-directory"])
        directory.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, directory / "blueprint-sbom")

    def resolved_assets(self, plan, bundles):
        result = {}
        by_id = {item["id"]: item for item in bundles}
        for rule in plan["assets"]:
            path = rule["path"]
            if "{hash}" in path:
                suffix = path.split(".tar.zst", 1)[1]
                path = by_id[rule["subject"]]["path"] + suffix
            if path in result:
                raise AssertionError("duplicate planned public file")
            result[path] = rule
        return result

    def sboms(self, plan, bundles, workspace, evidence, env):
        self.seed_sbom_cache(workspace, evidence, env)
        assets = Path(env["RO_CANDIDATE"])
        by_id = {item["id"]: item["path"] for item in bundles}
        for index, mapping in enumerate(plan["topology"]["sboms"]):
            file = by_id.get(mapping["subject"], mapping["subject"])
            directory = evidence / f"sbom-{index}"
            directory.mkdir()
            imports = mapping["supplier_imports"] or f".lifecycle-test-imports-{index}.json"
            output = directory / "outputs"
            output.write_text("")
            shared = {**env, "INPUT_TARGET": mapping["target"], "INPUT_COMPOSITION": mapping["composition"],
                      "INPUT_BLUEPRINT_BINARY": str(self.tools["blueprint"]), "GITHUB_OUTPUT": str(output)}
            self.run(f"{workspace.name}-fetch-sboms-{index}", [sys.executable, self.source / "actions/fetch-dependency-sboms/fetch_dependency_sboms.py"],
                     cwd=workspace, env={**shared, "INPUT_OUTPUT": imports, "INPUT_MISSING": "fail", "INPUT_STALE_OPT_OUTS": "fail"})
            self.run(f"{workspace.name}-bind-sboms-{index}", [sys.executable, self.source / "actions/sbom-sidecars/sbom_sidecars.py"],
                     cwd=workspace, env={**shared, "INPUT_FILES": (assets / file).relative_to(workspace).as_posix(),
                                         "INPUT_IMPORTS": imports, "INPUT_VERSION": VERSION, "INPUT_AUTHOR": plan["topology"]["sbom_author"],
                                         "INPUT_URL_BASE": f"https://github.com/{self.args.repository}/releases/download/{VERSION}",
                                         "INPUT_NAMESPACE_BASE": f"https://github.com/{self.args.repository}/sbom", "INPUT_REVISION": "1",
                                         "INPUT_ARCHIVE_MEMBERS": "generate", "INPUT_CACHE_BINARY": "true", "INPUT_SPDX_2_3": "false"})

    def prove_host_members(self, plan, bundles, workspace, evidence, env):
        by_id = {item["id"]: item for item in bundles}
        inspections = {}
        for intermediate in plan["intermediates"]:
            path = intermediate["path"]
            owners = [bundle for bundle in plan["topology"]["bundles"] if Path(bundle["root"]).parent == Path(".") or path.startswith(Path(bundle["root"]).parent.as_posix() + "/")]
            if len(owners) != 1:
                raise AssertionError("host intermediate must have one owning bundle")
            owner = owners[0]
            if owner["id"] not in inspections:
                bundle = by_id[owner["id"]]
                output = evidence / f"members-{owner['id']}.json"
                self.run(f"{workspace.name}-member-hashes-{owner['id']}",
                         [sys.executable, self.source / "actions/release-compatibility/archive.py", "--bundle",
                          Path(env["RO_CANDIDATE"]) / bundle["path"], "sha256:" + bundle["sha256"], output], env=env)
                inspections[owner["id"]] = {member["path"]: member for member in read_json(output)["members"]}
            relative = Path(path).relative_to(Path(owner["root"]).parent).as_posix()
            member = inspections[owner["id"]][relative]
            original = workspace / path
            if member["size"] != original.stat().st_size or member["sha256"] != sha256(original):
                raise AssertionError("platform bundle does not contain the exact native host intermediate")

    def compiler_counts(self, name, plan, workspace, executed_phases):
        receipts = [item for item in (json.loads(line) for line in self.receipts.read_text().splitlines()) if item["profile"] == name]
        counts = Counter((item["phase"], item["argv"][0] if item["argv"] else "") for item in receipts)
        bundles = len(plan["topology"]["bundles"])
        if counts[("prepare", "bundle")] != bundles or counts[("prepare", "docs")] != bundles:
            raise AssertionError("a bundle or its API was not built exactly once")
        for phase in ("source", "candidate"):
            obligations = [item for item in plan["obligations"] if item["stage"] == phase and item["runner"] == self.host["runner"] and item["operation"] in ("working-examples", "archive-examples")]
            expected = sum(len(read_json(workspace / example["spec"])["apps"]) for item in obligations
                           for example in plan["topology"]["examples"] if example["id"] == item["subject"])
            if phase not in executed_phases:
                expected = 0
            if counts[(phase, "build")] != expected:
                raise AssertionError(f"{phase} must build each selected consumer app once, expected {expected}, got {counts[(phase, 'build')]}")
        return [dict(phase=phase, command=command, count=count) for (phase, command), count in sorted(counts.items())]

    def profile(self, profile):
        name = f"lifecycle-{profile}"
        self.report["active_profile"] = name
        workspace = self.clone / FIXTURES / profile
        evidence = self.output / name
        evidence.mkdir()
        (evidence / "runner-temp").mkdir()
        env = self.fixture_env(name, workspace, evidence)
        for key in ("GITHUB_OUTPUT", "GITHUB_PATH", "GITHUB_ENV", "GITHUB_STEP_SUMMARY"):
            Path(env[key]).write_text("")
        topology_path = workspace / "release-topology.json"
        topology = read_json(topology_path)
        context = dict(event="pull_request", ref="refs/pull/1/merge", default_branch="main", repository=self.args.repository,
                       sha=self.source_sha, run_id="1", run_attempt="1", compiler=self.compiler_tag, version="", full_dry_run=True,
                       topology_sha256=sha256(topology_path))
        write_json(evidence / "context.json", context)
        write_json(evidence / "changes.json", dict(complete=False, files=[]))
        self.run(name + "-plan", [self.tools["planner"], "plan", evidence / "context.json", topology_path,
                                 evidence / "changes.json", evidence / "plan.json"], cwd=workspace, env=env)
        plan = read_json(evidence / "plan.json")
        report = dict(profile=name, source_targets_executed=[], candidate_targets_executed=[], cross_built_host_tasks=[],
                      unexecuted_target_runners=sorted({target["runner"] for target in topology["target_matrix"] if target["runner"] != self.host["runner"]}))
        self.run(name + "-blueprint-check", [self.tools["blueprint"], "check"], cwd=workspace, env=env)
        host = self.stage(plan, "host", self.host["runner"], workspace, evidence, env)
        for adapter in topology["adapters"]:
            if adapter["operation"] == "build-host" and adapter["builder"] != self.host["runner"]:
                self.run(name + "-cross-" + adapter["id"], [self.tools["blueprint"], "run", adapter["task"]], cwd=workspace,
                         env={**env, "FIXTURE_PHASE": "cross-host", "RO_TARGET_IDS": json.dumps(adapter["target_ids"]),
                              "RO_OUTPUTS": json.dumps(adapter["outputs"]), "RO_COMPILER": str(self.wrapper), "RO_OUTPUT_DIRECTORY": str(workspace)})
                report["cross_built_host_tasks"].append(adapter["id"])
        report["local_common_source_preflight"] = self.common_source_preflight(plan, workspace, env)
        source = self.stage(plan, "source", self.host["runner"], workspace, evidence, env)
        if source:
            report["source_targets_executed"] = [target["id"] for target in topology["target_matrix"] if target["runner"] == self.host["runner"]]
        # Common controller preparation is exercised on this actual host. Its
        # scheduled Ubuntu label is not recorded as Linux target execution.
        prepare = self.stage(plan, "prepare", "ubuntu-24.04", workspace, evidence, env)
        if not prepare:
            raise AssertionError("full fixture rehearsal requires common preparation")
        env["RO_BUNDLE_MAP"] = str(prepare / "bundle-map.json")
        bundles = read_json(prepare / "bundle-map.json")
        self.prove_host_members(plan, bundles, workspace, evidence, env)
        self.sboms(plan, bundles, workspace, evidence, env)
        self.stage(plan, "seal", "ubuntu-24.04", workspace, evidence, env)
        assets = Path(env["RO_CANDIDATE"])
        planned = self.resolved_assets(plan, bundles)
        actual = {path.relative_to(assets).as_posix(): dict(size=path.stat().st_size, sha256=sha256(path)) for path in assets.rglob("*") if path.is_file()}
        if set(actual) != set(planned):
            raise AssertionError(f"public asset mismatch: missing={set(planned)-set(actual)}, unexpected={set(actual)-set(planned)}")
        inventory = read_json(assets / "release-inventory.json")
        listed = {item["path"]: dict(size=item["size"], sha256=item["sha256"]) for item in inventory["files"]}
        if listed != {path: record for path, record in actual.items() if path != "release-inventory.json"}:
            raise AssertionError("sealed inventory does not describe the exact public bytes")
        # Withhold every source bundle tree after sealing: candidate execution
        # must use distributed files, never source native hosts or package roots.
        held = evidence / "withheld-source"
        held.mkdir()
        for bundle in topology["bundles"]:
            parent = Path(bundle["root"]).parent
            if parent == Path("."):
                raise ValueError("fixture test requires separate bundle source directories")
            original = workspace / parent
            if original.exists():
                shutil.move(str(original), str(held / bundle["id"]))
        candidate = self.stage(plan, "candidate", self.host["runner"], workspace, evidence, env)
        if candidate:
            report["candidate_targets_executed"] = [target["id"] for target in topology["target_matrix"] if target["runner"] == self.host["runner"]]
        after = {path.relative_to(assets).as_posix(): dict(size=path.stat().st_size, sha256=sha256(path)) for path in assets.rglob("*") if path.is_file()}
        if after != actual:
            raise AssertionError("candidate execution changed the sealed release files")
        report["public_files"] = [dict(path=path, **record) for path, record in sorted(actual.items())]
        report["compiler_invocations"] = self.compiler_counts(name, plan, workspace, {"source" if source else "", "candidate" if candidate else ""})
        report["common_preparation_host"] = self.host["system"]
        report["status"] = "passed-for-reported-execution-scope"
        write_json(evidence / "report.json", report)
        self.report["profiles"].append(report)

    def execute(self):
        for profile in PROFILES if self.args.profile == "all" else (self.args.profile,):
            self.profile(profile)
        self.report["status"] = "passed-for-reported-execution-scope"
        write_json(self.output / "report.json", self.report)
        print(self.output / "report.json")


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=(*PROFILES, "all"), default="all")
    parser.add_argument("--source", default=str(ROOT), help="committed roc-automation repository providing fixture sources/controllers")
    parser.add_argument("--output", required=True, help="new evidence directory; existing directories are refused")
    parser.add_argument("--runner", help="actual current-host runner label; other OS labels are rejected")
    parser.add_argument("--repository", default="roc-automation-fixtures/local-test")
    for name, env in (("planner", "ROC_RELEASE_PLAN_BINARY"), ("operations", "ROC_RELEASE_OPERATIONS_BINARY"),
                      ("compiler", "RO_COMPILER"), ("tooling-compiler", "ROC_TOOLING_COMPILER"),
                      ("blueprint", "RO_BLUEPRINT"), ("policy", "RO_POLICY"), ("docs", "RO_DOCS"), ("zstd", "RA_ZSTD")):
        parser.add_argument("--" + name, default=os.environ.get(env), required=not os.environ.get(env))
    parser.add_argument("--sbom-tool", default=os.environ.get("ROC_SBOM_BINARY"), help="optional native tool cache seed; reviewed digest is still verified by the actual action")
    return parser.parse_args(argv)


def main(argv=None):
    harness = None
    try:
        args = arguments(argv)
        harness = Harness(args)
        harness.execute()
        return 0
    except (AssertionError, KeyError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        if harness is not None:
            harness.report["status"] = "failed"
            harness.report["error"] = str(error)
            write_json(harness.output / "report.json", harness.report)
        print((f"real lifecycle fixture TEST failed: {error}").replace("::", ": :"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
