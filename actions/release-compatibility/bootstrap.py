"""Build the pinned Roc controller with native paths on every hosted OS."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    directory = Path(tempfile.mkdtemp(prefix="roc-compatibility.", dir=os.environ["RUNNER_TEMP"]))
    action = Path(__file__).resolve().parent
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as outputs:
        outputs.write(f"directory={directory}\npython={sys.executable}\narchive-helper={action / 'archive.py'}\n")
    compiler = shutil.which("roc")
    if not compiler:
        raise RuntimeError("tooling compiler not on PATH")
    executable = directory / ("runner.exe" if os.name == "nt" else "runner")
    subprocess.run([compiler, "build", str(action / "../../scripts/release_compatibility.roc"),
                    "--output=" + str(executable)], check=True)
    # setup-roc finds the first extracted compiler in the workspace. Move this
    # installation aside so the package nightly cannot resolve to tooling again.
    tooling = Path(compiler).resolve().parent
    workspace = Path(os.environ["GITHUB_WORKSPACE"]).resolve()
    if tooling.parent != workspace or not tooling.name.startswith("roc_nightly-"):
        raise RuntimeError("unexpected tooling compiler directory")
    shutil.move(str(tooling), directory / "tooling")


if __name__ == "__main__":
    main()
