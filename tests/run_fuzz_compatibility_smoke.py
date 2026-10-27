"""Build and run published roc-fuzz targets through the shared examples engine.

Only GitHub's examples-asset transport is replaced. The fixed platform URL,
compiler, archive admission, --fuzz build, libFuzzer, and verdicts are real.
"""
import json
import os
import re
import shutil
import stat
import subprocess
import zipfile

from test_release_compatibility import CompatibilityRunnerTests


def main():
    compiler = os.environ.get("ROC_COMPATIBILITY_SMOKE_COMPILER") or shutil.which("roc")
    if not compiler:
        raise RuntimeError("real compiler not installed")
    nightly = re.search(r"nightly-\d{4}-\d{2}-\d{2}-[0-9a-f]+",
                        subprocess.check_output([compiler, "version"], text=True)).group()
    dependency = ("https://github.com/lukewilliamboswell/roc-fuzz/releases/download/0.4.4/"
                  "D4YgxZeGd6MqK1wA17xERBocQHnVff3c8UNmDcvGeF7J.tar.zst")
    CompatibilityRunnerTests.setUpClass()
    fixture = CompatibilityRunnerTests(methodName="test_success_preserves_nonfatal_warnings_and_both_streams")
    try:
        fixture.directory_name = "fuzz-suite"
        fixture.temporary_parent = os.environ.get("RUNNER_TEMP")
        fixture.setUp()

        def case(name, args, exit_code=0):
            return dict(name=name, args=args, stdin="", stdout="", stderr="",
                        stderr_match="ignore", exit_code=exit_code)

        suite = dict(schema_version=1, release="0.4.4", dependencies=[dependency], apps=[
            dict(path="examples/keep.roc", build_args=["--fuzz"], cases=[
                case("replay", ["replay", "seed"]),
                case("one-second-smoke", ["run", "corpus", "--time=1"])]),
            dict(path="examples/fail.roc", build_args=["--fuzz"], cases=[
                case("expected-failure", ["replay", "seed"], 77)])])
        header = 'app [target] { fuzz: platform "' + dependency + '" }\nimport fuzz.Fuzz\n'

        def target(name, test):
            return (header + 'test : List(U8) -> Fuzz.Outcome\ntest = |_bytes| ' + test + '\n'
                    'target = Fuzz.target_with({name: "' + name + '", generator: Fuzz.bytes, '
                    'test, show: |bytes| Str.inspect(bytes)})\n')

        files = {"README.md": "Fixed roc-fuzz 0.4.4 shared-runner smoke fixture.\n",
                 "spec.json": json.dumps(suite), "examples/seed": b"",
                 "examples/keep.roc": target("keep", "Fuzz.keep"),
                 "examples/fail.roc": target("fail", 'crash "expected smoke fixture failure"')}
        with zipfile.ZipFile(fixture.archive, "w") as archive:
            for name, contents in files.items():
                entry = zipfile.ZipInfo("roc-fuzz-examples-0.4.4/" + name)
                entry.create_system = 3
                entry.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(entry, contents)
        selected = json.loads(fixture.selection.read_text())
        selected.update(repository="lukewilliamboswell/roc-fuzz", release="0.4.4", nightly=nightly,
                        asset={**fixture.asset(), "name": "roc-fuzz-examples-0.4.4.zip"})
        fixture.selection.write_text(json.dumps(selected))
        ran, directory, _ = fixture.run_phase("test", RA_COMPILER=compiler,
                                            RA_CASE_TIMEOUT_MS="10000", phase_timeout=900)
        print(ran.stdout)
        print(ran.stderr)
        result = json.loads((directory / "result.json").read_text())
        assert ran.returncode == 0, result
        assert (result["outcome"], result["tested"], result["failed"], result["skipped"]) == ("compatible", 3, 0, 0), result
        commands = [json.loads(path.read_text()) for path in sorted(directory.glob("*-command.json"))]
        builds = [command for command in commands if command["argv"][1:2] == ["build"]]
        assert len(builds) == 2 and all("--fuzz" in command["argv"] for command in builds), builds
        assert any(command["status"] == "exited" and command["code"] == 77 for command in commands), commands
        assert any(path.read_bytes() for path in directory.glob("*-stderr.log")), "lost fuzzer diagnostics"
    finally:
        fixture.doCleanups()
        CompatibilityRunnerTests.doClassCleanups()


if __name__ == "__main__":
    main()
