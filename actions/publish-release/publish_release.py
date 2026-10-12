#!/usr/bin/env python3
"""Stage a draft release, verify every uploaded asset against the tested file, then publish."""

from __future__ import annotations

import glob
import hashlib
import hmac
import json
import os
import re
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# The same rule as VERSION in actions/setup-blueprint/setup_blueprint.py.
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z]+(\.[0-9A-Za-z]+)*)?")
TAG_PREFIX = re.compile(r"[A-Za-z0-9]+([._-][A-Za-z0-9]+)*")
COMMIT = re.compile(r"[0-9a-f]{40}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
# GitHub rewrites other characters in an asset name, which would publish a file under a name nobody tested.
ASSET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
MAX_NOTES_CHARACTERS = 125000
REDIRECTS = (301, 302, 303, 307, 308)
USER_AGENT = "roc-automation-publish-release"

Send = Callable[[urllib.request.Request, int], object]


@dataclass(frozen=True)
class Asset:
    path: Path
    size: int
    sha256: str


@dataclass(frozen=True)
class Plan:
    """Everything decided from the inputs and the local files, before any request is made."""

    version: str
    tag: str
    prerelease: bool
    target: str
    notes: str
    assets: dict[str, Asset]


def is_prerelease_version(version: str) -> bool:
    match = VERSION.fullmatch(version)
    if not match:
        raise ValueError("version must be a release version such as 0.12.0 or 0.12.0-rc1")
    return match.group(4) is not None


def checked_prerelease(value: str, version: str) -> bool:
    if value not in ("true", "false"):
        raise ValueError("prerelease must be exactly true or false")
    prerelease = value == "true"
    if prerelease != is_prerelease_version(version):
        if prerelease:
            raise ValueError(f"prerelease is true but {version} has no prerelease suffix")
        raise ValueError(f"prerelease is false but {version} is a prerelease version")
    return prerelease


def checked_tag(tag: str, version: str) -> str:
    """The tag is the version, or the version behind a package name: 0.12.0 or kit-0.12.0."""
    is_prerelease_version(version)
    if tag in ("", version):
        return version
    suffix = "-" + version
    if not tag.endswith(suffix) or not TAG_PREFIX.fullmatch(tag[: -len(suffix)]):
        raise ValueError(f"tag must be {version} or <name>-{version}, where <name> is letters and digits joined by single '.', '_' or '-'")
    return tag


def checked_target(target: str) -> str:
    if not COMMIT.fullmatch(target):
        raise ValueError("target must be a full 40-character lowercase commit SHA")
    return target


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def regular_file(path: Path, description: str) -> os.stat_result:
    status = path.lstat()
    if stat.S_ISLNK(status.st_mode):
        raise ValueError(f"{description} is a symbolic link: {path}")
    if not stat.S_ISREG(status.st_mode):
        raise ValueError(f"{description} is not a regular file: {path}")
    return status


def expand_assets(patterns: str, root: Path) -> dict[str, Asset]:
    """Expand one glob per line. Every glob must match, and every match must be a distinct regular file."""
    paths: dict[Path, None] = {}
    for line in patterns.splitlines():
        pattern = line.strip()
        if not pattern:
            continue
        matches = sorted(glob.glob(pattern, root_dir=root, recursive=True))
        if not matches:
            raise ValueError(f"assets glob matched no file: {pattern}")
        for match in matches:
            paths[Path(os.path.normpath(root / match))] = None
    if not paths:
        raise ValueError("assets names no file")
    assets: dict[str, Asset] = {}
    for path in sorted(paths):
        status = regular_file(path, "asset")
        if path.name in assets:
            raise ValueError(f"two assets share the name {path.name}: {assets[path.name].path} and {path}")
        if not ASSET_NAME.fullmatch(path.name):
            raise ValueError(f"asset name must use only letters, digits, '.', '_' and '-': {path.name}")
        if status.st_size == 0:
            raise ValueError(f"asset is empty: {path}")
        assets[path.name] = Asset(path, status.st_size, sha256_of(path))
    return assets


def read_notes(path: Path) -> str:
    try:
        regular_file(path, "notes-file")
        notes = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ValueError(f"notes-file does not exist: {path}") from None
    except UnicodeDecodeError:
        raise ValueError(f"notes-file is not UTF-8 text: {path}") from None
    if not notes.strip():
        raise ValueError(f"notes-file is empty: {path}")
    if len(notes) > MAX_NOTES_CHARACTERS:
        raise ValueError(f"notes-file is longer than the {MAX_NOTES_CHARACTERS} characters a release body may hold")
    return notes


def make_plan(env: dict[str, str], root: Path) -> Plan:
    version = env.get("INPUT_VERSION", "")
    prerelease = checked_prerelease(env.get("INPUT_PRERELEASE", ""), version)
    tag = checked_tag(env.get("INPUT_TAG", ""), version)
    target = checked_target(env.get("INPUT_TARGET", ""))
    if not env.get("INPUT_NOTES_FILE"):
        raise ValueError("notes-file is required")
    notes = read_notes(root / env["INPUT_NOTES_FILE"])
    assets = expand_assets(env.get("INPUT_ASSETS", ""), root)
    return Plan(version, tag, prerelease, target, notes, assets)


def digest_listing(assets: dict[str, Asset]) -> str:
    """The listing `sha256sum` prints, and `sha256sum --check` reads, in name order."""
    return "".join(f"{assets[name].sha256}  {name}\n" for name in sorted(assets))


def check_inventory(assets: dict[str, Asset], records: list[dict]) -> None:
    """The draft must hold exactly the planned names, each fully uploaded and of the tested size."""
    names = [record["name"] for record in records]
    missing = sorted(set(assets) - set(names))
    extra = sorted(set(names) - set(assets))
    if missing:
        raise ValueError(f"the draft is missing {', '.join(missing)}")
    if extra:
        raise ValueError(f"the draft holds {', '.join(extra)}, which is not a tested file")
    if len(names) != len(assets):
        raise ValueError("the draft lists an asset name more than once")
    for record in records:
        if record.get("state") != "uploaded":
            raise ValueError(f"the upload of {record['name']} did not complete")
        if record.get("size") != assets[record["name"]].size:
            raise ValueError(f"{record['name']} in the draft differs in size from the tested file")


class GitHub:
    """The REST calls a release needs. `send` is the only place a request leaves the process."""

    def __init__(self, api_url: str, repository: str, token: str, send: Send | None = None):
        if not api_url.startswith("https://"):
            raise ValueError("the GitHub API URL must use https")
        if not REPOSITORY.fullmatch(repository) or {".", ".."} & set(repository.split("/")):
            raise ValueError("invalid repository")
        if not token:
            raise ValueError("token is required")
        self.base = f"{api_url.rstrip('/')}/repos/{repository}"
        self.token = token
        self.send = send or no_redirect_send

    def _open(self, method: str, url: str, *, data=None, headers: dict[str, str] | None = None, timeout: int = 120):
        """Raises urllib.error.HTTPError for every status other than success, including a redirect."""
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("User-Agent", USER_AGENT)
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("X-GitHub-Api-Version", "2026-03-10")
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        return self.send(request, timeout)

    def _json(self, method: str, url: str, body: dict | None = None, *, missing_ok: bool = False, **options):
        headers = {"Accept": "application/vnd.github+json", **options.pop("headers", {})}
        data = options.pop("data", None)
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        try:
            with self._open(method, url, data=data, headers=headers, **options) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            if missing_ok and error.code == 404:
                return None
            raise ValueError(f"GitHub answered {error.code} to {method} {url.split('?')[0]}: {rejection(error)}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            raise ValueError(f"{method} {url.split('?')[0]} failed: {error}") from None

    def _pages(self, url: str) -> list[dict]:
        items: list[dict] = []
        page = 1
        while True:
            batch = self._json("GET", f"{url}?per_page=100&page={page}")
            items.extend(batch)
            if len(batch) < 100:
                return items
            page += 1

    def commit_exists(self, sha: str) -> bool:
        return self._json("GET", f"{self.base}/git/commits/{sha}", missing_ok=True) is not None

    def tag_exists(self, tag: str) -> bool:
        return self._json("GET", f"{self.base}/git/ref/tags/{urllib.parse.quote(tag, safe='')}", missing_ok=True) is not None

    def releases_for_tag(self, tag: str) -> list[dict]:
        """Published releases and drafts alike. A draft has no tag yet, so only the list shows it."""
        return [release for release in self._pages(f"{self.base}/releases") if release.get("tag_name") == tag]

    def create_draft(self, tag: str, target: str, notes: str, prerelease: bool) -> dict:
        return self._json("POST", f"{self.base}/releases", {
            "tag_name": tag, "target_commitish": target, "name": tag, "body": notes,
            "draft": True, "prerelease": prerelease, "make_latest": "false",
        })

    def upload_asset(self, release: dict, name: str, path: Path, size: int) -> dict:
        upload_url = str(release.get("upload_url", "")).split("{")[0]
        if not upload_url.startswith("https://"):
            raise ValueError("the draft has no https upload URL")
        with path.open("rb") as source:
            return self._json(
                "POST", f"{upload_url}?name={urllib.parse.quote(name, safe='')}", data=source, timeout=1800,
                headers={"Content-Type": "application/octet-stream", "Content-Length": str(size)},
            )

    def list_assets(self, release_id: int) -> list[dict]:
        return self._pages(f"{self.base}/releases/{int(release_id)}/assets")

    def download_asset(self, asset_id: int, destination: Path, limit: int) -> None:
        """Write at most `limit` + 1 bytes to a new file. The token is not sent to the storage host."""
        url = f"{self.base}/releases/assets/{int(asset_id)}"
        try:
            try:
                response = self._open("GET", url, headers={"Accept": "application/octet-stream"}, timeout=600)
            except urllib.error.HTTPError as error:
                location = error.headers.get("Location", "") if error.code in REDIRECTS else ""
                if not location.startswith("https://"):
                    raise
                request = urllib.request.Request(location, headers={"User-Agent": USER_AGENT})
                response = self.send(request, 600)
            with response, destination.open("xb") as output:
                remaining = limit + 1
                while remaining > 0 and (chunk := response.read(min(remaining, 1024 * 1024))):
                    output.write(chunk)
                    remaining -= len(chunk)
        except urllib.error.HTTPError as error:
            raise ValueError(f"GitHub answered {error.code} to the download of asset {asset_id}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            raise ValueError(f"the download of asset {asset_id} failed: {error}") from None

    def get_release(self, release_id: int) -> dict:
        return self._json("GET", f"{self.base}/releases/{int(release_id)}")

    def publish(self, release_id: int, prerelease: bool) -> dict:
        return self._json("PATCH", f"{self.base}/releases/{int(release_id)}", {
            "draft": False, "prerelease": prerelease, "make_latest": "false" if prerelease else "true",
        })


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def no_redirect_send(request: urllib.request.Request, timeout: int):
    """urllib would repeat the Authorization header to the redirect's host, so no redirect is followed here."""
    return urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout)


def rejection(error: urllib.error.HTTPError) -> str:
    try:
        message = json.loads(error.read())["message"]
    except (ValueError, TypeError, KeyError, OSError):
        message = error.reason
    return " ".join(str(message).split())


def stage(plan: Plan, github, release: dict, downloads: Path) -> None:
    """Upload every tested file to the draft, then read each one back and compare it."""
    for name in sorted(plan.assets):
        asset = plan.assets[name]
        uploaded = github.upload_asset(release, name, asset.path, asset.size)
        if uploaded.get("name") != name:
            raise ValueError(f"GitHub stored {name} as {uploaded.get('name')}")
    records = github.list_assets(release["id"])
    check_inventory(plan.assets, records)
    for record in sorted(records, key=lambda item: item["name"]):
        asset = plan.assets[record["name"]]
        downloaded = downloads / record["name"]
        github.download_asset(record["id"], downloaded, asset.size)
        if downloaded.stat().st_size != asset.size or not hmac.compare_digest(sha256_of(downloaded), asset.sha256):
            raise ValueError(f"{record['name']} downloaded from the draft differs from the tested file")


def publish_release(plan: Plan, github, downloads: Path) -> dict:
    """Return the published release. Every check precedes publication, which is what makes a release immutable."""
    if not github.commit_exists(plan.target):
        raise ValueError(f"target {plan.target} is not a commit in this repository")
    if github.tag_exists(plan.tag):
        raise ValueError(f"the tag {plan.tag} already exists; a version is never published twice")
    existing = github.releases_for_tag(plan.tag)
    if existing:
        kind = "draft release" if existing[0].get("draft") else "release"
        raise ValueError(f"a {kind} for {plan.tag} already exists: {existing[0].get('html_url')}")

    release = github.create_draft(plan.tag, plan.target, plan.notes, plan.prerelease)
    draft = f"the draft release {release.get('html_url')} (id {release.get('id')})"
    try:
        if release.get("draft") is not True or release.get("tag_name") != plan.tag:
            raise ValueError("GitHub did not create the requested draft")
        stage(plan, github, release, downloads)
    except (ValueError, OSError) as error:
        raise ValueError(
            f"{error}. Nothing was published: {draft} is left unpublished and the tag {plan.tag} has not been created. "
            "Inspect the draft, delete it, then run this job again"
        ) from error

    # A publish request can take effect even when its answer is lost, so the release is read back either way.
    failure = None
    try:
        github.publish(release["id"], plan.prerelease)
    except ValueError as error:
        failure = error
    try:
        published = github.get_release(release["id"])
    except ValueError as error:
        raise ValueError(
            f"{failure or error}. The release could not be read after the publish request, so {draft} may or may not be "
            f"published. Look at the releases page: if {plan.tag} is published, do not run this job again"
        ) from error
    if published.get("draft") is not False:
        raise ValueError(
            f"{failure or 'GitHub accepted the publish request without publishing'}. Nothing was published: {draft} is "
            f"verified but left unpublished and the tag {plan.tag} has not been created. Delete the draft, then run this job again"
        )
    url = published.get("html_url")
    if published.get("tag_name") != plan.tag or published.get("prerelease") is not plan.prerelease:
        raise ValueError(f"{url} is published, but not as the {'prerelease' if plan.prerelease else 'release'} {plan.tag}; do not run this job again")
    if published.get("immutable") is not True:
        raise ValueError(
            f"{url} is published with the verified assets, but it is not immutable. Enable immutable releases in the "
            "repository settings; do not run this job again"
        )
    return published


def summary(plan: Plan, url: str) -> str:
    kind = "Prerelease" if plan.prerelease else "Release"
    return f"### {kind} {plan.tag}\n\n{url}\n\nSHA-256 of each published asset, verified against the tested files:\n\n```\n{digest_listing(plan.assets)}```\n"


def main() -> None:
    env = os.environ
    plan = make_plan(dict(env), Path.cwd())
    github = GitHub(env.get("GITHUB_API_URL") or "https://api.github.com", env.get("GITHUB_REPOSITORY", ""), env.get("INPUT_TOKEN", ""))
    work = Path(tempfile.mkdtemp(prefix="roc-automation-publish-release-", dir=env["RUNNER_TEMP"]))
    downloads = work / "downloaded"
    downloads.mkdir()
    listing = work / "sha256sums.txt"
    listing.write_text(digest_listing(plan.assets), encoding="utf-8")
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"digests={listing}\n")
    published = publish_release(plan, github, downloads)
    url = published["html_url"]
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"release-url={url}\n")
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as step_summary:
            step_summary.write(summary(plan, url))
    print(f"Published {url}")
    print(digest_listing(plan.assets), end="")


if __name__ == "__main__":
    try:
        main()
    except ValueError as error:
        print(f"::error::{' '.join(str(error).split())}", file=sys.stderr)
        raise SystemExit(1) from error
