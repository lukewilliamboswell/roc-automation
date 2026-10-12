import importlib.util
import io
import json
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("nightly_report", Path(__file__).resolve().parents[1] / "actions/nightly-report/nightly_report.py")
nightly = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nightly)

REPO = "octo/package"
TITLE = nightly.DEFAULT_TITLE
NIGHTLY = "nightly-2026-10-12-0a1b2c3"
RELEASE = "0.4.1"
RUN = "https://github.com/octo/package/actions/runs/987654321"
ACTIONS_BOT = {"login": "github-actions[bot]", "type": "Bot"}
HUMAN = {"login": "mallory", "type": "User"}


def issue(number, title=TITLE, user=ACTIONS_BOT, state="open", pull_request=False):
    item = {"number": number, "title": title, "user": user, "state": state}
    if pull_request:
        item["pull_request"] = {"url": f"https://api.github.com/repos/{REPO}/pulls/{number}"}
    return item


class FakeGitHub:
    """The issues of one repository, behind the three REST calls the action makes."""

    def __init__(self, issues=()):
        self.issues = {item["number"]: dict(item) for item in issues}
        self.comments = []
        self.calls = []

    def __call__(self, method, path, body):
        self.calls.append((method, path, body))
        parsed = urllib.parse.urlsplit(path)
        parts = parsed.path.split("/")
        assert parts[:4] == ["", "repos", *REPO.split("/")] and parts[4] == "issues", path
        rest = parts[5:]
        if method == "GET" and not rest:
            query = dict(urllib.parse.parse_qsl(parsed.query))
            assert query["state"] == "open", "the action must ask only for open issues"
            size, page = int(query["per_page"]), int(query["page"])
            matching = [item for _, item in sorted(self.issues.items()) if item["state"] == "open"]
            return [dict(item) for item in matching[(page - 1) * size : page * size]]
        if method == "POST" and not rest:
            number = max(self.issues, default=0) + 1
            self.issues[number] = {"number": number, "title": body["title"], "body": body["body"], "user": ACTIONS_BOT, "state": "open"}
            return dict(self.issues[number])
        if method == "POST" and len(rest) == 2 and rest[1] == "comments":
            assert self.issues[int(rest[0])]["state"] == "open", "comment on a closed issue"
            self.comments.append((int(rest[0]), body["body"]))
            return {"id": len(self.comments)}
        if method == "PATCH" and len(rest) == 1:
            self.issues[int(rest[0])].update(body)
            return dict(self.issues[int(rest[0])])
        raise AssertionError(f"unexpected call {method} {path}")

    @property
    def writes(self):
        return [(method, path.split("?")[0]) for method, path, _ in self.calls if method != "GET"]

    def snapshot(self):
        return json.dumps(self.issues, sort_keys=True), list(self.comments)


def run(github, outcome, title=TITLE, nightly_tag=NIGHTLY, release_tag=RELEASE):
    return nightly.report(github, REPO, outcome, nightly_tag, release_tag, title, RUN)


