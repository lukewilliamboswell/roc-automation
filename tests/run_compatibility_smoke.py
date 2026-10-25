"""Run the complete native runner with a real installed compiler on each OS.

Only the release asset HTTP transport is replaced. The archive contract, digest,
compiler check/build, executable invocation and log/result paths are real.
"""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import zipfile

from test_release_compatibility import CompatibilityRunnerTests


def main():
    compiler = os.environ.get("ROC_COMPATIBILITY_SMOKE_COMPILER") or shutil.which("roc")
    expected = os.environ.get("ROC_COMPATIBILITY_SMOKE_EXPECTED", "compatible")
    if expected not in ("compatible", "incompatible"):
        raise ValueError("expected smoke outcome must be compatible or incompatible")
    if not compiler:
        raise RuntimeError("real compiler not installed")
    version = subprocess.check_output([compiler, "version"], text=True)
    nightly = re.search(r"nightly-\d{4}-\d{2}-\d{2}-[0-9a-f]+", version).group()
    CompatibilityRunnerTests.setUpClass()
    fixture = CompatibilityRunnerTests(methodName="test_success_preserves_diagnostics_and_both_streams")
    try:
        # Match the hosted action's private RUNNER_TEMP directory. The separate
        # native fixture suite exercises Unicode and spaces with real processes;
        # this smoke validates compiler behavior in the production directory.
        fixture.directory_name = "suite"
        fixture.temporary_parent = os.environ.get("RUNNER_TEMP")
        fixture.setUp()
        dependency = "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst"
        suite = dict(schema_version=1, release="0.25.0", dependencies=[dependency],
                     apps=[dict(path="examples/main.roc", cases=[dict(name="hello", args=[], stdin="",
                                stdout="Hello!\n", stderr="", exit_code=0)])])
        source = ('app [main!] { cli: platform "' + dependency + '" }\n'
                  'import cli.Stdout\nmain! = |_args| Stdout.line!("Hello!")\n')
        with zipfile.ZipFile(fixture.archive, "w") as archive:
            for name, contents in {"spec.json": json.dumps(suite), "examples/main.roc": source}.items():
                entry = zipfile.ZipInfo("basic-cli-examples-0.25.0/" + name)
                entry.create_system = 3
                entry.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(entry, contents)
        selected = json.loads(fixture.selection.read_text())
        selected.update(repository="roc-lang/basic-cli", release="0.25.0", nightly=nightly, asset=fixture.asset())
        selected["asset"]["name"] = "basic-cli-examples-0.25.0.zip"
        fixture.selection.write_text(json.dumps(selected))
        ran, directory, _ = fixture.run_phase("test", RA_COMPILER=compiler, RA_CASE_TIMEOUT_MS="30000", phase_timeout=600)
        print(ran.stdout)
        print(ran.stderr, file=sys.stderr)
        result = json.loads((directory / "result.json").read_text())
        if expected == "incompatible":
            # A real compiler/platform regression must retain its nonzero exit
            # and failed result. This assertion never changes the runner verdict.
            check = json.loads((directory / "2-command.json").read_text())
            build = json.loads((directory / "3-command.json").read_text())
            assert ran.returncode != 0, "broken-baseline fixture unexpectedly passed"
            assert result["outcome"] == "incompatible" and result["failed"] > 0 and result["tested"] == 0, result
            assert check["code"] == 0 and build["code"] != 0, (check, build)
            assert result["diagnostic_commands"] == 2, result
            diagnostics = (directory / "3-stdout.log").read_bytes() + (directory / "3-stderr.log").read_bytes()
            assert diagnostics, "missing failed compiler diagnostics"
            print("Verified real compiler failure remains incompatible; no example pass was reported.")
            return
        if ran.returncode:
            environment = {**fixture.env, "GH_TOKEN": "", "GITHUB_TOKEN": "", "NO_COLOR": "1",
                           "XDG_CACHE_HOME": str(directory / "empty-cache"),
                           "ROC_CACHE_DIR": str(directory / "empty-cache")}
            build_command = json.loads((directory / "3-command.json").read_text())
            verbose = subprocess.run([*build_command["argv"], "--verbose"],
                                     cwd=build_command["cwd"], env=environment,
                                     capture_output=True, text=True, timeout=300)
            print(verbose.stdout)
            print(verbose.stderr, file=sys.stderr)
            raise RuntimeError("real compiler smoke failed")
        assert result["outcome"] == "compatible" and result["tested"] == 1 and result["failed"] == 0, result
        build = json.loads((directory / "3-command.json").read_text())
        if os.name == "nt":
            assert any(argument.endswith(".exe") for argument in build["argv"]), build
    finally:
        fixture.doCleanups()
        CompatibilityRunnerTests.doClassCleanups()


if __name__ == "__main__":
    main()
