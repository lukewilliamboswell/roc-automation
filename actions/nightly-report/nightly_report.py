#!/usr/bin/env python3
"""Keep the one tracking issue of a nightly compatibility workflow."""

from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from typing import Any, Callable, Iterator, Mapping

DEFAULT_TITLE = "The latest release fails on the latest Roc nightly"
# Tags are written into issue text, so they are limited to characters that are
# inert in Markdown: no whitespace, backticks, brackets, `@` or `#`.
TAG = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+/-]{0,99}")
REPOSITORY = re.compile(r"[0-9A-Za-z][0-9A-Za-z-]{0,38}/[0-9A-Za-z._-]{1,100}")
HTTPS_URL = re.compile(r"https://[0-9A-Za-z.-]+(:[0-9]+)?(/[0-9A-Za-z._/-]*)?")
RUN_ID = re.compile(r"[1-9][0-9]{0,19}")
MAX_TITLE = 256  # GitHub's own limit for an issue title
PER_PAGE = 100
MAX_PAGES = 100

# (method, path below the API root, JSON body or None) -> decoded JSON response
Api = Callable[[str, str, "dict[str, Any] | None"], Any]


def classify(outcome: str) -> str:
    """Only an examples step that ran to a verdict is evidence about compatibility."""
    if outcome in ("success", "failure"):
        return outcome
    raise ValueError(
        f"setup failure: the examples step reported outcome {outcome!r:.60}, not success or failure; "
        "the examples did not run to a verdict, so the tracking issue is left alone"
    )


def checked_tag(name: str, value: str) -> str:
    if not TAG.fullmatch(value):
        raise ValueError(f"{name} must be a tag of at most 100 letters, digits and . _ + / - characters, starting with a letter or digit")
    return value


def checked_title(title: str) -> str:
    if not title or title != title.strip():
        raise ValueError("title must not be empty or begin or end with whitespace")
    if len(title) > MAX_TITLE:
        raise ValueError(f"title must be at most {MAX_TITLE} characters")
    # Category C covers control, format (including bidirectional overrides),
    # surrogate, private-use and unassigned code points.
    if any(unicodedata.category(character).startswith("C") for character in title):
        raise ValueError("title must not contain control or format characters")
    return title


def run_url(server: str, repository: str, run_id: str) -> str:
    if not HTTPS_URL.fullmatch(server):
        raise ValueError("GITHUB_SERVER_URL must be an https URL")
    if not REPOSITORY.fullmatch(repository):
        raise ValueError("GITHUB_REPOSITORY must be an owner/name pair")
    if not RUN_ID.fullmatch(run_id):
        raise ValueError("GITHUB_RUN_ID must be a run number")
    return f"{server.rstrip('/')}/{repository}/actions/runs/{run_id}"


def open_issues(api: Api, repository: str) -> Iterator[dict[str, Any]]:
    """Every open issue, oldest first. The endpoint also lists pull requests."""
    for page in range(1, MAX_PAGES + 1):
        items = api("GET", f"/repos/{repository}/issues?state=open&sort=created&direction=asc&per_page={PER_PAGE}&page={page}", None)
        if not isinstance(items, list):
            raise ValueError("GitHub returned something other than a list of issues")
        yield from items
        if len(items) < PER_PAGE:
            return
    raise ValueError(f"more than {MAX_PAGES * PER_PAGE} open issues; refusing to guess whether a tracking issue exists")


def is_tracking_issue(issue: Any, title: str) -> bool:
    """An issue, not a pull request, with exactly this title, opened by a bot account.

    `user.type` is assigned by GitHub and is `Bot` for `github-actions[bot]` and for
    every GitHub App; no person can open an issue as one. The login is not compared,
    because `token` may belong to an App and the action must still recognise the issue
    that token opened.
    """
    if not isinstance(issue, dict) or "pull_request" in issue:
        return False
    user = issue.get("user")
    return (
        issue.get("state") == "open"
        and issue.get("title") == title
        and isinstance(user, dict)
        and user.get("type") == "Bot"
        and isinstance(issue.get("number"), int)
        and not isinstance(issue.get("number"), bool)
    )


def tracking_issue(api: Api, repository: str, title: str) -> int | None:
    """The number of the oldest open tracking issue, if there is one."""
    numbers = [issue["number"] for issue in open_issues(api, repository) if is_tracking_issue(issue, title)]
    return min(numbers) if numbers else None


