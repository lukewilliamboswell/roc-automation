"""Exercise actual --fuzz builds and native libFuzzer execution via the shared runner.

The only substitute is the GitHub asset download, as in run_compatibility_smoke.
No downstream workflow or script is downloaded or executed. The platform is a
released content-addressed bundle named in the checked-in tiny target headers.
"""
import copy
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import zipfile

from test_release_compatibility import CompatibilityRunnerTests

ROOT = Path(__file__).resolve().parent / "fixtures/fuzz-compatibility"


def archive_fixture(fixture, suite):
    prefix = "roc-fuzz-examples-0.4.3/"
    with zipfile.ZipFile(fixture.archive, "w") as archive:
        files = {"spec.json": json.dumps(suite).encode(), "README.md": (ROOT / "README.adoc").read_bytes()}
        files.update({path.relative_to(ROOT).as_posix(): path.read_bytes() for path in (ROOT / "examples").rglob("*") if path.is_file()})
        for name, content in sorted(files.items()):
            entry = zipfile.ZipInfo(prefix + name)
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(entry, content)
    selected = json.loads(fixture.selection.read_text())
    selected.update(repository="lukewilliamboswell/roc-fuzz", release="0.4.3", asset=fixture.asset())
    selected["asset"]["name"] = "roc-fuzz-examples-0.4.3.zip"
    fixture.selection.write_text(json.dumps(selected))


def preserve(directory, evidence, name):
    if not evidence:
        return
    destination = evidence / name
    destination.mkdir(parents=True)
    for path in directory.iterdir():
        if path.name == "result.json" or path.name.endswith(("-command.json", "-stdout.log", "-stderr.log", "-expected.json")):
            shutil.copyfile(path, destination / path.name)


def probe(fixture, compiler, suite, name, expected, evidence, timeout="10000"):
    archive_fixture(fixture, suite)
    ran, directory, _ = fixture.run_phase("test", RA_COMPILER=compiler, RA_CASE_TIMEOUT_MS=timeout, phase_timeout=600)
    preserve(directory, evidence, name)
    result = json.loads((directory / "result.json").read_text())
    if result["outcome"] != expected or (ran.returncode == 0) != (expected == "compatible"):
        raise AssertionError(f"{name}: {result}\n{ran.stderr[-4000:]}\n{ran.stdout[-4000:]}")
    commands = {path.stem.removesuffix("-command"): json.loads(path.read_text()) for path in directory.glob("*-command.json")}
    for command in commands.values():
        argv = command["argv"]
        if len(argv) > 1 and argv[1] == "check":
            assert "--fuzz" not in argv, argv
        if len(argv) > 1 and argv[1] == "build":
            assert argv.count("--fuzz") == 1 and "--opt=speed" in argv and "--opt=dev" not in argv, argv
    print(f"{name}: {result['outcome']}; tested={result['tested']} failed={result['failed']}")
    return result, commands, directory