class VerdictTests(unittest.TestCase):
    def test_pass_with_no_issue_does_nothing(self):
        github = FakeGitHub([issue(1, title="Unrelated bug", user=HUMAN)])
        self.assertEqual(run(github, "success"), ("none", None))
        self.assertEqual(github.writes, [])

    def test_pass_comments_on_and_closes_the_open_issue(self):
        github = FakeGitHub([issue(7)])
        self.assertEqual(run(github, "success"), ("closed", 7))
        self.assertEqual(github.issues[7]["state"], "closed")
        self.assertEqual(github.issues[7]["state_reason"], "completed")
        self.assertEqual(github.writes, [("POST", f"/repos/{REPO}/issues/7/comments"), ("PATCH", f"/repos/{REPO}/issues/7")])
        (number, text), = github.comments
        self.assertEqual(number, 7)
        for expected in (f"`{NIGHTLY}`", f"`{RELEASE}`", RUN, "passes"):
            self.assertIn(expected, text)

    def test_first_failure_opens_the_issue(self):
        github = FakeGitHub([issue(1, title="Unrelated bug", user=HUMAN)])
        self.assertEqual(run(github, "failure"), ("opened", 2))
        created = github.issues[2]
        self.assertEqual(created["title"], TITLE)
        for expected in (f"`{NIGHTLY}`", f"`{RELEASE}`", RUN):
            self.assertIn(expected, created["body"])
        self.assertEqual(github.writes, [("POST", f"/repos/{REPO}/issues")])
        self.assertEqual(github.comments, [])

    def test_repeat_failure_comments_instead_of_opening_another(self):
        github = FakeGitHub([issue(7)])
        self.assertEqual(run(github, "failure", nightly_tag="nightly-2026-10-13-fff"), ("commented", 7))
        self.assertEqual(sorted(github.issues), [7])
        self.assertEqual(github.issues[7]["state"], "open")
        (number, text), = github.comments
        self.assertEqual(number, 7)
        for expected in ("`nightly-2026-10-13-fff`", f"`{RELEASE}`", RUN, "Still failing"):
            self.assertIn(expected, text)

    def test_one_issue_per_incident_across_a_sequence_of_runs(self):
        github = FakeGitHub()
        actions = [run(github, outcome) for outcome in ("success", "failure", "failure", "success", "success", "failure")]
        self.assertEqual(actions, [("none", None), ("opened", 1), ("commented", 1), ("closed", 1), ("none", None), ("opened", 2)])
        self.assertEqual({number: item["state"] for number, item in github.issues.items()}, {1: "closed", 2: "open"})
        self.assertEqual([number for number, _ in github.comments], [1, 1])

    def test_custom_title_selects_a_different_issue(self):
        github = FakeGitHub([issue(3), issue(4, title="Nightly: macOS examples fail")])
        self.assertEqual(run(github, "success", title="Nightly: macOS examples fail"), ("closed", 4))
        self.assertEqual(github.issues[3]["state"], "open")


class SetupFailureTests(unittest.TestCase):
    OUTCOMES = ("", "skipped", "cancelled", "neutral", "timed_out", "Success", "FAILURE", "success ", " failure", "failure\n", "true", "0", "success\nfailure")

    def test_any_other_outcome_is_an_error_and_reaches_no_api(self):
        for outcome in self.OUTCOMES:
            for existing in ([], [issue(7)]):
                github = FakeGitHub(existing)
                before = github.snapshot()
                with self.subTest(outcome=outcome, existing=bool(existing)), self.assertRaisesRegex(ValueError, "setup failure"):
                    run(github, outcome)
                self.assertEqual(github.calls, [])
                self.assertEqual(github.snapshot(), before)

    def test_main_fails_before_connecting_and_writes_no_output(self):
        def connect(api_url, token):
            raise AssertionError("a setup failure must not reach GitHub")

        for outcome in self.OUTCOMES:
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "output"
                # Tags are typically empty too when setup failed; the outcome is still what is reported.
                env = main_env(output, INPUT_OUTCOME=outcome, INPUT_NIGHTLY_TAG="", INPUT_RELEASE_TAG="")
                with self.assertRaisesRegex(ValueError, "setup failure"):
                    nightly.main(env, connect)
                self.assertFalse(output.exists())

    def test_missing_outcome_is_a_setup_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            env = main_env(Path(temporary) / "output")
            del env["INPUT_OUTCOME"]
            with self.assertRaisesRegex(ValueError, "setup failure"):
                nightly.main(env, lambda api_url, token: self.fail("connected"))

    def test_error_text_cannot_carry_a_long_or_multiline_outcome(self):
        with self.assertRaises(ValueError) as caught:
            nightly.classify("x" * 500 + "\n::notice::forged")
        self.assertNotIn("\n", str(caught.exception))
        self.assertNotIn("forged", str(caught.exception))


