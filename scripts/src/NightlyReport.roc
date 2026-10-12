import GitHub

## Keep the one tracking issue of a nightly compatibility workflow.
##
## Nothing here has an effect. `plan` checks every input, and `start` and
## `advance` then decide each request from the answers so far, so the order of
## the requests and what a failure leaves untouched are covered by the `expect`
## tests below. The script only carries requests to `gh` and answers back.
NightlyReport := [].{

	## The inputs of the action and the runner's environment, as text. A
	## variable that is not set is the empty string.
	Inputs : {
		outcome : Str,
		nightly_tag : Str,
		release_tag : Str,
		title : Str,
		server : Str,
		repository : Str,
		run_id : Str,
		token : Str,
	}

	## Everything decided before any request is made. It does not hold the
	## token, so nothing built from a plan can print or send it.
	Plan : {
		verdict : [Success, Failure],
		outcome : Str,
		nightly : Str,
		release : Str,
		title : Str,
		repository : Str,
		url : Str,
		per_page : U64,
		max_pages : U64,
	}

	## One open issue, cut down to what decides whether it is the tracking
	## issue. `author` is the type GitHub assigns to the account that opened
	## it, and `pull` is whether the record is a pull request.
	Issue : { number : U64, title : Str, state : Str, author : Str, pull : Bool }

	## The four requests the action can make. Only `ListOpen` is a GET.
	Request : [ListOpen(U64), Comment(U64, Str), Close(U64), Open(Str, Str)]

	## GitHub's answer to one request.
	Reply : [Issues(List(Issue)), Accepted, Opened(U64), Failed(Str)]

	## What the action is waiting for.
	Stage : [Listing({ page : U64, found : List(U64) }), Commenting(U64), Closing(U64), Opening]

	## What was done, and the issue it concerned.
	Report : { action : Str, issue : [Absent, Number(U64)] }

	## The next request to make, or the end of the run.
	Next : [Do(Request, Stage), Finished(Try(Report, Str))]

	## The title of the tracking issue when the caller names none.
	default_title : Str
	default_title = "The latest release fails on the latest Roc nightly"

	## Check every input. The verdict is settled first, so a setup failure is
	## reported as one whatever else is wrong, and it reaches no request: only
	## a plan can start one.
	plan : Inputs -> Try(Plan, Str)
	plan = |inputs| {
		verdict = classify(inputs.outcome)?
		nightly = checked_tag("nightly-tag", inputs.nightly_tag)?
		release = checked_tag("release-tag", inputs.release_tag)?
		title = checked_title(inputs.title)?
		url = run_url(inputs.server, inputs.repository, inputs.run_id)?
		if GitHub.valid_token(inputs.token) {
			Ok({ verdict, outcome: inputs.outcome, nightly, release, title, repository: inputs.repository, url, per_page: 100, max_pages: 100 })
		} else {
			Err("token must not be empty or contain whitespace")
		}
	}

	## The first request: the oldest page of open issues.
	start : Plan -> Next
	start = |_plan| list(1, [])

	## Decide what follows `reply`, the answer to the request made at `stage`.
	advance : Plan, Stage, Reply -> Next
	advance = |made, stage, reply|
		match (stage, reply) {
			(_, Failed(reason)) => Finished(Err(reason))
			(Listing({ page, found }), Issues(issues)) => {
				tracking = found.concat(issues.keep_if(|issue| is_tracking_issue(issue, made.title)).map(|issue| issue.number))
				if issues.len() < made.per_page {
					decide(made, tracking)
				} else if page >= made.max_pages {
					# A tracking issue may be on a page that was not read, and opening another would duplicate it.
					Finished(Err("more than ${(made.max_pages * made.per_page).to_str()} open issues; refusing to guess whether a tracking issue exists"))
				} else {
					list(page + 1, tracking)
				}
			}
			(Commenting(number), Accepted) =>
				match made.verdict {
					Success => Do(Close(number), Closing(number))
					Failure => done("commented", number)
				}
			(Closing(number), Accepted) => done("closed", number)
			(Opening, Opened(number)) => done("opened", number)
			_ => Finished(Err("GitHub answered a request the action did not make"))
		}

	## The `gh api` call for `request`.
	call : Plan, Request -> GitHub.Call
	call = |made, request| {
		issues = "repos/${made.repository}/issues"
		match request {
			ListOpen(page) => { ..rest, path: "${issues}?state=open&sort=created&direction=asc&per_page=${made.per_page.to_str()}&page=${page.to_str()}", jq: issue_fields }
			# A lone name in braces is a block, not a record, so the field is spelled out.
			Comment(number, body) => { ..rest, method: "POST", path: "${issues}/${number.to_str()}/comments", jq: "{id}", send: Json(Json.to_str({ body: body })) }
			Close(number) => { ..rest, method: "PATCH", path: "${issues}/${number.to_str()}", jq: "{number}", send: Json(Json.to_str({ state: "closed", state_reason: "completed" })) }
			Open(title, body) => { ..rest, method: "POST", path: issues, jq: "{number}", send: Json(Json.to_str({ title, body })) }
		}
	}

	## Read what `gh` printed for `request`.
	reply : Plan, Request, GitHub.Output -> Reply
	reply = |made, request, output| {
		listed : Try(List(Issue), _)
		listed = Json.parse(output.stdout)
		created : Try({ number : U64 }, _)
		created = Json.parse(output.stdout)
		match (GitHub.answer(NightlyReport.call(made, request), output), request) {
			(Failed(reason), _) => Failed(reason)
			(Missing(reason), _) => Failed(reason)
			(Body(_), ListOpen(_)) =>
				match listed {
					Ok(issues) => Issues(issues)
					Err(_) => Failed("GitHub returned something other than a list of issues")
				}
			(Body(_), Open(_, _)) =>
				match created {
					Ok({ number }) => Opened(number)
					Err(_) => Failed("GitHub did not return the number of the issue it created")
				}
			(Body(_), _) => Accepted
		}
	}

	## The lines to append to `GITHUB_OUTPUT`.
	outputs : Report -> Str
	outputs = |report| "action=${report.action}\nissue-number=${number_text(report.issue, "")}\n"

	## One line for the job log.
	summary : Plan, Report -> Str
	summary = |made, report| "Release ${made.release} on nightly ${made.nightly}: ${made.outcome}; tracking issue ${number_text(report.issue, "#")}; action ${report.action}"

	## The text of a new tracking issue.
	issue_body : Plan -> Str
	issue_body = |made| "The examples of release `${made.release}` fail on Roc nightly `${made.nightly}`.\n\nWorkflow run: ${made.url}\n\nThe nightly compatibility workflow maintains this issue. Each later failure adds a comment, and the next passing run closes it. The fix is a patch release that works with the new compiler.\n"

	## The comment a later failure adds.
	failure_comment : Plan -> Str
	failure_comment = |made| "Still failing: release `${made.release}` on Roc nightly `${made.nightly}`.\n\nWorkflow run: ${made.url}\n"

	## The comment a pass adds before the issue is closed.
	success_comment : Plan -> Str
	success_comment = |made| "Release `${made.release}` passes on Roc nightly `${made.nightly}`. Closing.\n\nWorkflow run: ${made.url}\n"
}

