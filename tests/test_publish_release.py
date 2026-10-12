import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("publish_release", Path(__file__).resolve().parents[1] / "actions/publish-release/publish_release.py")
publisher = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = publisher
spec.loader.exec_module(publisher)

TARGET = "a" * 40
BUNDLE = "5Yr6CbZ1hPrVvXkEDkPjYkEkPQ9x.tar.zst"
FILES = {
    f"candidate/dist/{BUNDLE}": b"bundle bytes",
    "candidate/dist/example-examples-0.12.0.zip": b"examples bytes",
    "candidate/.docs-out/example-0.12.0.pdf": b"%PDF manual",
    "candidate/.release/release-notes.md": b"# example 0.12.0\n\nStreaming decoders.\n",
}
GLOBS = "candidate/dist/*\ncandidate/.docs-out/*.pdf\n"
NAMES = sorted([BUNDLE, "example-examples-0.12.0.zip", "example-0.12.0.pdf"])


def inputs(**changes):
    env = {
        "INPUT_VERSION": "0.12.0",
        "INPUT_PRERELEASE": "false",
        "INPUT_NOTES_FILE": "candidate/.release/release-notes.md",
        "INPUT_ASSETS": GLOBS,
        "INPUT_TAG": "",
        "INPUT_TARGET": TARGET,
    }
    env.update(changes)
    return env


class FakeGitHub:
    """An in-memory repository. `calls` records every request in order; no test touches the network."""

    def __init__(self, tags=(), releases=(), immutable=True):
        self.calls = []
        self.tags = set(tags)
        self.releases = {release["id"]: dict(release) for release in releases}
        self.stored = {}
        self.immutable = immutable
        self.corrupt = None
        self.drop = None
        self.extra = None
        self.fail = {}

    def _call(self, name, *details):
        self.calls.append((name, *details))
        if name in self.fail:
            raise ValueError(self.fail[name])

    def commit_exists(self, sha):
        self._call("commit_exists", sha)
        return sha == TARGET

    def tag_exists(self, tag):
        self._call("tag_exists", tag)
        return tag in self.tags

    def releases_for_tag(self, tag):
        self._call("releases_for_tag", tag)
        return [release for release in self.releases.values() if release["tag_name"] == tag]

    def create_draft(self, tag, target, notes, prerelease):
        self._call("create_draft", tag, target, prerelease)
        self.notes = notes
        release = {
            "id": 7, "tag_name": tag, "target_commitish": target, "draft": True, "prerelease": prerelease,
            "html_url": "https://github.com/o/r/releases/tag/untagged-1f2e", "immutable": False,
        }
        self.releases[7] = release
        return dict(release)

    def upload_asset(self, release, name, path, size):
        self._call("upload_asset", name)
        self.stored[name] = path.read_bytes()
        return {"name": name}

    def list_assets(self, release_id):
        self._call("list_assets", release_id)
        names = [name for name in self.stored if name != self.drop]
        records = [{"id": 100 + index, "name": name, "size": len(self.stored[name]), "state": "uploaded"} for index, name in enumerate(sorted(names))]
        if self.extra:
            records.append({"id": 999, "name": self.extra, "size": 1, "state": "uploaded"})
        self.ids = {record["id"]: record["name"] for record in records}
        return records

    def download_asset(self, asset_id, destination, limit):
        name = self.ids[asset_id]
        self._call("download_asset", name)
        content = self.stored[name]
        if name == self.corrupt:
            content = bytes([content[0] ^ 1]) + content[1:]
        with destination.open("xb") as output:
            output.write(content[: limit + 1])

    def publish(self, release_id, prerelease):
        self._call("publish", release_id, prerelease)
        release = self.releases[release_id]
        release.update(draft=False, prerelease=prerelease, immutable=self.immutable, html_url=f"https://github.com/o/r/releases/tag/{release['tag_name']}")
        self.tags.add(release["tag_name"])
        self.latest = not prerelease

    def get_release(self, release_id):
        self._call("get_release", release_id)
        return dict(self.releases[release_id])

    def names(self):
        return [call[0] for call in self.calls]