class OwnershipTests(unittest.TestCase):
    def test_same_titled_issue_opened_by_a_person_is_never_touched(self):
        for outcome, expected in (("success", ("none", None)), ("failure", ("opened", 6))):
            github = FakeGitHub([issue(5, user=HUMAN)])
            with self.subTest(outcome=outcome):
                self.assertEqual(run(github, outcome), expected)
                self.assertEqual(github.issues[5], issue(5, user=HUMAN))
                self.assertEqual(github.comments, [])

    def test_bot_issue_is_used_while_a_persons_twin_is_left_alone(self):
        github = FakeGitHub([issue(5, user=HUMAN), issue(6)])
        self.assertEqual(run(github, "failure"), ("commented", 6))
        self.assertEqual(run(github, "success"), ("closed", 6))
        self.assertEqual(github.issues[5], issue(5, user=HUMAN))
        self.assertEqual({number for number, _ in github.comments}, {6})

    def test_author_must_be_a_bot_account_by_type_not_by_name(self):
        impostors = (
            {"login": "github-actions", "type": "User"},
            {"login": "github-actions[bot]", "type": "User"},
            {"login": "octo", "type": "Organization"},
            {"login": "ghost", "type": "bot"},
            {"login": "ghost"},
            None,
            "github-actions[bot]",
        )
        for user in impostors:
            with self.subTest(user=user):
                self.assertFalse(nightly.is_tracking_issue(issue(5, user=user), TITLE))
        self.assertTrue(nightly.is_tracking_issue(issue(5, user=ACTIONS_BOT), TITLE))
        self.assertTrue(nightly.is_tracking_issue(issue(5, user={"login": "release-helper[bot]", "type": "Bot"}), TITLE))

    def test_closed_bot_issue_is_not_reopened(self):
        github = FakeGitHub([issue(3, state="closed")])
        self.assertEqual(run(github, "success"), ("none", None))
        self.assertEqual(run(github, "failure"), ("opened", 4))
        self.assertEqual(github.issues[3], issue(3, state="closed"))
        self.assertEqual(github.comments, [])

    def test_closed_issue_is_rejected_even_if_the_listing_returns_it(self):
        self.assertFalse(nightly.is_tracking_issue(issue(3, state="closed"), TITLE))

    def test_pull_request_with_the_title_is_not_an_issue(self):
        github = FakeGitHub([issue(8, pull_request=True)])
        self.assertEqual(run(github, "success"), ("none", None))
        self.assertEqual(run(github, "failure"), ("opened", 9))
        self.assertEqual(github.issues[8]["state"], "open")
        self.assertEqual(github.comments, [])

    def test_title_must_match_exactly(self):
        near = (TITLE.lower(), TITLE + " ", " " + TITLE, TITLE + " (macOS)", TITLE[4:], TITLE.replace("Roc", "roc"), "")
        github = FakeGitHub([issue(number, title=title) for number, title in enumerate(near, start=1)])
        before = github.snapshot()
        self.assertEqual(run(github, "success"), ("none", None))
        self.assertEqual(github.snapshot(), before)
        self.assertEqual(run(github, "failure"), ("opened", len(near) + 1))

    def test_oldest_open_tracking_issue_is_the_one_used(self):
        github = FakeGitHub([issue(12), issue(9), issue(30)])
        self.assertEqual(run(github, "failure"), ("commented", 9))

    def test_malformed_issue_records_are_skipped(self):
        records = [None, "issue", {"title": TITLE, "user": ACTIONS_BOT, "state": "open"}, issue("7"), issue(True)]
        for record in records:
            with self.subTest(record=record):
                self.assertFalse(nightly.is_tracking_issue(record, TITLE))


class ListingTests(unittest.TestCase):
    def filler(self, count):
        return [issue(number, title=f"Bug {number}", user=HUMAN) for number in range(1, count + 1)]

    def test_tracking_issue_beyond_the_first_page_is_found(self):
        github = FakeGitHub(self.filler(250) + [issue(251)])
        self.assertEqual(run(github, "failure"), ("commented", 251))
        pages = [urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["page"] for method, path, _ in github.calls if method == "GET"]
        self.assertEqual(pages, [["1"], ["2"], ["3"]])

    def test_a_full_last_page_is_followed_by_one_more_request(self):
        github = FakeGitHub(self.filler(100))
        self.assertEqual(run(github, "success"), ("none", None))
        self.assertEqual(len(github.calls), 2)

    def test_too_many_open_issues_is_an_error_not_a_duplicate(self):
        github = FakeGitHub(self.filler(200) + [issue(201)])
        with patch.object(nightly, "MAX_PAGES", 2), self.assertRaisesRegex(ValueError, "more than 200 open issues"):
            run(github, "failure")
        self.assertEqual(github.writes, [])

    def test_unexpected_listing_response_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "other than a list"):
            run(lambda method, path, body: {"message": "Not Found"}, "failure")

    def test_create_response_without_a_number_is_an_error(self):
        def api(method, path, body):
            return [] if method == "GET" else {"message": "created?"}

        with self.assertRaisesRegex(ValueError, "did not return the number"):
            run(api, "failure")