# Every request is JSON, under the API version the action was written for.
rest : GitHub.Call
rest = { method: "GET", path: "", version: "2022-11-28", accept: "application/vnd.github+json", jq: "", send: Nothing }

# One record for each issue GitHub lists, so that a full page stays full. The
# author's type is read without assuming there is an author: an account that
# no longer exists has none, and is then not a bot.
issue_fields = "[.[] | {number, title, state, author: (.user.type? // \"\"), pull: has(\"pull_request\")}]"

list : U64, List(U64) -> NightlyReport.Next
list = |page, found| Do(ListOpen(page), Listing({ page, found }))

done : Str, U64 -> NightlyReport.Next
done = |action, number| Finished(Ok({ action, issue: Number(number) }))

# With every open issue read, apply the verdict to the oldest tracking issue.
decide : NightlyReport.Plan, List(U64) -> NightlyReport.Next
decide = |made, tracking|
	match (made.verdict, oldest(tracking)) {
		(Success, Absent) => Finished(Ok({ action: "none", issue: Absent }))
		(Success, Number(number)) => Do(Comment(number, NightlyReport.success_comment(made)), Commenting(number))
		(Failure, Number(number)) => Do(Comment(number, NightlyReport.failure_comment(made)), Commenting(number))
		(Failure, Absent) => Do(Open(made.title, NightlyReport.issue_body(made)), Opening)
	}

oldest : List(U64) -> [Absent, Number(U64)]
oldest = |numbers|
	match numbers {
		[] => Absent
		[first, .. as others] => Number(others.fold(first, |least, number| if number < least number else least))
	}

number_text : [Absent, Number(U64)], Str -> Str
number_text = |issue, prefix|
	match issue {
		Number(number) => "${prefix}${number.to_str()}"
		Absent => if prefix == "" "" else "absent"
	}