def issue_body(nightly: str, release: str, url: str) -> str:
    return (
        f"The examples of release `{release}` fail on Roc nightly `{nightly}`.\n\n"
        f"Workflow run: {url}\n\n"
        "The nightly compatibility workflow maintains this issue. Each later failure adds a comment, "
        "and the next passing run closes it. The fix is a patch release that works with the new compiler.\n"
    )


def failure_comment(nightly: str, release: str, url: str) -> str:
    return f"Still failing: release `{release}` on Roc nightly `{nightly}`.\n\nWorkflow run: {url}\n"


def success_comment(nightly: str, release: str, url: str) -> str:
    return f"Release `{release}` passes on Roc nightly `{nightly}`. Closing.\n\nWorkflow run: {url}\n"


def report(api: Api, repository: str, outcome: str, nightly: str, release: str, title: str, url: str) -> tuple[str, int | None]:
    """Apply one run's verdict. Returns the action taken and the issue it concerned."""
    verdict = classify(outcome)
    checked_tag("nightly-tag", nightly)
    checked_tag("release-tag", release)
    checked_title(title)
    number = tracking_issue(api, repository, title)
    if verdict == "success":
        if number is None:
            return "none", None
        api("POST", f"/repos/{repository}/issues/{number}/comments", {"body": success_comment(nightly, release, url)})
        api("PATCH", f"/repos/{repository}/issues/{number}", {"state": "closed", "state_reason": "completed"})
        return "closed", number
    if number is not None:
        api("POST", f"/repos/{repository}/issues/{number}/comments", {"body": failure_comment(nightly, release, url)})
        return "commented", number
    created = api("POST", f"/repos/{repository}/issues", {"title": title, "body": issue_body(nightly, release, url)})
    number = created.get("number") if isinstance(created, dict) else None
    if not isinstance(number, int) or isinstance(number, bool):
        raise ValueError("GitHub did not return the number of the issue it created")
    return "opened", number


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would resend the token to wherever the response points."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def github_api(api_url: str, token: str, send: Callable[..., Any] | None = None) -> Api:
    if not HTTPS_URL.fullmatch(api_url):
        raise ValueError("GITHUB_API_URL must be an https URL")
    if not token or any(character.isspace() for character in token):
        raise ValueError("token must not be empty or contain whitespace")
    send = send or urllib.request.build_opener(_NoRedirect).open
    root = api_url.rstrip("/")

    def api(method: str, path: str, body: dict[str, Any] | None) -> Any:
        request = urllib.request.Request(
            root + path,
            method=method,
            data=None if body is None else json.dumps(body).encode("utf-8"),
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "roc-automation-nightly-report",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        resource = path.split("?", 1)[0]
        try:
            with send(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise ValueError(f"GitHub answered {method} {resource} with HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"{method} {resource} failed: {type(error).__name__}") from error

    return api


def main(env: Mapping[str, str] | None = None, connect: Callable[[str, str], Api] | None = None) -> None:
    env = os.environ if env is None else env
    # The verdict is settled before anything else, so a setup failure reaches no API.
    outcome = env.get("INPUT_OUTCOME", "")
    classify(outcome)
    nightly = checked_tag("nightly-tag", env.get("INPUT_NIGHTLY_TAG", ""))
    release = checked_tag("release-tag", env.get("INPUT_RELEASE_TAG", ""))
    title = checked_title(env.get("INPUT_TITLE", ""))
    repository = env.get("GITHUB_REPOSITORY", "")
    url = run_url(env.get("GITHUB_SERVER_URL", ""), repository, env.get("GITHUB_RUN_ID", ""))
    api = (connect or github_api)(env.get("GITHUB_API_URL", ""), env.get("INPUT_TOKEN", ""))
    action, number = report(api, repository, outcome, nightly, release, title, url)
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"action={action}\nissue-number={'' if number is None else number}\n")
    print(f"Release {release} on nightly {nightly}: {outcome}; tracking issue {'#' + str(number) if number else 'absent'}; action {action}")


def workflow_command_text(message: str) -> str:
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


if __name__ == "__main__":
    try:
        main()
    except ValueError as error:
        print(f"::error::{workflow_command_text(str(error))}", file=sys.stderr)
        raise SystemExit(1) from error
