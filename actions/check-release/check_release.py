#!/usr/bin/env python3
"""Read-only release branch/version/checkout policy, without publication authority."""
import os
import re
import subprocess
from pathlib import Path


NUMBER = r"(?:0|[1-9][0-9]*)"
IDENTIFIER = rf"(?:{NUMBER}|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
VERSION = re.compile(rf"({NUMBER})\.({NUMBER})\.({NUMBER})(?:-{IDENTIFIER}(?:\.{IDENTIFIER})*)?")


COMPILER_VERSION = re.compile(rf"v?({NUMBER})\.({NUMBER})\.({NUMBER})")
NIGHTLY = re.compile(r"nightly-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9a-f]{7,40}")


def validate(version, ref, default_branch, sha, checkout_sha, compiler_pin, allow_default=False):
    if not VERSION.fullmatch(version):
        raise ValueError("Package version must be an unprefixed SemVer release or prerelease without build metadata")
    compiler = COMPILER_VERSION.fullmatch(compiler_pin)
    if compiler is None and not NIGHTLY.fullmatch(compiler_pin):
        raise ValueError("Compiler pin must be an exact final version or pinned nightly")
    line = ""
    if not (allow_default and default_branch and ref == f"refs/heads/{default_branch}"):
        if compiler is None:
            raise ValueError("A compiler maintenance branch requires a final versioned compiler pin")
        line = f"release/roc-{compiler[1]}.{compiler[2]}.x"
        if ref != f"refs/heads/{line}":
            raise ValueError(f"Compiler pin requires branch {line}, or the explicitly permitted default branch")
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or checkout_sha != sha:
        raise ValueError("Checkout must equal the exact workflow event commit")
    return {"version": version, "sha": sha, "compiler-pin": compiler_pin, "maintenance-branch": line}


def main():
    allowed = os.environ.get("ALLOW_DEFAULT_BRANCH", "false")
    if allowed not in {"true", "false"}:
        raise ValueError("allow-default-branch must be true or false")
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise ValueError("Release policy requires an explicit workflow dispatch")
    checkout_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    result = validate(os.environ["RELEASE_VERSION"], os.environ["GITHUB_REF"],
                      os.environ.get("DEFAULT_BRANCH", ""), os.environ["GITHUB_SHA"],
                      checkout_sha, Path(".roc-version").read_text().strip(), allowed == "true")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        for key, value in result.items():
            output.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
