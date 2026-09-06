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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compiler_pins

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
    try:
        result = run(args, data=json.dumps(data) if data is not None else None)
    except subprocess.CalledProcessError as error:
        # Only expose GitHub's structured error message, never a subprocess
        # environment, authenticated git arguments, or arbitrary stderr.
        try:
            message = json.loads(error.stdout)["message"]
        except (ValueError, TypeError, KeyError):
            raise error
        raise ValueError(f"GitHub API rejected {endpoint}: {message}") from None
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


def require_trusted_context(*, checkout=True):
    if (os.environ.get("GITHUB_EVENT_NAME") not in {"schedule", "workflow_dispatch"}
            or os.environ.get("GITHUB_REF") != f"refs/heads/{os.environ['DEFAULT_BRANCH']}"):
        raise ValueError("Controller writes require a scheduled or manual default-branch run")
    if checkout and run(["git", "rev-parse", "HEAD"]) != os.environ["GITHUB_SHA"]:
        raise ValueError("Default branch moved since the updater started; retry on its current commit")


def sources_at(sha, config):
    paths = config.get("compiler_roots", [".roc-version"])
    sources = {}
    for path in paths:
        result = api(f"repos/{repo()}/contents/{path}?ref={sha}")
        if result.get("type", "file") != "file":
            raise ValueError("Compiler root must be an ordinary repository file")
        sources[path] = base64.b64decode(result["content"]).decode()
    return sources


def pin_at(sha, config=None):
    config = load_config() if config is None else config
    return tag(compiler_pins.version(compiler_pins.discover(sources_at(sha, config))))


def verify_header_candidate(base, sha, files, nightly, config):
    pins = compiler_pins.discover(sources_at(base, config))
    expected = compiler_pins.replace(pins, nightly)
    if (len(files) != len(expected) or {item["filename"] for item in files} != set(expected)
            or any(item.get("status") != "modified" for item in files)):
        raise ValueError("Candidate changes files outside the compiler header pins")
    if sources_at(sha, config) != expected:
        raise ValueError("Candidate contains changes beyond compiler pin literals")


def head():
    return api(f"repos/{repo()}/git/ref/heads/{BRANCH}")["object"]["sha"]


def existing_pr():
    owner = repo().split("/")[0]
    prs = api(f"repos/{repo()}/pulls?state=open&head={owner}:{BRANCH}")
    if len(prs) > 1:
        raise ValueError("More than one nightly PR")
    return prs[0] if prs else None


