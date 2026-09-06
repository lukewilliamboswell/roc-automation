#!/usr/bin/env python3
"""Roc nightly PR controller. Run only from the trusted default branch.

Repository validation workflows are declared in .github/roc-nightly.json.
This controller is distributed from the SHA-pinned shared action. No project code
is executed by this controller's privileged jobs.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

BRANCH = "automation/roc-nightly"
TAG = re.compile(r"nightly-\d{4}-\d{2}-\d{2}-[0-9a-f]{7,40}")
ROOT = Path(os.environ.get("GITHUB_WORKSPACE", os.getcwd())).resolve()


def run(args, *, data=None, env=None):
    return subprocess.run(args, input=data, text=True, capture_output=True,
                          check=True, cwd=ROOT, env=env).stdout.strip()


def api(endpoint, data=None, method=None):
    args = ["gh", "api", endpoint, "-H", "X-GitHub-Api-Version: 2026-03-10"]
    if method:
        args += ["--method", method]
    if data is not None:
        args += ["--input", "-"]
    result = run(args, data=json.dumps(data) if data is not None else None)
    return json.loads(result) if result else None


def output(key, value):
    value = str(value)
    if any(c in key + value for c in "\r\n"):
        raise ValueError("Multiline workflow output")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
        handle.write(f"{key}={value}\n")


def tag(value):
    if not TAG.fullmatch(value):
        raise ValueError(f"Invalid nightly tag: {value!r}")
    return value


def repo():
    value = os.environ["GITHUB_REPOSITORY"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise ValueError("Invalid repository")
    return value


def pin_at(sha):
    result = api(f"repos/{repo()}/contents/.roc-version?ref={sha}")
    return tag(base64.b64decode(result["content"]).decode().strip())


def head():
    return api(f"repos/{repo()}/git/ref/heads/{BRANCH}")["object"]["sha"]


def existing_pr():
    owner = repo().split("/")[0]
    prs = api(f"repos/{repo()}/pulls?state=open&head={owner}:{BRANCH}")
    if len(prs) > 1:
        raise ValueError("More than one nightly PR")
    return prs[0] if prs else None


def pr_body(sha, nightly, status, runs=()):
    lines = [f"Updates `.roc-version` to [{nightly}](https://github.com/roc-lang/nightlies/releases/tag/{nightly}).",
             f"Candidate commit: `{sha}`.", status]
    for item in runs:
        lines.append(f"- [{item['workflow']}]({item['html_url']}): **{item.get('conclusion') or 'pending'}**")
    lines += [f"[Updater run]({os.environ['GITHUB_SERVER_URL']}/{repo()}/actions/runs/{os.environ['GITHUB_RUN_ID']}).",
              "Created by the Roc nightly updater. Validation runs on the candidate commit. Merging is disabled unless the repository explicitly opts in; this workflow never approves PRs."]
    return "\n\n".join(lines)


def save_pr(sha, nightly, status, runs=()):
    current = existing_pr()
    data = {"title": f"Update Roc nightly to {nightly}",
            "body": pr_body(sha, nightly, status, runs)}
    if current:
        return api(f"repos/{repo()}/pulls/{current['number']}", data, "PATCH")
    data.update(head=BRANCH, base=os.environ["DEFAULT_BRANCH"])
    return api(f"repos/{repo()}/pulls", data)


def push_base(base, old):
    # Authenticate only this git invocation. Do not persist the token or pass
    # it in argv. A lease refuses to overwrite concurrent branch changes.
    env = os.environ.copy()
    auth = base64.b64encode(("x-access-token:" + env["GH_TOKEN"]).encode()).decode()
    env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
               GIT_CONFIG_VALUE_0="AUTHORIZATION: basic " + auth)
    run(["git", "push", f"--force-with-lease=refs/heads/{BRANCH}:{old}",
         "origin", f"{base}:refs/heads/{BRANCH}"], env=env)


def signed_pin(base, nightly):
    request = {"query": """mutation($input: CreateCommitOnBranchInput!) {
      createCommitOnBranch(input: $input) { commit { oid } }
    }""", "variables": {"input": {
        "branch": {"repositoryNameWithOwner": repo(), "branchName": BRANCH},
        "expectedHeadOid": base,
        "message": {"headline": f"Update Roc nightly to {nightly}"},
        "fileChanges": {"additions": [{"path": ".roc-version",
            "contents": base64.b64encode((nightly + "\n").encode()).decode()}]},
    }}}
    sha = api("graphql", request)["data"]["createCommitOnBranch"]["commit"]["oid"]
    if not api(f"repos/{repo()}/commits/{sha}")["commit"]["verification"]["verified"]:
        raise ValueError("GitHub did not verify the update commit signature")
    return sha


def prepare():
    output("auto_merge", str(load_config().get("auto_merge", False)).lower())
    base = run(["git", "rev-parse", "HEAD"])
    release = api("repos/roc-lang/nightlies/releases/latest")
    nightly = tag(release["tag_name"])
    if release["draft"] or release["prerelease"] or not release["assets"]:
        raise ValueError("Latest release is not a published nightly with assets")
    if nightly == tag((ROOT / ".roc-version").read_text().strip()):
        output("changed", "false")
        return
    # An exact matching-refs lookup distinguishes absence from API failures.
    refs = api(f"repos/{repo()}/git/matching-refs/heads/{BRANCH}")
    refs = [r for r in refs if r["ref"] == f"refs/heads/{BRANCH}"]
    old = refs[0]["object"]["sha"] if refs else ""
    same = False
    if old and old != base:
        commit = api(f"repos/{repo()}/commits/{old}")
        # Never erase human work from this reserved branch.
        if len(commit["parents"]) != 1 or [f["filename"] for f in commit["files"]] != [".roc-version"]:
            raise ValueError("Nightly branch contains changes other than a pin commit")
        same = commit["parents"][0]["sha"] == base and pin_at(old) == nightly
    if same and existing_pr() and os.environ.get("FORCE", "false") != "true":
        output("changed", "false")
        return
    current = existing_pr()
    if current:
        # Clear old success before changing the branch, even if push fails.
        api(f"repos/{repo()}/pulls/{current['number']}",
            {"body": "Nightly update is being prepared. Previous validation is stale; wait for the new candidate results."}, "PATCH")
    if same:
        sha = old
    else:
        push_base(base, old)
        sha = signed_pin(base, nightly)
    save_pr(sha, nightly, "**Pending:** candidate validation has not completed.")
    for key, value in {"changed": "true", "sha": sha, "nightly": nightly, "base": base}.items():
        output(key, value)



def load_config():
    config = json.loads((ROOT / ".github/roc-nightly.json").read_text())
    if not isinstance(config, dict) or "workflows" not in config or set(config) - {"workflows", "auto_merge"}:
        raise ValueError("Expected workflows and optional auto_merge configuration")
    if type(config.get("auto_merge", False)) is not bool:
        raise ValueError("auto_merge must be a boolean")
    return config


def load_workflows():
    config = load_config()
    workflows = config["workflows"]
    if not isinstance(workflows, list) or not workflows:
        raise ValueError("Validation workflows must be a nonempty list")
    for workflow in workflows:
        if not isinstance(workflow, str) or not re.fullmatch(r"[A-Za-z0-9_-]+\.ya?ml", workflow):
            raise ValueError("Invalid workflow filename")
        path = ROOT / ".github/workflows" / workflow
        if not path.is_file() or not path.resolve().is_relative_to(ROOT):
            raise ValueError(f"Validation workflow is missing or outside the repository: {workflow}")
    if len(set(workflows)) != len(workflows):
        raise ValueError("Validation workflows must be unique")
    return workflows


def check():
    tag((ROOT / ".roc-version").read_text().strip())
    workflows = load_workflows()
    print(f"Validated compiler pin and {len(workflows)} workflow filenames")


def validate_run(item, expected_sha):
    if item["head_sha"] != expected_sha or item["head_branch"] != BRANCH or item["event"] != "workflow_dispatch":
        raise ValueError("Validation run does not belong to the candidate commit")


def validate():
    sha = os.environ["CANDIDATE_SHA"]
    workflows = load_workflows()
    runs = []
    try:
        for workflow in workflows:
            if not re.fullmatch(r"[A-Za-z0-9_-]+\.ya?ml", workflow):
                raise ValueError("Invalid workflow filename")
            if head() != sha:
                raise ValueError("Candidate branch changed before dispatch")
            dispatched = api(f"repos/{repo()}/actions/workflows/{workflow}/dispatches",
                             {"ref": BRANCH, "inputs": {"nightly_validation": True}})
            runs.append({"workflow": workflow, "id": dispatched["workflow_run_id"],
                         "html_url": dispatched["html_url"], "conclusion": None})
        deadline = time.monotonic() + 85 * 60
        while True:
            complete = True
            for item in runs:
                result = api(f"repos/{repo()}/actions/runs/{item['id']}")
                validate_run(result, sha)
                item["conclusion"] = result["conclusion"]
                complete &= result["status"] == "completed"
            output("runs", json.dumps(runs))
            if complete:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for candidate validation; see linked runs")
            time.sleep(30)
        if head() != sha:
            raise ValueError("Candidate branch changed during validation")
        if any(item["conclusion"] != "success" for item in runs):
            raise ValueError("Candidate validation did not pass")
    finally:
        output("runs", json.dumps(runs))


def report():
    sha = os.environ["CANDIDATE_SHA"]
    if head() != sha:
        raise ValueError("Refusing to report results on a different candidate")
    status = os.environ["TEST_RESULT"]
    runs = json.loads(os.environ.get("VALIDATION_RUNS") or "[]")
    expected = load_workflows()
    passed = status == "success" and [r["workflow"] for r in runs] == expected and all(r["conclusion"] == "success" for r in runs)
    message = "**Passed:** all configured validation workflows passed." if passed else f"**Needs attention:** validation finished with status `{status}`. Do not merge until all validation passes."
    save_pr(sha, tag(os.environ["NIGHTLY_TAG"]), message, runs)


def merge():
    # This checkout and the run IDs come from the trusted updater, never the PR.
    if not load_config().get("auto_merge", False):
        print("Automatic merging is disabled")
        return
    workflows = load_workflows()
    sha = os.environ["CANDIDATE_SHA"]
    base = run(["git", "rev-parse", "HEAD"])
    default = os.environ["DEFAULT_BRANCH"]
    repository = repo()
    current = existing_pr()
    if not current:
        raise ValueError("No open nightly PR")
    number = current["number"]
    current = api(f"repos/{repository}/pulls/{number}")
    if (current["state"] != "open" or current["draft"]
            or current["user"]["login"] != "github-actions[bot]"
            or current["user"]["type"] != "Bot"
            or current["head"]["repo"]["full_name"] != repository
            or current["head"]["ref"] != BRANCH or current["head"]["sha"] != sha
            or current["base"]["repo"]["full_name"] != repository
            or current["base"]["ref"] != default or current["base"]["sha"] != base
            or current["commits"] != 1 or current["changed_files"] != 1):
        raise ValueError("PR is not the trusted pin-only candidate on the current base")
    commit = api(f"repos/{repository}/commits/{sha}")
    if (len(commit["parents"]) != 1 or commit["parents"][0]["sha"] != base
            or not commit["commit"]["verification"]["verified"]
            or (commit.get("author") or {}).get("login") != "github-actions[bot]"
            or len(commit["files"]) != 1
            or commit["files"][0]["filename"] != ".roc-version"
            or commit["files"][0]["status"] != "modified"
            or commit["files"][0]["additions"] != 1 or commit["files"][0]["deletions"] != 1):
        raise ValueError("Candidate is not a verified bot pin commit directly on the tested base")
    nightly = pin_at(sha)
    if nightly != tag(os.environ["NIGHTLY_TAG"]):
        raise ValueError("Candidate pin changed")
    release = api(f"repos/roc-lang/nightlies/releases/tags/{nightly}")
    if release["tag_name"] != nightly or release["draft"] or release["prerelease"] or not release["assets"]:
        raise ValueError("Candidate is not a published upstream nightly with assets")
    runs = json.loads(os.environ.get("VALIDATION_RUNS") or "[]")
    if [item["workflow"] for item in runs] != workflows or len({item["id"] for item in runs}) != len(workflows):
        raise ValueError("Missing or duplicate validation evidence")
    for item in runs:
        if type(item["id"]) is not int or item["id"] <= 0:
            raise ValueError("Invalid validation run ID")
        result = api(f"repos/{repository}/actions/runs/{item['id']}")
        validate_run(result, sha)
        if (result["path"] != f".github/workflows/{item['workflow']}"
                or result["status"] != "completed" or result["conclusion"] != "success"):
            raise ValueError("Live validation evidence is not a successful configured workflow")
    # Strict required checks close the base-movement race at GitHub's merge API.
    # Refuse unprotected repositories rather than relying on a client-side check.
    rules = api(f"repos/{repository}/rules/branches/{default}")
    if not any(rule["type"] == "required_status_checks"
               and rule["parameters"]["strict_required_status_checks_policy"]
               and rule["parameters"]["required_status_checks"] for rule in rules):
        raise ValueError("Automatic merging requires an active strict required-status-check ruleset")
    if not any(rule["type"] == "pull_request" for rule in rules):
        raise ValueError("Automatic merging requires an active pull-request ruleset")
    if head() != sha or api(f"repos/{repository}/git/ref/heads/{default}")["object"]["sha"] != base:
        raise ValueError("Candidate or default branch changed; revalidate before merging")
    result = api(f"repos/{repository}/pulls/{number}/merge",
                 {"sha": sha, "merge_method": "squash"}, "PUT")
    if not result["merged"]:
        raise ValueError("GitHub refused the merge")
    print(f"Merged nightly PR #{number}: {result['sha']}")


if __name__ == "__main__":
    commands = {"prepare": prepare, "validate": validate, "report": report, "check": check, "merge": merge}
    try:
        if len(sys.argv) != 2 or sys.argv[1] not in commands:
            raise ValueError("Expected prepare, validate, report, check, or merge")
        commands[sys.argv[1]]()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError, TimeoutError) as error:
        # Do not print subprocess environments or authenticated git arguments.
        print(f"Nightly updater failed: {error}", file=sys.stderr)
        sys.exit(1)