class Workspace(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "workspace"
        self.downloads = Path(temporary.name) / "downloaded"
        self.downloads.mkdir()
        for name, content in FILES.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

    def plan(self, **changes):
        return publisher.make_plan(inputs(**changes), self.root)

    def run_publish(self, github, **changes):
        return publisher.publish_release(self.plan(**changes), github, self.downloads)


class InputTests(Workspace):
    def test_plan_names_the_tested_files_and_their_digests(self):
        plan = self.plan()
        self.assertEqual((plan.version, plan.tag, plan.prerelease, plan.target), ("0.12.0", "0.12.0", False, TARGET))
        self.assertEqual(sorted(plan.assets), NAMES)
        self.assertEqual(plan.assets[BUNDLE].sha256, hashlib.sha256(b"bundle bytes").hexdigest())
        self.assertEqual(plan.notes, FILES["candidate/.release/release-notes.md"].decode())

    def test_malformed_versions_are_rejected(self):
        for version in ("", "latest", "v0.12.0", "0.12", "0.12.0.1", "0.012.0", "0.12.0 ", " 0.12.0", "0.12.0\n0.13.0", "0.12.0-", "0.12.0-rc_1", "0.12.0+build", "0.12.0/x", "main"):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "version must be"):
                self.plan(INPUT_VERSION=version)

    def test_prerelease_is_exactly_true_or_false(self):
        for value in ("", "True", "FALSE", "1", "0", "yes", "true ", " false", "true\n"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "exactly true or false"):
                self.plan(INPUT_PRERELEASE=value)

    def test_prerelease_must_agree_with_the_version(self):
        with self.assertRaisesRegex(ValueError, "prerelease is false but 0.12.0-rc1 is a prerelease version"):
            self.plan(INPUT_VERSION="0.12.0-rc1", INPUT_PRERELEASE="false")
        with self.assertRaisesRegex(ValueError, "prerelease is true but 0.12.0 has no prerelease suffix"):
            self.plan(INPUT_VERSION="0.12.0", INPUT_PRERELEASE="true")
        self.assertTrue(self.plan(INPUT_VERSION="0.12.0-rc1", INPUT_PRERELEASE="true").prerelease)
        self.assertTrue(self.plan(INPUT_VERSION="0.12.0-rc.2", INPUT_PRERELEASE="true").prerelease)

    def test_tag_defaults_to_the_version_and_may_carry_a_package_name(self):
        self.assertEqual(self.plan().tag, "0.12.0")
        self.assertEqual(self.plan(INPUT_TAG="0.12.0").tag, "0.12.0")
        self.assertEqual(self.plan(INPUT_TAG="kit-0.12.0").tag, "kit-0.12.0")
        self.assertEqual(self.plan(INPUT_TAG="roc-kit.core-0.12.0").tag, "roc-kit.core-0.12.0")
        self.assertEqual(self.plan(INPUT_VERSION="0.1.0-rc1", INPUT_PRERELEASE="true", INPUT_TAG="kit-0.1.0-rc1").tag, "kit-0.1.0-rc1")

    def test_tags_that_are_unsafe_or_name_another_version_are_rejected(self):
        for tag in (
            "v0.12.0", "kit-0.13.0", "kit-0.12.0-rc1", "kit", "-0.12.0", "kit--0.12.0", "kit/0.12.0", "refs/tags/kit-0.12.0",
            "kit..x-0.12.0", ".kit-0.12.0", "kit.-0.12.0", "kit 0.12.0", "kit-0.12.0 ", "kit-0.12.0\n", "kit~1-0.12.0",
            "kit:x-0.12.0", "kit?-0.12.0", "../kit-0.12.0", "kit@{0}-0.12.0", "0.12.0-0.12.0x",
        ):
            with self.subTest(tag=tag), self.assertRaisesRegex(ValueError, "tag must be 0.12.0 or <name>-0.12.0"):
                self.plan(INPUT_TAG=tag)

    def test_target_is_a_full_commit_sha(self):
        for target in ("", "main", "refs/heads/main", "a" * 39, "a" * 41, "A" * 40, "g" * 40, "a" * 40 + "\n", "HEAD", "a" * 7):
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "target must be a full"):
                self.plan(INPUT_TARGET=target)

    def test_notes_file_is_required_authored_text(self):
        with self.assertRaisesRegex(ValueError, "notes-file is required"):
            self.plan(INPUT_NOTES_FILE="")
        with self.assertRaisesRegex(ValueError, "notes-file does not exist"):
            self.plan(INPUT_NOTES_FILE="candidate/.release/missing.md")
        with self.assertRaisesRegex(ValueError, "notes-file is not a regular file"):
            self.plan(INPUT_NOTES_FILE="candidate/.release")
        (self.root / "blank.md").write_text(" \n")
        with self.assertRaisesRegex(ValueError, "notes-file is empty"):
            self.plan(INPUT_NOTES_FILE="blank.md")
        (self.root / "linked.md").symlink_to(self.root / "candidate/.release/release-notes.md")
        with self.assertRaisesRegex(ValueError, "notes-file is a symbolic link"):
            self.plan(INPUT_NOTES_FILE="linked.md")
        (self.root / "long.md").write_text("x" * (publisher.MAX_NOTES_CHARACTERS + 1))
        with self.assertRaisesRegex(ValueError, "notes-file is longer"):
            self.plan(INPUT_NOTES_FILE="long.md")