class InputTests(unittest.TestCase):
    BAD_TAGS = ("", " ", "0.4.1 ", "a b", "0.4.1\n0.4.0", "`0.4.1`", "@octocat", "#12", "[x](https://evil.example)", "<b>", "-rf", ".hidden", "a" * 101, "0.4.1\x00", "0.4.1‮")
    BAD_TITLES = ("", " ", " leading", "trailing ", "two\nlines", "tab\there", "nul\x00", "bell\x07", "bidi‮override", "zero​width", "x" * 257)

    def test_ordinary_tags_are_accepted(self):
        for tag in ("0.4.1", "v1.2.3", "1.0.0-rc.1", "1.0.0+build.5", "nightly-2026-10-12-0a1b2c3", "release/2026.10", "a" * 100):
            with self.subTest(tag=tag):
                self.assertEqual(nightly.checked_tag("release-tag", tag), tag)

    def test_malformed_tags_are_rejected_before_any_request(self):
        for name, arguments in (("nightly-tag", "nightly_tag"), ("release-tag", "release_tag")):
            for tag in self.BAD_TAGS:
                github = FakeGitHub([issue(7)])
                with self.subTest(name=name, tag=tag), self.assertRaisesRegex(ValueError, f"^{name} must be a tag"):
                    run(github, "failure", **{arguments: tag})
                self.assertEqual(github.calls, [])

    def test_titles_within_the_limits_are_accepted(self):
        for title in (TITLE, "x" * 256, "Nightly: la dernière version échoue", "Nightly (macOS) #2"):
            with self.subTest(title=title):
                self.assertEqual(nightly.checked_title(title), title)

    def test_malformed_titles_are_rejected_before_any_request(self):
        for title in self.BAD_TITLES:
            github = FakeGitHub([issue(7)])
            with self.subTest(title=title), self.assertRaisesRegex(ValueError, "^title must"):
                run(github, "failure", title=title)
            self.assertEqual(github.calls, [])

    def test_run_link_is_built_from_the_runner_environment(self):
        self.assertEqual(nightly.run_url("https://github.com", REPO, "987654321"), RUN)
        self.assertEqual(nightly.run_url("https://ghe.example.com/", "o/r.s_t", "1"), "https://ghe.example.com/o/r.s_t/actions/runs/1")

    def test_run_link_rejects_a_malformed_environment(self):
        cases = (
            ("http://github.com", REPO, "1", "GITHUB_SERVER_URL"),
            ("", REPO, "1", "GITHUB_SERVER_URL"),
            ("https://github.com/?x=[a](b)", REPO, "1", "GITHUB_SERVER_URL"),
            ("https://github.com", "octo", "1", "GITHUB_REPOSITORY"),
            ("https://github.com", "octo/package/extra", "1", "GITHUB_REPOSITORY"),
            ("https://github.com", "octo/pack age", "1", "GITHUB_REPOSITORY"),
            ("https://github.com", REPO, "", "GITHUB_RUN_ID"),
            ("https://github.com", REPO, "12/../3", "GITHUB_RUN_ID"),
            ("https://github.com", REPO, "012", "GITHUB_RUN_ID"),
        )
        for server, repository, run_id, named in cases:
            with self.subTest(server=server, repository=repository, run_id=run_id), self.assertRaisesRegex(ValueError, named):
                nightly.run_url(server, repository, run_id)

    def test_workflow_command_text_cannot_start_a_second_command(self):
        self.assertEqual(nightly.workflow_command_text("50% done\r\n::notice::x"), "50%25 done%0D%0A::notice::x")


def main_env(output, **overrides):
    env = {
        "INPUT_OUTCOME": "failure",
        "INPUT_NIGHTLY_TAG": NIGHTLY,
        "INPUT_RELEASE_TAG": RELEASE,
        "INPUT_TITLE": TITLE,
        "INPUT_TOKEN": "ghs_secret",
        "GITHUB_API_URL": "https://api.github.com",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_RUN_ID": "987654321",
        "GITHUB_OUTPUT": str(output),
    }
    env.update(overrides)
    return env


