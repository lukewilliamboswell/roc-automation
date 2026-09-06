#!/usr/bin/env python3
"""Read-only release branch/version/checkout policy, without publication authority."""
import json
import os
import re
import subprocess
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nightly"))
import compiler_pins


NUMBER = r"(?:0|[1-9][0-9]*)"
IDENTIFIER = rf"(?:{NUMBER}|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
VERSION = re.compile(rf"({NUMBER})\.({NUMBER})\.({NUMBER})(?:-{IDENTIFIER}(?:\.{IDENTIFIER})*)?")


COMPILER_VERSION = re.compile(rf"v?({NUMBER})\.({NUMBER})\.({NUMBER})")
NIGHTLY = re.compile(r"nightly-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9a-f]{7,40}")


def validate(version, ref, default_branch, sha, checkout_sha, compiler_pin, allow_default=False, allow_nightly_bootstrap=False, simulated_stable_pin="", simulated_stable_line=""):
    if not VERSION.fullmatch(version):
        raise ValueError("Package version must be an unprefixed SemVer release or prerelease without build metadata")
    compiler = COMPILER_VERSION.fullmatch(compiler_pin)
    on_default = bool(allow_default and default_branch and ref == f"refs/heads/{default_branch}")
    bootstrap = bool(on_default and allow_nightly_bootstrap and NIGHTLY.fullmatch(compiler_pin))
    if bool(simulated_stable_pin) != bool(simulated_stable_line):
        raise ValueError("Simulation requires both an exact pin and compiler line")
    if simulated_stable_pin and (not NIGHTLY.fullmatch(simulated_stable_pin)
            or not re.fullmatch(rf"{NUMBER}\.{NUMBER}", simulated_stable_line)):
        raise ValueError("Invalid simulated stable mapping")
    simulated = bool(simulated_stable_pin and compiler_pin == simulated_stable_pin
                     and ref == f"refs/heads/roc-{simulated_stable_line}.x")
    if compiler is None and not bootstrap and not simulated:
        raise ValueError("Publishing requires a stable compiler pin or explicit default-branch nightly bootstrap")
    line = ""
    if simulated:
        line = f"roc-{simulated_stable_line}.x"
    elif not on_default:
        line = f"roc-{compiler[1]}.{compiler[2]}.x"
        if ref != f"refs/heads/{line}":
            raise ValueError(f"Compiler pin requires branch {line}, or the explicitly permitted default branch")
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or checkout_sha != sha:
        raise ValueError("Checkout must equal the exact workflow event commit")
    return {"version": version, "sha": sha, "compiler-pin": compiler_pin, "maintenance-branch": line,
            "compiler-channel": "simulated-stable" if simulated else "nightly-bootstrap" if bootstrap else "stable"}


def verify_compiler_release(compiler_pin, release, channel="stable"):
    if channel not in {"stable", "nightly-bootstrap", "simulated-stable"}:
        raise ValueError("Unknown compiler channel")
    repository = "roc-lang/roc" if channel == "stable" else "roc-lang/nightlies"
    if (not isinstance(release, dict) or release.get("tag_name") != compiler_pin
            or release.get("draft") is not False or release.get("prerelease") is not False
            or not isinstance(release.get("assets"), list) or not release["assets"]):
        raise ValueError(f"Compiler must be an existing published {repository} release with assets and matching channel")
    return f"https://github.com/{repository}/releases/tag/{compiler_pin}"


def main():
    allowed = os.environ.get("ALLOW_DEFAULT_BRANCH", "false")
    if allowed not in {"true", "false"}:
        raise ValueError("allow-default-branch must be true or false")
    bootstrap = os.environ.get("ALLOW_NIGHTLY_BOOTSTRAP", "false")
    if bootstrap not in {"true", "false"}:
        raise ValueError("allow-nightly-bootstrap must be true or false")
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise ValueError("Release policy requires an explicit workflow dispatch")
    checkout_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    root = os.environ.get("COMPILER_ROOT", "")
    if root:
        compiler_pins.validate_paths([root])
        path = Path(root)
        if path.is_symlink() or not path.resolve().is_relative_to(Path.cwd().resolve()):
            raise ValueError("Compiler root must be a regular file inside the checkout")
        compiler_pin = compiler_pins.read_pin(path)
    else:
        compiler_pin = Path(".roc-version").read_text().strip()
    result = validate(os.environ["RELEASE_VERSION"], os.environ["GITHUB_REF"],
                      os.environ.get("DEFAULT_BRANCH", ""), os.environ["GITHUB_SHA"],
                      checkout_sha, compiler_pin, allowed == "true", bootstrap == "true",
                      os.environ.get("SIMULATED_STABLE_PIN", ""), os.environ.get("SIMULATED_STABLE_LINE", ""))
    repository = "roc-lang/roc" if result["compiler-channel"] == "stable" else "roc-lang/nightlies"
    release = json.loads(subprocess.check_output(
        ["gh", "api", f"repos/{repository}/releases/tags/{result['compiler-pin']}"], text=True))
    result["compiler-release"] = verify_compiler_release(result["compiler-pin"], release, result["compiler-channel"])
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        for key, value in result.items():
            output.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