class AssetTests(Workspace):
    def test_an_unmatched_glob_is_an_error_even_when_others_match(self):
        with self.assertRaisesRegex(ValueError, r"assets glob matched no file: candidate/dist/\*\.sbom"):
            self.plan(INPUT_ASSETS=GLOBS + "candidate/dist/*.sbom\n")

    def test_no_assets_is_an_error(self):
        for value in ("", "\n\n", "   \n"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "assets names no file"):
                self.plan(INPUT_ASSETS=value)

    def test_two_assets_with_one_basename_are_rejected(self):
        other = self.root / "candidate/other" / BUNDLE
        other.parent.mkdir()
        other.write_bytes(b"a different bundle")
        with self.assertRaisesRegex(ValueError, f"two assets share the name {BUNDLE}"):
            self.plan(INPUT_ASSETS=GLOBS + "candidate/other/*\n")

    def test_one_file_matched_by_two_globs_is_one_asset(self):
        plan = self.plan(INPUT_ASSETS=GLOBS + "candidate/dist/*.zip\n./candidate/dist/../dist/*.zip\n")
        self.assertEqual(sorted(plan.assets), NAMES)

    def test_a_symlink_asset_is_rejected(self):
        (self.root / "candidate/dist/linked.zip").symlink_to(self.root / "candidate/dist/example-examples-0.12.0.zip")
        with self.assertRaisesRegex(ValueError, "asset is a symbolic link: .*linked.zip"):
            self.plan()

    def test_a_dangling_symlink_asset_is_rejected(self):
        (self.root / "candidate/dist/dangling.zip").symlink_to(self.root / "nowhere")
        with self.assertRaisesRegex(ValueError, "asset is a symbolic link"):
            self.plan()

    def test_a_directory_matched_by_a_glob_is_rejected(self):
        (self.root / "candidate/dist/nested").mkdir()
        with self.assertRaisesRegex(ValueError, "asset is not a regular file: .*nested"):
            self.plan()

    def test_an_empty_asset_is_rejected(self):
        (self.root / "candidate/dist/empty.zip").write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "asset is empty"):
            self.plan()

    def test_names_github_would_rewrite_are_rejected(self):
        for name in ("has space.zip", "plus+plus.zip", "tilde~.zip", "-leading.zip", "café.zip"):
            with self.subTest(name=name):
                path = self.root / "candidate/dist" / name
                path.write_bytes(b"x")
                with self.assertRaisesRegex(ValueError, "asset name must use only"):
                    self.plan()
                path.unlink()

    def test_digest_listing_is_sha256sum_format_in_name_order(self):
        listing = publisher.digest_listing(self.plan().assets)
        self.assertEqual(listing, "".join(
            f"{hashlib.sha256(content).hexdigest()}  {name}\n"
            for name, content in sorted((Path(path).name, content) for path, content in FILES.items() if not path.endswith(".md"))
        ))