# An issue, not a pull request, that is open, has exactly this title, and was
# opened by a bot account.
#
# GitHub assigns the account type, which is `Bot` for `github-actions[bot]`
# and for every GitHub App, and no person can open an issue as one. The login
# is not compared, because the token may belong to an App and the action must
# still recognise the issue that token opened.
is_tracking_issue : NightlyReport.Issue, Str -> Bool
is_tracking_issue = |issue, title| !issue.pull and issue.state == "open" and issue.title == title and issue.author == "Bot"

# Only an examples step that ran to a verdict is evidence about compatibility.
classify : Str -> Try([Success, Failure], Str)
classify = |outcome|
	match outcome {
		"success" => Ok(Success)
		"failure" => Ok(Failure)
		_ => Err("setup failure: the examples step reported outcome ${shown(outcome)}, not success or failure; the examples did not run to a verdict, so the tracking issue is left alone")
	}

# A value from outside, cut short and without anything that is not printable
# ASCII, so that it cannot end the error line or add to it.
shown : Str -> Str
shown = |value| {
	bytes = Str.to_utf8(value).take_first(58).map(|byte| if byte >= ' ' and byte < 127 and byte != '\'' byte else '?')
	"'${Str.from_utf8_lossy(bytes)}'"
}

# Tags are written into issue text, so they are limited to characters that are
# inert in Markdown: no whitespace, backticks, brackets, `@` or `#`.
checked_tag : Str, Str -> Try(Str, Str)
checked_tag = |name, value| {
	starts_plain = match Str.to_utf8(value) {
		[first, ..] => first != '.' and first != '_' and first != '+' and first != '/' and first != '-'
		[] => Bool.False
	}
	if starts_plain and GitHub.is_plain(value, "._+/-") and GitHub.count(value) <= 100 {
		Ok(value)
	} else {
		Err("${name} must be a tag of at most 100 letters, digits and . _ + / - characters, starting with a letter or digit")
	}
}

# 256 characters is GitHub's own limit for an issue title.
checked_title : Str -> Try(Str, Str)
checked_title = |title| {
	points = GitHub.code_points(title)
	padded = match (points.first(), points.last()) {
		(Ok(first), Ok(last)) => is_space(first) or is_space(last)
		_ => Bool.True
	}
	if padded {
		Err("title must not be empty or begin or end with whitespace")
	} else if points.len() > 256 {
		Err("title must be at most 256 characters")
	} else if points.any(is_invisible) {
		Err("title must not contain control or format characters")
	} else {
		Ok(title)
	}
}

# Unicode white space, with the four separator controls.
is_space : U32 -> Bool
is_space = |point| within(point, space_ranges)

space_ranges : List((U32, U32))
space_ranges = [(0x09, 0x0D), (0x1C, 0x20), (0x85, 0x85), (0xA0, 0xA0), (0x1680, 0x1680), (0x2000, 0x200A), (0x2028, 0x2029), (0x202F, 0x202F), (0x205F, 0x205F), (0x3000, 0x3000)]

# Control characters, format characters (which include the bidirectional
# overrides and the zero-width marks), private-use characters and
# noncharacters: none of them shows in a title, and each can make one title
# look like another.
is_invisible : U32 -> Bool
is_invisible = |point| within(point, invisible_ranges) or point % 0x10000 >= 0xFFFE

# Inclusive ranges of code points: the controls, the format characters of
# general category Cf, the private-use areas and the noncharacters of the
# Arabic presentation forms. The last two code points of every plane are
# noncharacters too, and `is_invisible` finds those by arithmetic.
invisible_ranges : List((U32, U32))
invisible_ranges = [
	(0x00, 0x1F),
	(0x7F, 0x9F),
	(0xAD, 0xAD),
	(0x0600, 0x0605),
	(0x061C, 0x061C),
	(0x06DD, 0x06DD),
	(0x070F, 0x070F),
	(0x0890, 0x0891),
	(0x08E2, 0x08E2),
	(0x180E, 0x180E),
	(0x200B, 0x200F),
	(0x202A, 0x202E),
	(0x2060, 0x206F),
	(0xE000, 0xF8FF),
	(0xFDD0, 0xFDEF),
	(0xFEFF, 0xFEFF),
	(0xFFF9, 0xFFFB),
	(0x110BD, 0x110BD),
	(0x110CD, 0x110CD),
	(0x13430, 0x1343F),
	(0x1BCA0, 0x1BCA3),
	(0x1D173, 0x1D17A),
	(0xE0000, 0xE007F),
	(0xF0000, 0x10FFFF),
]