def main():
    machine = platform.machine().lower()
    supported = (sys.platform == "linux" and machine in ("x86_64", "amd64")) or (sys.platform == "darwin" and machine in ("arm64", "aarch64"))
    if not supported:
        raise RuntimeError("the pinned roc-fuzz 0.4.3 platform supports this smoke only on Linux x64 and macOS arm64; Windows/other targets are not claimed")
    compiler = os.environ.get("ROC_FUZZ_SMOKE_COMPILER") or shutil.which("roc")
    if not compiler or not os.environ.get("ROC_COMPATIBILITY_BINARY"):
        raise RuntimeError("set ROC_COMPATIBILITY_BINARY and install the real compiler or set ROC_FUZZ_SMOKE_COMPILER")
    version = subprocess.check_output([compiler, "version"], text=True)
    nightly = re.search(r"nightly-\d{4}-\d{2}-\d{2}-[0-9a-f]+", version).group()
    requested = os.environ.get("ROC_FUZZ_SMOKE_NIGHTLY")
    if requested and nightly != requested:
        raise RuntimeError(f"installed fuzz smoke compiler {nightly} differs from requested {requested}")
    evidence = Path(os.environ["ROC_FUZZ_SMOKE_EVIDENCE"]) if os.environ.get("ROC_FUZZ_SMOKE_EVIDENCE") else None
    if evidence:
        if evidence.exists() and any(evidence.iterdir()):
            raise ValueError("fuzz smoke evidence directory must be fresh; existing diagnostics are never merged or overwritten")
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "compiler.txt").write_text(version)
        (evidence / "host.json").write_text(json.dumps(dict(platform=sys.platform, machine=machine)))
    CompatibilityRunnerTests.setUpClass()
    fixture = CompatibilityRunnerTests(methodName="test_success_preserves_nonfatal_warnings_and_both_streams")
    try:
        fixture.directory_name = "suite"
        fixture.temporary_parent = os.environ.get("RUNNER_TEMP")
        fixture.setUp()
        selected = json.loads(fixture.selection.read_text())
        selected["nightly"] = nightly
        fixture.selection.write_text(json.dumps(selected))
        suite = json.loads((ROOT / "spec.json").read_text())
        result, commands, directory = probe(fixture, compiler, suite, "clean-and-expected-failure", "compatible", evidence)
        assert (result["tested"], result["failed"], result["diagnostic_commands"]) == (3, 0, 4), result
        cases = [(number, command) for number, command in commands.items() if len(command["argv"]) > 1 and command["argv"][1] in ("replay", "run")]
        assert sorted(command["code"] for _, command in cases) == [0, 0, 77], cases
        for number, command in cases:
            assert command["status"] == "exited" and not command["timed_out"] and not command["limited"], command
            assert (directory / f"{number}-stdout.log").read_bytes() == b""
            assert b"INFO:" in (directory / f"{number}-stderr.log").read_bytes(), "libFuzzer stats were not preserved"

        wrong_exit = copy.deepcopy(suite)
        wrong_exit["apps"] = [wrong_exit["apps"][1]]
        wrong_exit["apps"][0]["cases"][0]["exit_code"] = 0
        result, commands, directory = probe(fixture, compiler, wrong_exit, "unexpected-crash", "incompatible", evidence)
        assert (result["tested"], result["failed"]) == (1, 1), result
        assert any(command["code"] == 77 and command["status"] == "exited" for command in commands.values()), commands
        assert any(b"deliberate compatibility fixture failure" in path.read_bytes() for path in directory.glob("*-stderr.log"))
        fixture.assert_no_success_report(directory)

        noisy = copy.deepcopy(suite)
        noisy["apps"] = [dict(path="examples/noisy.roc", build_args=["--fuzz"], cases=[copy.deepcopy(suite["apps"][1]["cases"][0])])]
        result, commands, directory = probe(fixture, compiler, noisy, "output-bound", "output-limit", evidence)
        assert result["tested"] == 0, result
        assert any(command["limited"] for command in commands.values()), commands
        stderr = list(directory.glob("*-stderr.log"))
        assert max(path.stat().st_size for path in stderr) <= 10_000_000
        fixture.assert_no_success_report(directory)

        deadline = copy.deepcopy(suite)
        deadline["apps"] = [deadline["apps"][0]]
        deadline["apps"][0]["cases"] = [dict(name="outer-deadline", args=["run", "examples/corpus", "--time=0", "--seed=1"], stdin="", stdout="", stderr="", stderr_match="ignore", exit_code=124)]
        result, commands, directory = probe(fixture, compiler, deadline, "outer-deadline", "test-timeout", evidence, timeout="200")
        assert result["tested"] == 0 and any(command["timed_out"] for command in commands.values()), result
        fixture.assert_no_success_report(directory)
    finally:
        fixture.doCleanups()
        CompatibilityRunnerTests.doClassCleanups()


if __name__ == "__main__":
    main()
