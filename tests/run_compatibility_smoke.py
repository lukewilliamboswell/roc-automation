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
    compiler = shutil.which("roc")
    if not compiler:
        raise RuntimeError("real compiler not installed")
    version = subprocess.check_output([compiler, "version"], text=True)
    nightly = re.search(r"nightly-\d{4}-\d{2}-\d{2}-[0-9a-f]+", version).group()
    CompatibilityRunnerTests.setUpClass()
    fixture = CompatibilityRunnerTests(methodName="test_success_preserves_nonfatal_warnings_and_both_streams")
    try:
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
        if ran.returncode:
            raise RuntimeError("real compiler smoke failed")
        result = json.loads((directory / "result.json").read_text())
        assert result["outcome"] == "compatible" and result["tested"] == 1 and result["failed"] == 0, result
        build = json.loads((directory / "3-command.json").read_text())
        if os.name == "nt":
            assert any(argument.endswith(".exe") for argument in build["argv"]), build
    finally:
        fixture.doCleanups()
        CompatibilityRunnerTests.doClassCleanups()


if __name__ == "__main__":
    main()