within : U32, List((U32, U32)) -> Bool
within = |point, ranges| ranges.any(|range| point >= range.0 and point <= range.1)

# The page a comment links to, built only from values that are safe in Markdown.
run_url : Str, Str, Str -> Try(Str, Str)
run_url = |server, repository, run_id|
	if !https_url(server) {
		Err("GITHUB_SERVER_URL must be an https URL")
	} else if !GitHub.valid_repository(repository) {
		Err("GITHUB_REPOSITORY must be an owner/name pair")
	} else if !GitHub.is_digits(run_id) or run_id.starts_with("0") or GitHub.count(run_id) > 20 {
		Err("GITHUB_RUN_ID must be a run number")
	} else {
		Ok("${without_trailing_slashes(server)}/${repository}/actions/runs/${run_id}")
	}

# `https://`, a host, an optional port and an optional path, and nothing else.
https_url : Str -> Bool
https_url = |url| {
	after = url.drop_prefix("https://")
	(authority, path) = match Str.split_first(after, "/") {
		Ok(parts) => (parts.before, parts.after)
		Err(_) => (after, "")
	}
	served = match Str.split_on(authority, ":") {
		[host] => GitHub.is_plain(host, ".-")
		[host, port] => GitHub.is_plain(host, ".-") and GitHub.is_digits(port)
		_ => Bool.False
	}
	url.starts_with("https://") and served and (path == "" or GitHub.is_plain(path, "._/-"))
}

without_trailing_slashes : Str -> Str
without_trailing_slashes = |text| if text.ends_with("/") without_trailing_slashes(text.drop_suffix("/")) else text

# Tests. `World` is the issues of one repository behind the four requests, and
# `run` applies one verdict to it with the same `start` and `advance` the
# script uses, recording every request in order.

repo = "octo/package"

nightly_tag = "nightly-2026-10-12-0a1b2c3"

release_tag = "0.4.1"

run_link = "https://github.com/octo/package/actions/runs/987654321"

title = NightlyReport.default_title

environment : NightlyReport.Inputs
environment = { outcome: "failure", nightly_tag, release_tag, title, server: "https://github.com", repository: repo, run_id: "987654321", token: "ghs_secret" }

unplanned : NightlyReport.Plan
unplanned = { verdict: Failure, outcome: "", nightly: "", release: "", title: "", repository: "", url: "", per_page: 0, max_pages: 0 }

planned : NightlyReport.Inputs -> NightlyReport.Plan
planned = |inputs| NightlyReport.plan(inputs) ?? unplanned

failing : NightlyReport.Plan
failing = planned(environment)

passing : NightlyReport.Plan
passing = planned({ ..environment, outcome: "success" })

refusal : NightlyReport.Inputs -> Str
refusal = |inputs|
	match NightlyReport.plan(inputs) {
		Err(reason) => reason
		Ok(_) => "accepted"
	}

World : { issues : List(NightlyReport.Issue), comments : List((U64, Str)), asked : List(NightlyReport.Request) }

issue : U64 -> NightlyReport.Issue
issue = |number| { number, title, state: "open", author: "Bot", pull: Bool.False }

by_person : U64 -> NightlyReport.Issue
by_person = |number| { ..issue(number), author: "User" }

world : List(NightlyReport.Issue) -> World
world = |issues| { issues, comments: [], asked: [] }

respond : NightlyReport.Plan, World, NightlyReport.Request -> (World, NightlyReport.Reply)
respond = |made, before, request| {
	now = { ..before, asked: before.asked.append(request) }
	match request {
		ListOpen(page) => (now, Issues(now.issues.keep_if(|item| item.state == "open").sublist({ start: (page - 1) * made.per_page, len: made.per_page })))
		Comment(number, body) => (if now.issues.any(|item| item.number == number and item.state == "open") { ..now, comments: now.comments.append((number, body)) } else now, Accepted)
		Close(number) => ({ ..now, issues: now.issues.map(|item| if item.number == number { ..item, state: "closed" } else item) }, Accepted)
		Open(new_title, _) => {
			number = now.issues.fold(0, |most, item| if item.number > most item.number else most) + 1
			({ ..now, issues: now.issues.append({ ..issue(number), title: new_title }) }, Opened(number))
		}
	}
}

drive : NightlyReport.Plan, World, NightlyReport.Next -> { after : World, result : Try(NightlyReport.Report, Str) }
drive = |made, before, next|
	match next {
		Finished(result) => { after: before, result }
		Do(request, stage) => {
			(now, answer) = respond(made, before, request)
			drive(made, now, NightlyReport.advance(made, stage, answer))
		}
	}