class PublishTests(Workspace):
    def assert_left_as_draft(self, github, error):
        self.assertNotIn("publish", github.names())
        self.assertTrue(github.releases[7]["draft"])
        self.assertNotIn("0.12.0", github.tags)
        message = str(error.exception)
        self.assertIn("Nothing was published", message)
        self.assertIn("https://github.com/o/r/releases/tag/untagged-1f2e (id 7) is left unpublished", message)
        self.assertIn("the tag 0.12.0 has not been created", message)

    def test_successful_run_stages_verifies_then_publishes_in_order(self):
        github = FakeGitHub()
        published = self.run_publish(github)
        self.assertEqual(github.calls, [
            ("commit_exists", TARGET),
            ("tag_exists", "0.12.0"),
            ("releases_for_tag", "0.12.0"),
            ("create_draft", "0.12.0", TARGET, False),
            *[("upload_asset", name) for name in NAMES],
            ("list_assets", 7),
            *[("download_asset", name) for name in NAMES],
            ("publish", 7, False),
            ("get_release", 7),
        ])
        self.assertEqual(published["html_url"], "https://github.com/o/r/releases/tag/0.12.0")
        self.assertEqual(github.notes, FILES["candidate/.release/release-notes.md"].decode())
        self.assertEqual(sorted(path.name for path in self.downloads.iterdir()), NAMES)

    def test_final_release_is_latest_and_not_a_prerelease(self):
        github = FakeGitHub()
        published = self.run_publish(github)
        self.assertFalse(published["prerelease"])
        self.assertTrue(github.latest)

    def test_release_candidate_is_a_prerelease_and_never_latest(self):
        github = FakeGitHub()
        published = self.run_publish(github, INPUT_VERSION="0.12.0-rc1", INPUT_PRERELEASE="true")
        self.assertIn(("create_draft", "0.12.0-rc1", TARGET, True), github.calls)
        self.assertIn(("publish", 7, True), github.calls)
        self.assertTrue(published["prerelease"])
        self.assertFalse(github.latest)

    def test_prefixed_tag_is_the_one_checked_and_created(self):
        github = FakeGitHub(tags={"0.12.0"})
        published = self.run_publish(github, INPUT_TAG="kit-0.12.0")
        self.assertIn(("tag_exists", "kit-0.12.0"), github.calls)
        self.assertEqual(published["tag_name"], "kit-0.12.0")

    def test_existing_tag_stops_before_anything_is_created(self):
        github = FakeGitHub(tags={"0.12.0"})
        with self.assertRaisesRegex(ValueError, "the tag 0.12.0 already exists"):
            self.run_publish(github)
        self.assertEqual(github.names(), ["commit_exists", "tag_exists"])

    def test_existing_release_stops_before_anything_is_created(self):
        github = FakeGitHub(releases=[{"id": 3, "tag_name": "0.12.0", "draft": False, "html_url": "https://github.com/o/r/releases/tag/0.12.0"}])
        with self.assertRaisesRegex(ValueError, "a release for 0.12.0 already exists: https://github.com/o/r/releases/tag/0.12.0"):
            self.run_publish(github)
        self.assertNotIn("create_draft", github.names())

    def test_existing_draft_stops_before_anything_is_created(self):
        github = FakeGitHub(releases=[{"id": 3, "tag_name": "0.12.0", "draft": True, "html_url": "https://github.com/o/r/releases/tag/untagged-9"}])
        with self.assertRaisesRegex(ValueError, "a draft release for 0.12.0 already exists: .*untagged-9"):
            self.run_publish(github)
        self.assertNotIn("create_draft", github.names())
        self.assertNotIn("upload_asset", github.names())

    def test_target_outside_the_repository_stops_before_anything_is_created(self):
        github = FakeGitHub()
        with self.assertRaisesRegex(ValueError, "is not a commit in this repository"):
            self.run_publish(github, INPUT_TARGET="b" * 40)
        self.assertEqual(github.names(), ["commit_exists"])

    def test_digest_mismatch_leaves_the_draft_unpublished(self):
        github = FakeGitHub()
        github.corrupt = BUNDLE
        with self.assertRaisesRegex(ValueError, f"{BUNDLE} downloaded from the draft differs from the tested file") as error:
            self.run_publish(github)
        self.assert_left_as_draft(github, error)

    def test_truncated_and_padded_downloads_are_mismatches(self):
        for change in (lambda content: content[:-1], lambda content: content + b"\0"):
            with self.subTest(change=change):
                github = FakeGitHub()
                original = github.upload_asset

                def upload(release, name, path, size):
                    result = original(release, name, path, size)
                    if name == BUNDLE:
                        github.stored[name] = change(github.stored[name])
                    return result

                github.upload_asset = upload
                with self.assertRaisesRegex(ValueError, "differs in size from the tested file") as error:
                    self.run_publish(github)
                self.assert_left_as_draft(github, error)
                for path in self.downloads.iterdir():
                    path.unlink()

    def test_a_download_longer_than_its_listed_size_is_a_mismatch(self):
        github = FakeGitHub()
        original = github.list_assets

        def listing(release_id):
            records = original(release_id)
            github.stored[BUNDLE] += b"appended after the listing"
            return records

        github.list_assets = listing
        with self.assertRaisesRegex(ValueError, "downloaded from the draft differs") as error:
            self.run_publish(github)
        self.assert_left_as_draft(github, error)
        self.assertEqual((self.downloads / BUNDLE).stat().st_size, len(b"bundle bytes") + 1)

    def test_asset_missing_from_the_draft_leaves_it_unpublished(self):
        github = FakeGitHub()
        github.drop = "example-0.12.0.pdf"
        with self.assertRaisesRegex(ValueError, "the draft is missing example-0.12.0.pdf") as error:
            self.run_publish(github)
        self.assert_left_as_draft(github, error)
        self.assertNotIn("download_asset", github.names())

    def test_extra_asset_in_the_draft_leaves_it_unpublished(self):
        github = FakeGitHub()
        github.extra = "injected.zip"
        with self.assertRaisesRegex(ValueError, "the draft holds injected.zip, which is not a tested file") as error:
            self.run_publish(github)
        self.assert_left_as_draft(github, error)

    def test_incomplete_or_repeated_inventory_is_rejected(self):
        assets = self.plan().assets
        records = [{"id": index, "name": name, "size": assets[name].size, "state": "uploaded"} for index, name in enumerate(NAMES)]
        publisher.check_inventory(assets, records)
        with self.assertRaisesRegex(ValueError, "more than once"):
            publisher.check_inventory(assets, records + [records[0]])
        with self.assertRaisesRegex(ValueError, "did not complete"):
            publisher.check_inventory(assets, [{**records[0], "state": "starter"}, *records[1:]])

    def test_failed_upload_leaves_the_draft_unpublished(self):
        github = FakeGitHub()
        github.fail["upload_asset"] = "GitHub answered 502 to POST https://uploads.github.com/x"
        with self.assertRaisesRegex(ValueError, "GitHub answered 502") as error:
            self.run_publish(github)
        self.assert_left_as_draft(github, error)

    def test_a_renamed_upload_leaves_the_draft_unpublished(self):
        github = FakeGitHub()
        github.upload_asset = lambda release, name, path, size: {"name": "renamed"}
        with self.assertRaisesRegex(ValueError, "GitHub stored .* as renamed") as error:
            self.run_publish(github)
        self.assert_left_as_draft(github, error)

    def test_rejected_publish_reports_the_verified_draft(self):
        github = FakeGitHub()
        github.fail["publish"] = "GitHub answered 422 to PATCH"
        with self.assertRaisesRegex(ValueError, "GitHub answered 422.*Nothing was published.*is verified but left unpublished"):
            self.run_publish(github)
        self.assertTrue(github.releases[7]["draft"])

    def test_publish_whose_answer_was_lost_is_recognised_as_published(self):
        github = FakeGitHub()
        real = github.publish

        def publish(release_id, prerelease):
            real(release_id, prerelease)
            raise ValueError("PATCH failed: timed out")

        github.publish = publish
        self.assertFalse(self.run_publish(github)["draft"])

    def test_unreadable_state_after_publish_is_not_reported_as_unpublished(self):
        github = FakeGitHub()
        github.fail["get_release"] = "GET failed: timed out"
        with self.assertRaisesRegex(ValueError, "may or may not be published") as error:
            self.run_publish(github)
        self.assertNotIn("Nothing was published", str(error.exception))

    def test_release_that_is_not_immutable_is_reported_after_publication(self):
        github = FakeGitHub(immutable=False)
        with self.assertRaisesRegex(ValueError, "is published with the verified assets, but it is not immutable") as error:
            self.run_publish(github)
        self.assertNotIn("Nothing was published", str(error.exception))

    def test_main_writes_outputs_summary_and_digest_listing(self):
        github = FakeGitHub()
        temp = self.root.parent / "runner-temp"
        temp.mkdir()
        env = inputs(
            INPUT_TOKEN="secret-token", GITHUB_REPOSITORY="o/r", RUNNER_TEMP=str(temp),
            GITHUB_OUTPUT=str(temp / "output"), GITHUB_STEP_SUMMARY=str(temp / "summary"),
        )
        seen = []

        def client(api_url, repository, token):
            seen.append((api_url, repository, token))
            return github

        with patch.dict(os.environ, env, clear=True), patch.object(publisher, "GitHub", client), patch.object(Path, "cwd", return_value=self.root):
            with patch("sys.stdout", io.StringIO()) as stdout:
                publisher.main()
        self.assertEqual(seen, [("https://api.github.com", "o/r", "secret-token")])
        outputs = dict(line.split("=", 1) for line in (temp / "output").read_text().splitlines())
        self.assertEqual(outputs["release-url"], "https://github.com/o/r/releases/tag/0.12.0")
        listing = Path(outputs["digests"]).read_text()
        self.assertEqual(listing, publisher.digest_listing(self.plan().assets))
        self.assertEqual(len(listing.splitlines()), 3)
        summary = (temp / "summary").read_text()
        self.assertIn("### Release 0.12.0", summary)
        self.assertIn(listing, summary)
        self.assertNotIn("secret-token", summary + stdout.getvalue() + (temp / "output").read_text())

    def test_main_makes_no_request_when_an_input_is_invalid(self):
        made = []
        env = inputs(INPUT_TOKEN="t", GITHUB_REPOSITORY="o/r", RUNNER_TEMP=str(self.root), INPUT_ASSETS="candidate/none/*")
        with patch.dict(os.environ, env, clear=True), patch.object(publisher, "GitHub", lambda *args: made.append(args)), patch.object(Path, "cwd", return_value=self.root):
            with self.assertRaisesRegex(ValueError, "matched no file"):
                publisher.main()
        self.assertEqual(made, [])