class MainTests(unittest.TestCase):
    def main(self, github, **overrides):
        connections = []

        def connect(api_url, token):
            connections.append((api_url, token))
            return github

        with tempfile.TemporaryDirectory() as temporary, patch("sys.stdout", new_callable=io.StringIO):
            output = Path(temporary) / "output"
            nightly.main(main_env(output, **overrides), connect)
            self.assertEqual(connections, [("https://api.github.com", "ghs_secret")])
            return output.read_text()

    def test_failure_exports_the_opened_issue_and_links_this_run(self):
        github = FakeGitHub()
        self.assertEqual(self.main(github), "action=opened\nissue-number=1\n")
        self.assertIn(f"Workflow run: {RUN}\n", github.issues[1]["body"])

    def test_each_action_is_exported(self):
        self.assertEqual(self.main(FakeGitHub([issue(7)])), "action=commented\nissue-number=7\n")
        self.assertEqual(self.main(FakeGitHub([issue(7)]), INPUT_OUTCOME="success"), "action=closed\nissue-number=7\n")
        self.assertEqual(self.main(FakeGitHub(), INPUT_OUTCOME="success"), "action=none\nissue-number=\n")

    def test_malformed_inputs_fail_before_connecting(self):
        cases = (
            ({"INPUT_NIGHTLY_TAG": "nightly latest"}, "nightly-tag"),
            ({"INPUT_RELEASE_TAG": ""}, "release-tag"),
            ({"INPUT_TITLE": "two\nlines"}, "title"),
            ({"GITHUB_REPOSITORY": "octo"}, "GITHUB_REPOSITORY"),
            ({"GITHUB_RUN_ID": "abc"}, "GITHUB_RUN_ID"),
        )
        for overrides, named in cases:
            with self.subTest(overrides=overrides), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "output"
                with self.assertRaisesRegex(ValueError, named):
                    nightly.main(main_env(output, **overrides), lambda api_url, token: self.fail("connected"))
                self.assertFalse(output.exists())


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return io.BytesIO(json.dumps(self.payload).encode())

    def __exit__(self, *exc):
        return False


class TransportTests(unittest.TestCase):
    def test_request_carries_the_token_only_to_the_api_root(self):
        sent = []

        def send(request, timeout):
            sent.append(request)
            return Response({"number": 5})

        api = nightly.github_api("https://api.github.com/", "ghs_secret", send)
        self.assertEqual(api("POST", f"/repos/{REPO}/issues", {"title": TITLE, "body": "text"}), {"number": 5})
        request, = sent
        self.assertEqual(request.full_url, f"https://api.github.com/repos/{REPO}/issues")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {"title": TITLE, "body": "text"})
        self.assertEqual(request.get_header("Authorization"), "Bearer ghs_secret")
        self.assertEqual(request.get_header("Accept"), "application/vnd.github+json")

    def test_get_sends_no_body(self):
        sent = []
        api = nightly.github_api("https://api.github.com", "ghs_secret", lambda request, timeout: sent.append(request) or Response([]))
        self.assertEqual(api("GET", f"/repos/{REPO}/issues?state=open", None), [])
        self.assertIsNone(sent[0].data)
        self.assertEqual(sent[0].get_method(), "GET")

    def test_http_error_names_the_call_and_status_without_the_token_or_query(self):
        def send(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(b'{"message":"Resource not accessible by integration"}'))

        api = nightly.github_api("https://api.github.com", "ghs_secret", send)
        with self.assertRaises(ValueError) as caught:
            api("POST", f"/repos/{REPO}/issues/7/comments", {"body": "x"})
        self.assertEqual(str(caught.exception), f"GitHub answered POST /repos/{REPO}/issues/7/comments with HTTP 403")

    def test_network_and_decoding_failures_are_reported_as_errors(self):
        def unreachable(request, timeout):
            raise urllib.error.URLError("no route")

        class NotJson(Response):
            def __enter__(self):
                return io.BytesIO(b"<html>")

        for send in (unreachable, lambda request, timeout: NotJson(None)):
            with self.subTest(send=send), self.assertRaisesRegex(ValueError, "GET /repos/octo/package/issues failed"):
                nightly.github_api("https://api.github.com", "ghs_secret", send)("GET", f"/repos/{REPO}/issues?page=1", None)

    def test_api_root_and_token_are_validated(self):
        for api_url in ("", "http://api.github.com", "https://api.github.com/?next=https://evil.example", "file:///etc/passwd"):
            with self.subTest(api_url=api_url), self.assertRaisesRegex(ValueError, "GITHUB_API_URL"):
                nightly.github_api(api_url, "ghs_secret")
        for token in ("", "two words", "line\nbreak"):
            with self.subTest(token=token), self.assertRaisesRegex(ValueError, "token must not"):
                nightly.github_api("https://api.github.com", token)

    def test_redirects_are_not_followed(self):
        request = urllib.request.Request("https://api.github.com/repos/octo/package/issues")
        self.assertIsNone(nightly._NoRedirect().redirect_request(request, None, 301, "Moved", {}, "https://evil.example/"))


if __name__ == "__main__":
    unittest.main()