run : NightlyReport.Plan, World -> { after : World, result : Try(NightlyReport.Report, Str) }
run = |made, before| drive(made, { ..before, asked: [] }, NightlyReport.start(made))

writes : World -> List(NightlyReport.Request)
writes = |now|
	now.asked.keep_if(
		|request|
			match request {
				ListOpen(_) => Bool.False
				_ => Bool.True
			},
	)

did : Str, U64 -> Try(NightlyReport.Report, Str)
did = |action, number| Ok({ action, issue: Number(number) })

nothing : Try(NightlyReport.Report, Str)
nothing = Ok({ action: "none", issue: Absent })

unrelated : NightlyReport.Issue
unrelated = { ..by_person(1), title: "Unrelated bug" }

# Verdicts

# A pass with no tracking issue does nothing.
expect run(passing, world([unrelated])).result == nothing and writes(run(passing, world([unrelated])).after) == []

# A pass comments on the open issue, then closes it.
expect {
	ran = run(passing, world([issue(7)]))
	[
		ran.result == did("closed", 7),
		ran.after.issues == [{ ..issue(7), state: "closed" }],
		writes(ran.after) == [Comment(7, "Release `0.4.1` passes on Roc nightly `nightly-2026-10-12-0a1b2c3`. Closing.\n\nWorkflow run: ${run_link}\n"), Close(7)],
		ran.after.comments.map(|comment| comment.0) == [7],
	].all(|held| held)
}

# The first failure opens the issue, and adds no comment.
expect {
	ran = run(failing, world([unrelated]))
	[
		ran.result == did("opened", 2),
		writes(ran.after) == [Open(title, NightlyReport.issue_body(failing))],
		ran.after.issues.map(|item| item.title) == ["Unrelated bug", title],
		ran.after.comments == [],
	].all(|held| held)
}
expect NightlyReport.issue_body(failing).starts_with("The examples of release `0.4.1` fail on Roc nightly `nightly-2026-10-12-0a1b2c3`.\n\nWorkflow run: ${run_link}\n\n")

# A later failure comments instead of opening another.
expect {
	later = planned({ ..environment, nightly_tag: "nightly-2026-10-13-fff" })
	ran = run(later, world([issue(7)]))
	[
		ran.result == did("commented", 7),
		ran.after.issues == [issue(7)],
		ran.after.comments == [(7, "Still failing: release `0.4.1` on Roc nightly `nightly-2026-10-13-fff`.\n\nWorkflow run: ${run_link}\n")],
	].all(|held| held)
}

# One issue for each incident, across a sequence of runs.
expect {
	first = run(passing, world([]))
	second = run(failing, first.after)
	third = run(failing, second.after)
	fourth = run(passing, third.after)
	fifth = run(passing, fourth.after)
	sixth = run(failing, fifth.after)
	[
		[first.result, second.result, third.result, fourth.result, fifth.result, sixth.result] == [nothing, did("opened", 1), did("commented", 1), did("closed", 1), nothing, did("opened", 2)],
		sixth.after.issues.map(|item| (item.number, item.state)) == [(1, "closed"), (2, "open")],
		sixth.after.comments.map(|comment| comment.0) == [1, 1],
	].all(|held| held)
}

# A custom title selects a different issue.
expect {
	other = "Nightly: macOS examples fail"
	ran = run(planned({ ..environment, outcome: "success", title: other }), world([issue(3), { ..issue(4), title: other }]))
	ran.result == did("closed", 4) and ran.after.issues.map(|item| item.state) == ["open", "closed"]
}

# Setup failures

not_verdicts = ["", "skipped", "cancelled", "neutral", "timed_out", "Success", "FAILURE", "success ", " failure", "failure\n", "true", "0", "success\nfailure"]

# Any other outcome is an error, and no plan exists to make a request from.
expect not_verdicts.all(|outcome| refusal({ ..environment, outcome }).starts_with("setup failure: "))
# The tags are typically empty too when setup failed; the outcome is still what is reported.
expect not_verdicts.all(|outcome| refusal({ ..environment, outcome, nightly_tag: "", release_tag: "", token: "" }).starts_with("setup failure: "))
expect refusal({ ..environment, outcome: "skipped" }) == "setup failure: the examples step reported outcome 'skipped', not success or failure; the examples did not run to a verdict, so the tracking issue is left alone"
# The error text cannot carry a long or a multi-line outcome.
expect {
	text = refusal({ ..environment, outcome: "${Str.repeat("x", 500)}\n::notice::forged" })
	!text.contains("\n") and !text.contains("forged") and text.contains("'${Str.repeat("x", 58)}'")
}
expect shown("a\nb'c\u(001b)é") == "'a?b?c???'"