def pr_body(sha, nightly, status, runs=()):
    lines = [f"Updates the selected compiler pins to [{nightly}](https://github.com/roc-lang/nightlies/releases/tag/{nightly}).",
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
    config = load_config()
    sources = compiler_pins.local_sources(ROOT, config.get("compiler_roots"))
    changes = compiler_pins.replace(compiler_pins.discover(sources), nightly)
    request = {"query": """mutation($input: CreateCommitOnBranchInput!) {
      createCommitOnBranch(input: $input) { commit { oid } }
    }""", "variables": {"input": {
        "branch": {"repositoryNameWithOwner": repo(), "branchName": BRANCH},
        "expectedHeadOid": base,
        "message": {"headline": f"Update Roc nightly to {nightly}"},
        "fileChanges": {"additions": [{"path": path,
            "contents": base64.b64encode(source.encode()).decode()} for path, source in changes.items()]},
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
    config = load_config()
    pins = compiler_pins.discover(compiler_pins.local_sources(ROOT, config.get("compiler_roots")))
    if nightly == tag(compiler_pins.version(pins)):
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
        if len(commit["parents"]) != 1 or (not config.get("compiler_roots") and [f["filename"] for f in commit["files"]] != [".roc-version"]):
            raise ValueError("Nightly branch contains changes other than a pin commit")
        if config.get("compiler_roots"):
            verify_header_candidate(commit["parents"][0]["sha"], old, commit["files"], pin_at(old, config), config)
        same = commit["parents"][0]["sha"] == base and pin_at(old, config) == nightly
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



def load_config(content=None):
    config = json.loads(content if content is not None else (ROOT / ".github/roc-nightly.json").read_text())
    if not isinstance(config, dict) or "workflows" not in config or set(config) - {"workflows", "auto_merge", "compiler_roots"}:
        raise ValueError("Expected workflows and optional auto_merge configuration")
    if type(config.get("auto_merge", False)) is not bool:
        raise ValueError("auto_merge must be a boolean")
    if "compiler_roots" in config:
        compiler_pins.validate_paths(config["compiler_roots"])
    return config


def load_workflows(config=None, *, check_files=True):
    config = load_config() if config is None else config
    workflows = config["workflows"]
    if not isinstance(workflows, list) or not workflows:
        raise ValueError("Validation workflows must be a nonempty list")
    for workflow in workflows:
        if not isinstance(workflow, str) or not re.fullmatch(r"[A-Za-z0-9_-]+\.ya?ml", workflow):
            raise ValueError("Invalid workflow filename")
        path = ROOT / ".github/workflows" / workflow
        if check_files and (not path.is_file() or not path.resolve().is_relative_to(ROOT)):
            raise ValueError(f"Validation workflow is missing or outside the repository: {workflow}")
    if len(set(workflows)) != len(workflows):
        raise ValueError("Validation workflows must be unique")
    return workflows


def check():
    config = load_config()
    compiler_pins.discover(compiler_pins.local_sources(ROOT, config.get("compiler_roots")))
    workflows = load_workflows()
    print(f"Validated compiler pin and {len(workflows)} workflow filenames")


def validate_run(item, expected_sha):
    if item["head_sha"] != expected_sha or item["head_branch"] != BRANCH or item["event"] != "workflow_dispatch":
        raise ValueError("Validation run does not belong to the candidate commit")


def validate():
    sha = os.environ["CANDIDATE_SHA"]
    workflows = load_workflows()
    contexts = required_contexts() if load_config().get("auto_merge", False) else []
    publish_statuses(sha, contexts, "pending")
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
        if contexts:
            verify_required_jobs(runs, contexts)
            publish_statuses(sha, contexts, "success")
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


def required_contexts():
    rules = api(f"repos/{repo()}/rules/branches/{os.environ['DEFAULT_BRANCH']}")
    if not any(rule["type"] == "required_status_checks"
               and rule["parameters"]["strict_required_status_checks_policy"]
               and rule["parameters"]["required_status_checks"] for rule in rules):
        raise ValueError("Automatic merging requires an active strict required-status-check ruleset")
    if not any(rule["type"] == "pull_request" for rule in rules):
        raise ValueError("Automatic merging requires an active pull-request ruleset")
    contexts = []
    for rule in rules:
        if rule["type"] != "required_status_checks":
            continue
        for check in rule["parameters"]["required_status_checks"]:
            if check.get("integration_id") not in {None, 15368}:
                raise ValueError("Nightly status reporting only supports required GitHub Actions checks")
            if check["context"] not in contexts:
                contexts.append(check["context"])
    return contexts


def verify_required_jobs(runs, contexts):
    jobs = []
    for item in runs:
        page = 1
        collected = []
        while True:
            result = api(f"repos/{repo()}/actions/runs/{item['id']}/jobs?filter=latest&per_page=100&page={page}")
            collected.extend(result["jobs"])
            if len(collected) >= result["total_count"]:
                break
            if not result["jobs"]:
                raise ValueError("Incomplete validation job evidence")
            page += 1
        jobs.extend(collected)
    for context in contexts:
        matching = [job for job in jobs if job["name"] == context]
        if not matching or any(job["status"] != "completed" or job["conclusion"] != "success" for job in matching):
            raise ValueError(f"Required check has no successful validation job: {context}")


def publish_statuses(sha, contexts, state):
    # Dispatched check runs are not always associated with bot-created PRs.
    # Mirror real job results using the same Actions identity and check names.
    for context in contexts:
        api(f"repos/{repo()}/statuses/{sha}", {
            "context": context, "state": state,
            "description": "Nightly candidate validation " + ("passed" if state == "success" else "in progress"),
            "target_url": f"{os.environ['GITHUB_SERVER_URL']}/{repo()}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
        })


def merge():
    # No consumer checkout in this privileged job. Read policy at the original
    # trusted event SHA, not at the candidate or a moving branch reference.
    base = os.environ["GITHUB_SHA"]
    repository = repo()
    contents = api(f"repos/{repository}/contents/.github/roc-nightly.json?ref={base}")
    config = load_config(base64.b64decode(contents["content"]).decode())
    if not config.get("auto_merge", False):
        print("Automatic merging is disabled")
        return
    workflows = load_workflows(config, check_files=False)
    sha = os.environ["CANDIDATE_SHA"]
    default = os.environ["DEFAULT_BRANCH"]
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
            or current["commits"] != 1 or current["changed_files"] != len(config.get("compiler_roots", [".roc-version"]))):
        raise ValueError("PR is not the trusted pin-only candidate on the current base")
    commit = api(f"repos/{repository}/commits/{sha}")
    if (len(commit["parents"]) != 1 or commit["parents"][0]["sha"] != base
            or not commit["commit"]["verification"]["verified"]
            or (commit.get("author") or {}).get("login") != "github-actions[bot]"
            or (not config.get("compiler_roots") and (len(commit["files"]) != 1
            or commit["files"][0]["filename"] != ".roc-version"
            or commit["files"][0]["status"] != "modified"
            or commit["files"][0]["additions"] != 1 or commit["files"][0]["deletions"] != 1))):
        raise ValueError("Candidate is not a verified bot pin commit directly on the tested base")
    nightly = pin_at(sha, config)
    if config.get("compiler_roots"):
        verify_header_candidate(base, sha, commit["files"], nightly, config)
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
    contexts = required_contexts()
    verify_required_jobs(runs, contexts)
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
        if sys.argv[1] != "check":
            require_trusted_context(checkout=sys.argv[1] != "merge")
        commands[sys.argv[1]]()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError, TimeoutError) as error:
        # Do not print subprocess environments or authenticated git arguments.
        print(f"Nightly updater failed: {error}", file=sys.stderr)
        sys.exit(1)