def answer(body, status=200):
    response = io.BytesIO(body if isinstance(body, bytes) else json.dumps(body).encode())
    response.status = status
    return response


def http_error(url, code, headers=None, body=b'{"message": "Validation Failed"}'):
    message = Message()
    for name, value in (headers or {}).items():
        message[name] = value
    return urllib.error.HTTPError(url, code, "error", message, io.BytesIO(body))


class ClientTests(unittest.TestCase):
    """The real client against a fake `send`: which requests it makes, and where the token goes."""

    def client(self, answers):
        self.requests = []

        def send(request, timeout):
            self.requests.append(request)
            result = answers.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        return publisher.GitHub("https://api.github.com", "o/r", "secret-token", send)

    def test_client_refuses_plain_http_a_malformed_repository_or_no_token(self):
        for arguments in (("http://api.github.com", "o/r", "t"), ("https://api.github.com", "o/r/x", "t"), ("https://api.github.com", "../r", "t"), ("https://api.github.com", "o/r", "")):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                publisher.GitHub(*arguments)

    def test_tag_lookup_is_exact_and_distinguishes_absent_from_failed(self):
        url = "https://api.github.com/repos/o/r/git/ref/tags/kit-0.1.0"
        self.assertTrue(self.client([answer({"ref": "refs/tags/kit-0.1.0"})]).tag_exists("kit-0.1.0"))
        self.assertEqual(self.requests[0].full_url, url)
        self.assertEqual(self.requests[0].get_header("Authorization"), "Bearer secret-token")
        self.assertFalse(self.client([http_error(url, 404)]).tag_exists("kit-0.1.0"))
        with self.assertRaisesRegex(ValueError, "GitHub answered 403 .*: Validation Failed"):
            self.client([http_error(url, 403)]).tag_exists("kit-0.1.0")

    def test_drafts_are_found_on_any_page_of_the_release_list(self):
        first = [{"tag_name": f"0.{index + 100}.0", "draft": False} for index in range(100)]
        second = [{"tag_name": "0.12.0", "draft": True}, {"tag_name": "0.12.0-rc1", "draft": False}]
        found = self.client([answer(first), answer(second)]).releases_for_tag("0.12.0")
        self.assertEqual(found, [{"tag_name": "0.12.0", "draft": True}])
        self.assertEqual([request.full_url for request in self.requests], [
            "https://api.github.com/repos/o/r/releases?per_page=100&page=1",
            "https://api.github.com/repos/o/r/releases?per_page=100&page=2",
        ])

    def test_draft_is_created_at_the_target_with_authored_notes_only(self):
        self.client([answer({"id": 7})]).create_draft("0.12.0-rc1", TARGET, "notes", True)
        request = self.requests[0]
        self.assertEqual((request.get_method(), request.full_url), ("POST", "https://api.github.com/repos/o/r/releases"))
        self.assertEqual(json.loads(request.data), {
            "tag_name": "0.12.0-rc1", "target_commitish": TARGET, "name": "0.12.0-rc1", "body": "notes",
            "draft": True, "prerelease": True, "make_latest": "false",
        })

    def test_publish_sets_prerelease_and_latest_explicitly(self):
        client = self.client([answer({}), answer({})])
        client.publish(7, True)
        client.publish(7, False)
        self.assertEqual([request.get_method() for request in self.requests], ["PATCH", "PATCH"])
        self.assertEqual(json.loads(self.requests[0].data), {"draft": False, "prerelease": True, "make_latest": "false"})
        self.assertEqual(json.loads(self.requests[1].data), {"draft": False, "prerelease": False, "make_latest": "true"})

    def test_upload_goes_to_the_drafts_upload_url_with_an_encoded_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "a.zip"
            path.write_bytes(b"12345")
            release = {"upload_url": "https://uploads.github.com/repos/o/r/releases/7/assets{?name,label}"}
            self.client([answer({"name": "a.zip"})]).upload_asset(release, "a.zip", path, 5)
            request = self.requests[0]
            self.assertEqual(request.full_url, "https://uploads.github.com/repos/o/r/releases/7/assets?name=a.zip")
            self.assertEqual(request.get_header("Content-length"), "5")
            self.assertEqual(request.get_header("Content-type"), "application/octet-stream")
            with self.assertRaisesRegex(ValueError, "no https upload URL"):
                self.client([]).upload_asset({"upload_url": "http://uploads.example/x{?name}"}, "a.zip", path, 5)
            self.assertEqual(self.requests, [])

    def test_download_follows_one_redirect_without_the_token(self):
        api = "https://api.github.com/repos/o/r/releases/assets/101"
        storage = "https://release-assets.githubusercontent.com/signed?sig=1"
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "a.zip"
            self.client([http_error(api, 302, {"Location": storage}), answer(b"asset bytes")]).download_asset(101, destination, 11)
            self.assertEqual(destination.read_bytes(), b"asset bytes")
        first, second = self.requests
        self.assertEqual((first.full_url, first.get_header("Accept")), (api, "application/octet-stream"))
        self.assertEqual(first.get_header("Authorization"), "Bearer secret-token")
        self.assertEqual(second.full_url, storage)
        self.assertIsNone(second.get_header("Authorization"))
        self.assertNotIn("secret-token", repr(second.header_items()))

    def test_download_refuses_a_redirect_that_is_not_https_and_never_overwrites(self):
        api = "https://api.github.com/repos/o/r/releases/assets/101"
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "a.zip"
            with self.assertRaisesRegex(ValueError, "GitHub answered 302 to the download of asset 101"):
                self.client([http_error(api, 302, {"Location": "http://storage.example/a"})]).download_asset(101, destination, 5)
            self.assertEqual(len(self.requests), 1)
            destination.write_bytes(b"earlier")
            with self.assertRaises(FileExistsError):
                self.client([answer(b"other")]).download_asset(101, destination, 5)

    def test_download_stops_one_byte_past_the_expected_size(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "a.zip"
            self.client([answer(b"x" * 5000)]).download_asset(101, destination, 10)
            self.assertEqual(destination.stat().st_size, 11)

    def test_default_transport_does_not_follow_redirects(self):
        handler = publisher._NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {}, "https://elsewhere.example/"))


if __name__ == "__main__":
    unittest.main()