# Ownership

# An issue with the title that a person opened is never touched.
expect {
	passed = run(passing, world([by_person(5)]))
	failed = run(failing, world([by_person(5)]))
	[
		passed.result == nothing,
		passed.after.issues == [by_person(5)],
		failed.result == did("opened", 6),
		failed.after.issues == [by_person(5), issue(6)],
		failed.after.comments == [],
	].all(|held| held)
}

# The bot's issue is used while a person's twin is left alone.
expect {
	failed = run(failing, world([by_person(5), issue(6)]))
	passed = run(passing, failed.after)
	[
		failed.result == did("commented", 6),
		passed.result == did("closed", 6),
		passed.after.issues == [by_person(5), { ..issue(6), state: "closed" }],
		passed.after.comments.map(|comment| comment.0) == [6, 6],
	].all(|held| held)
}

# The author must be a bot account by type. No login is read at all.
expect ["User", "Organization", "bot", "BOT", "Bot ", "github-actions[bot]", ""].all(|author| !is_tracking_issue({ ..issue(5), author }, title))
expect is_tracking_issue(issue(5), title)

# A closed issue is not reopened, and is rejected even if a listing returns it.
expect {
	closed = { ..issue(3), state: "closed" }
	passed = run(passing, world([closed]))
	failed = run(failing, world([closed]))
	passed.result == nothing and failed.result == did("opened", 4) and failed.after.issues == [closed, issue(4)] and failed.after.comments == []
}
expect !is_tracking_issue({ ..issue(3), state: "closed" }, title)

# A pull request with the title is not an issue.
expect {
	pull = { ..issue(8), pull: Bool.True }
	passed = run(passing, world([pull]))
	failed = run(failing, world([pull]))
	passed.result == nothing and failed.result == did("opened", 9) and failed.after.issues == [pull, issue(9)] and failed.after.comments == []
}

# The title must match exactly.
expect {
	near = ["the latest release fails on the latest roc nightly", "${title} ", " ${title}", "${title} (macOS)", "latest release fails on the latest Roc nightly", "The latest release fails on the latest roc nightly", ""]
	before = world(near.map_with_index(|text, index| { ..issue(index + 1), title: text }))
	passed = run(passing, before)
	failed = run(failing, before)
	passed.result == nothing and passed.after.issues == before.issues and writes(passed.after) == [] and failed.result == did("opened", 8)
}

# The oldest open tracking issue is the one used, wherever the listing puts it.
expect run(failing, world([issue(12), issue(9), issue(30)])).result == did("commented", 9)
expect oldest([]) == Absent and oldest([4]) == Number(4) and oldest([12, 9, 30]) == Number(9)

# Listing

filler : U64 -> List(NightlyReport.Issue)
filler = |total| List.repeat(unrelated, total).map_with_index(|item, index| { ..item, number: index + 1 })

paged : NightlyReport.Plan -> NightlyReport.Plan
paged = |made| { ..made, per_page: 2 }

# A tracking issue beyond the first page is found.
expect {
	ran = run(paged(failing), world(filler(5).append(issue(6))))
	ran.result == did("commented", 6) and ran.after.asked.take_first(3) == [ListOpen(1), ListOpen(2), ListOpen(3)]
}

# A full last page is followed by one more request.
expect {
	ran = run(paged(passing), world(filler(2)))
	ran.result == nothing and ran.after.asked == [ListOpen(1), ListOpen(2)]
}

# Too many open issues is an error, never a duplicate.
expect {
	ran = run({ ..paged(failing), max_pages: 2 }, world(filler(4).append(issue(5))))
	ran.result == Err("more than 4 open issues; refusing to guess whether a tracking issue exists") and writes(ran.after) == []
}

# A failed request ends the run with nothing after it.
expect NightlyReport.advance(failing, Listing({ page: 1, found: [] }), Failed("GitHub answered GET repos/octo/package/issues with HTTP 403: Forbidden")) == Finished(Err("GitHub answered GET repos/octo/package/issues with HTTP 403: Forbidden"))
expect NightlyReport.advance(passing, Commenting(7), Failed("x")) == Finished(Err("x")) and NightlyReport.advance(failing, Opening, Failed("x")) == Finished(Err("x"))
expect NightlyReport.advance(failing, Opening, Accepted) == Finished(Err("GitHub answered a request the action did not make"))

# Inputs

