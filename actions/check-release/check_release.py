#!/usr/bin/env python3
"""Read-only release branch/version/checkout policy, without publication authority."""
import os
import re
import subprocess


NUMBER = r"(?:0|[1-9][0-9]*)"
IDENTIFIER = rf"(?:{NUMBER}|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
VERSION = re.compile(rf"({NUMBER})\.({NUMBER})\.({NUMBER})(?:-{IDENTIFIER}(?:\.{IDENTIFIER})*)?")


def validate(version, ref, default_branch, sha, checkout_sha, allow_default=False):
    match = VERSION.fullmatch(version)
    if not match:
        raise ValueError("Version must be an unprefixed SemVer release or prerelease without build metadata")
    line = f"release/{match[1]}.{match[2]}.x"
    permitted = {f"refs/heads/{line}"}
    if allow_default and default_branch:
        permitted.add(f"refs/heads/{default_branch}")
    if ref not in permitted:
        raise ValueError(f"Release must run on {line}, or the explicitly permitted default branch")
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or checkout_sha != sha:
        raise ValueError("Checkout must equal the exact workflow event commit")
    return {"version": version, "sha": sha, "release-line": line}


def main():
    allowed = os.environ.get("ALLOW_DEFAULT_BRANCH", "false")
    if allowed not in {"true", "false"}:
        raise ValueError("allow-default-branch must be true or false")
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise ValueError("Release policy requires an explicit workflow dispatch")
    checkout_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    result = validate(os.environ["RELEASE_VERSION"], os.environ["GITHUB_REF"],
                      os.environ.get("DEFAULT_BRANCH", ""), os.environ["GITHUB_SHA"],
                      checkout_sha, allowed == "true")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        for key, value in result.items():
            output.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