# Ordinary tags are accepted.
expect ["0.4.1", "v1.2.3", "1.0.0-rc.1", "1.0.0+build.5", "nightly-2026-10-12-0a1b2c3", "release/2026.10", Str.repeat("a", 100)].all(|tag| checked_tag("release-tag", tag) == Ok(tag))

bad_tags = ["", " ", "0.4.1 ", "a b", "0.4.1\n0.4.0", "`0.4.1`", "@octocat", "#12", "[x](https://evil.example)", "<b>", "-rf", ".hidden", Str.repeat("a", 101), "0.4.1\u(0000)", "0.4.1\u(202e)"]

# Malformed tags are rejected before any request.
expect bad_tags.all(|tag| refusal({ ..environment, nightly_tag: tag }).starts_with("nightly-tag must be a tag"))
expect bad_tags.all(|tag| refusal({ ..environment, release_tag: tag }).starts_with("release-tag must be a tag"))

# Titles within the limits are accepted.
expect [title, Str.repeat("x", 256), "Nightly: la dernière version échoue", "Nightly (macOS) #2"].all(|text| checked_title(text) == Ok(text))

# Malformed titles are rejected before any request.
expect ["", " ", " leading", "trailing ", "two\nlines", "tab\there", "nul\u(0000)", "bell\u(0007)", "bidi\u(202e)override", "zero\u(200b)width", Str.repeat("x", 257), "\u(00a0)padded", "private\u(e000)use", "tag\u(e0041)"].all(|text| refusal({ ..environment, title: text }).starts_with("title must"))
expect refusal({ ..environment, title: Str.repeat("é", 257) }) == "title must be at most 256 characters" and checked_title(Str.repeat("é", 256)).is_ok()

# The run link is built from the runner's environment.
expect run_url("https://github.com", repo, "987654321") == Ok(run_link)
expect run_url("https://ghe.example.com/", "o/r.s_t", "1") == Ok("https://ghe.example.com/o/r.s_t/actions/runs/1")
expect failing.url == run_link

# A malformed environment is rejected.
expect ["http://github.com", "", "https://github.com/?x=[a](b)", "https://", "https://github.com:port", "file:///etc/passwd"].all(|server| run_url(server, repo, "1") == Err("GITHUB_SERVER_URL must be an https URL"))
expect ["octo", "octo/package/extra", "octo/pack age"].all(|name| run_url("https://github.com", name, "1") == Err("GITHUB_REPOSITORY must be an owner/name pair"))
expect ["", "12/../3", "012", "abc", Str.repeat("9", 21)].all(|run_id| run_url("https://github.com", repo, run_id) == Err("GITHUB_RUN_ID must be a run number"))
expect refusal({ ..environment, repository: "octo" }) == "GITHUB_REPOSITORY must be an owner/name pair" and refusal({ ..environment, run_id: "abc" }) == "GITHUB_RUN_ID must be a run number"

# The token is checked before any request, and is not part of the plan.
expect ["", "two words", "line\nbreak"].all(|token| refusal({ ..environment, token }) == "token must not be empty or contain whitespace")
expect !Str.inspect(failing).contains("ghs_secret")

# Outputs

expect NightlyReport.outputs({ action: "opened", issue: Number(1) }) == "action=opened\nissue-number=1\n"
expect NightlyReport.outputs({ action: "commented", issue: Number(7) }) == "action=commented\nissue-number=7\n" and NightlyReport.outputs({ action: "closed", issue: Number(7) }) == "action=closed\nissue-number=7\n"
expect NightlyReport.outputs({ action: "none", issue: Absent }) == "action=none\nissue-number=\n"
expect NightlyReport.summary(passing, { action: "none", issue: Absent }) == "Release 0.4.1 on nightly nightly-2026-10-12-0a1b2c3: success; tracking issue absent; action none"
expect NightlyReport.summary(failing, { action: "opened", issue: Number(3) }) == "Release 0.4.1 on nightly nightly-2026-10-12-0a1b2c3: failure; tracking issue #3; action opened"

# Requests

json = "application/vnd.github+json"

# The listing asks only for open issues, oldest first, one page at a time, and sends no body.
expect NightlyReport.call(failing, ListOpen(3)) == { method: "GET", path: "repos/octo/package/issues?state=open&sort=created&direction=asc&per_page=100&page=3", version: "2022-11-28", accept: json, jq: issue_fields, send: Nothing }
expect GitHub.stdin(NightlyReport.call(failing, ListOpen(1))) == [] and !GitHub.args(NightlyReport.call(failing, ListOpen(1))).contains("--input")
expect issue_fields == "[.[] | {number, title, state, author: (.user.type? // \"\"), pull: has(\"pull_request\")}]"

# Each write names its method and its one resource, and carries its text as a JSON body.
expect NightlyReport.call(failing, Comment(7, "a \"b\"\nc")) == { method: "POST", path: "repos/octo/package/issues/7/comments", version: "2022-11-28", accept: json, jq: "{id}", send: Json("{\"body\":\"a \\\"b\\\"\\nc\"}") }
expect NightlyReport.call(passing, Close(7)) == { method: "PATCH", path: "repos/octo/package/issues/7", version: "2022-11-28", accept: json, jq: "{number}", send: Json("{\"state\":\"closed\",\"state_reason\":\"completed\"}") }
expect NightlyReport.call(failing, Open(title, "text")) == { method: "POST", path: "repos/octo/package/issues", version: "2022-11-28", accept: json, jq: "{number}", send: Json("{\"body\":\"text\",\"title\":\"The latest release fails on the latest Roc nightly\"}") }

# The token is in no argument and no body of any request.
expect [ListOpen(1), Comment(7, "x"), Close(7), Open(title, "x")].all(|request| !Str.inspect(GitHub.args(NightlyReport.call(failing, request))).contains("ghs_secret") and !Str.inspect(GitHub.stdin(NightlyReport.call(failing, request))).contains("ghs_secret"))

# Answers

printed : Str -> GitHub.Output
printed = |stdout| { succeeded: Bool.True, stdout, stderr: "" }

refused : GitHub.Output
refused = { succeeded: Bool.False, stdout: "{\"message\":\"Resource not accessible by integration\",\"status\":\"403\"}", stderr: "gh: Resource not accessible by integration (HTTP 403)" }

expect NightlyReport.reply(failing, ListOpen(1), printed("[]")) == Issues([])
expect NightlyReport.reply(failing, ListOpen(1), printed("[{\"author\":\"Bot\",\"number\":7,\"pull\":false,\"state\":\"open\",\"title\":\"A \\\"quoted\\\" title\"},{\"author\":\"\",\"number\":8,\"pull\":true,\"state\":\"open\",\"title\":\"\"}]")) == Issues([{ number: 7, title: "A \"quoted\" title", state: "open", author: "Bot", pull: Bool.False }, { number: 8, title: "", state: "open", author: "", pull: Bool.True }])
# Anything other than a list of issue records is an error, including one record that is malformed.
expect ["{\"message\":\"Not Found\"}", "", "<html>", "[null]", "[\"issue\"]", "[{\"title\":\"t\",\"state\":\"open\",\"author\":\"Bot\",\"pull\":false}]", "[{\"number\":\"7\",\"title\":\"t\",\"state\":\"open\",\"author\":\"Bot\",\"pull\":false}]", "[{\"number\":true,\"title\":\"t\",\"state\":\"open\",\"author\":\"Bot\",\"pull\":false}]"].all(|stdout| NightlyReport.reply(failing, ListOpen(1), printed(stdout)) == Failed("GitHub returned something other than a list of issues"))
expect NightlyReport.reply(failing, Open(title, "x"), printed("{\"number\":5}")) == Opened(5)
# A creation that returns no number is an error.
expect ["{\"message\":\"created?\"}", "{\"number\":null}", ""].all(|stdout| NightlyReport.reply(failing, Open(title, "x"), printed(stdout)) == Failed("GitHub did not return the number of the issue it created"))
expect NightlyReport.reply(failing, Comment(7, "x"), printed("{\"id\":1}")) == Accepted and NightlyReport.reply(passing, Close(7), printed("{\"number\":7}")) == Accepted
# An HTTP error names the call and the status, without the query.
expect NightlyReport.reply(failing, Comment(7, "x"), refused) == Failed("GitHub answered POST repos/octo/package/issues/7/comments with HTTP 403: Resource not accessible by integration")
expect NightlyReport.reply(failing, ListOpen(2), refused) == Failed("GitHub answered GET repos/octo/package/issues with HTTP 403: Resource not accessible by integration")
expect NightlyReport.reply(failing, ListOpen(1), { succeeded: Bool.False, stdout: "{\"message\":\"Not Found\",\"status\":\"404\"}", stderr: "gh: Not Found (HTTP 404)" }) == Failed("GitHub answered GET repos/octo/package/issues with HTTP 404")
expect NightlyReport.reply(failing, ListOpen(1), { succeeded: Bool.False, stdout: "", stderr: "error connecting to api.github.com" }) == Failed("GET repos/octo/package/issues failed: error connecting to api.github.com")
