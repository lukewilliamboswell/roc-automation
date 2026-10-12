import GitHub

## Stage a draft release, verify every uploaded asset against the tested file,
## then publish.
##
## Nothing here has an effect. The work is two sequences, each decided here one
## step at a time from the answers so far:
##
## * `collect` and `found` check the inputs and the files. The only things they
##   can ask for are on the runner's disk, so no request reaches GitHub before
##   every one of those checks has passed.
## * `publish` and `answered` then make the requests. Publication is the last
##   request that changes anything, and every check precedes it.
##
## The script carries each step to the disk or to `gh` and brings the answer
## back, so the order of the steps and what a failure leaves behind are covered
## by the `expect` tests below.
PublishRelease := [].{

	## The inputs of the action and the runner's environment, as text. A
	## variable that is not set is the empty string.
	Inputs : {
		version : Str,
		prerelease : Str,
		notes_file : Str,
		assets : Str,
		tag : Str,
		target : Str,
		repository : Str,
		token : Str,
		workspace : Str,
	}

	## One tested file: where it is, and the size and SHA-256 it had when it
	## was collected.
	Asset : { name : Str, path : Str, size : U64, sha256 : Str }

	## Everything decided from the inputs and the local files, before any
	## request is made. Assets are in name order. It does not hold the token.
	Plan : {
		version : Str,
		tag : Str,
		prerelease : Bool,
		target : Str,
		notes : Str,
		assets : List(Asset),
		repository : Str,
	}

	## Something to look at on the runner's disk. Paths are absolute.
	Local : [ReadNotes(Str), ListDirectory(Str), Inspect(Str), Hash(Str)]

	## What was there. `Names` are the entries of a directory, and are none
	## for a path that is not a directory. `Digest` is what `sha256sum` printed.
	Found : [
		Notes([Text(Str), NotText, SymLink, NotRegular, Absent]),
		Names(List(Str)),
		Kind([File(U64), SymLink, Other, Absent]),
		Digest(Str),
		Unreadable(Str),
	]

	## What has been established so far, and what is being waited for.
	Gathering : {
		waiting : [ForNotes(Str), ForNames(Str), ForKind(Str), ForDigest({ path : Str, name : Str, size : U64 })],
		inputs : Inputs,
		tag : Str,
		prerelease : Bool,
		notes : Str,
		patterns : List(Str),
		matching : [Idle, Matching({ pattern : Str, segment : Str, rest : List(Str), directories : List(Str), hits : List(Str) })],
		matched : List(Str),
		paths : List(Str),
		assets : List(Asset),
	}

	## The next thing to look at, or the end of the local checks.
	Collecting : [Look(Local, Gathering), Planned(Plan), Rejected(Str)]

	## A request to GitHub. `Upload`, `CreateDraft` and `Publish` change the
	## repository; the others read it.
	Remote : [
		CommitExists(Str),
		TagExists(Str),
		ListReleases(U64),
		CreateDraft,
		Upload({ url : Str, asset : Asset }),
		ListAssets({ release : U64, page : U64 }),
		Download({ id : U64, name : Str, limit : U64 }),
		Publish(U64),
		ReadBack(U64),
	]

	## A release as the release list shows it. `draft` is GitHub's value as
	## text, so that anything other than `false` reads as a draft.
	Listed : { id : U64, tag_name : Str, draft : Str, html_url : Str }

	## One asset of the draft, as GitHub lists it.
	Record : { id : U64, name : Str, size : U64, state : Str }

	## GitHub's answer to one request. `Downloaded` is the size of the bytes
	## that came back and what `sha256sum` printed for them.
	Reply : [
		Exists(Bool),
		Releases(List(Listed)),
		Created({ id : U64, tag_name : Str, draft : Str, html_url : Str, upload_url : Str }),
		Uploaded(Str),
		Assets(List(Record)),
		Downloaded({ size : U64, digest : Str }),
		Accepted,
		Release({ tag_name : Str, draft : Str, prerelease : Str, immutable : Str, html_url : Str }),
		Failed(Str),
	]

	## The draft this run created. Every later request names it by `id`.
	Draft : { id : U64, url : Str, upload : Str }

	## What the publication is waiting for.
	Stage : [
		AwaitCommit,
		AwaitTag,
		AwaitReleases(U64),
		AwaitDraft,
		Uploading({ draft : Draft, asset : Asset, rest : List(Asset) }),
		Listing({ draft : Draft, page : U64, records : List(Record) }),
		Downloading({ draft : Draft, record : Record, rest : List(Record) }),
		AwaitPublish(Draft),
		AwaitReadBack({ draft : Draft, failure : Str }),
	]

	## The next request, or the end: the URL of the published release, or why
	## there is none.
	Publishing : [Ask(Remote, Stage), Published(Str), Stopped(Str)]

	## Check the inputs that need no file, then ask for the notes file.
	collect : Inputs -> Collecting
	collect = |inputs|
		match checked_inputs(inputs) {
			Err(reason) => Rejected(reason)
			Ok({ tag, prerelease }) => {
				notes = within(inputs.workspace, inputs.notes_file)
				Look(
					ReadNotes(notes),
					{ waiting: ForNotes(notes), inputs, tag, prerelease, notes: "", patterns: [], matching: Idle, matched: [], paths: [], assets: [] },
				)
			}
		}

	## Decide what follows `seen`, which is what the last `Look` found.
	found : Gathering, Found -> Collecting
	found = |state, seen|
		match (state.waiting, seen) {
			(_, Unreadable(reason)) => Rejected(reason)
			(ForNotes(path), Notes(notes)) =>
				match checked_notes(path, notes) {
					Ok(text) => expand({ ..state, notes: text, patterns: Str.split_on(state.inputs.assets, "\n").map(|line| line.trim()).keep_if(|line| line != "") })
					Err(reason) => Rejected(reason)
				}
			(ForNames(directory), Names(names)) =>
				match state.matching {
					Matching(now) => {
						hits = GitHub.sort(names.keep_if(|name| matches(now.segment, name))).map(|name| if directory == "" name else "${directory}/${name}")
						expand({ ..state, matching: Matching({ ..now, hits: now.hits.concat(hits) }) })
					}
					Idle => Rejected(out_of_turn)
				}
			(ForKind(path), Kind(SymLink)) => Rejected("asset is a symbolic link: ${path}")
			(ForKind(path), Kind(File(size))) => {
				name = file_name(path)
				match state.assets.keep_if(|asset| asset.name == name) {
					[other, ..] => Rejected("two assets share the name ${name}: ${other.path} and ${path}")
					[] if !asset_name(name) => Rejected("asset name must use only letters, digits, '.', '_' and '-': ${name}")
					[] if size == 0 => Rejected("asset is empty: ${path}")
					[] => Look(Hash(path), { ..state, waiting: ForDigest({ path, name, size }) })
				}
			}
			(ForKind(path), Kind(_)) => Rejected("asset is not a regular file: ${path}")
			(ForDigest({ path, name, size }), Digest(printed)) =>
				match digest_of(printed) {
					Ok(sha256) => inspect({ ..state, assets: state.assets.append({ name, path, size, sha256 }) })
					Err(NoDigest) => Rejected("sha256sum printed no digest for ${path}")
				}
			_ => Rejected(out_of_turn)
		}

	## The first request: whether the target is a commit of the repository.
	publish : Plan -> Publishing
	publish = |made| Ask(CommitExists(made.target), AwaitCommit)

	## Decide what follows `reply`, the answer to the request made at `stage`.
	##
	## Nothing is public until the `Publish` request, so a failure after the
	## draft exists says that the draft is left unpublished and names it. A
	## publish request can take effect even when its answer is lost, so the
	## release is read back whatever that answer was.
	answered : Plan, Stage, Reply -> Publishing
	answered = |made, stage, reply|
		match (stage, reply) {
			(AwaitCommit, Exists(here)) => if here Ask(TagExists(made.tag), AwaitTag) else Stopped("target ${made.target} is not a commit in this repository")
			(AwaitTag, Exists(here)) => if here Stopped("the tag ${made.tag} already exists; a version is never published twice") else Ask(ListReleases(1), AwaitReleases(1))
			# A draft has no tag yet, so only the list of releases shows it.
			(AwaitReleases(page), Releases(listed)) =>
				match listed.keep_if(|release| release.tag_name == made.tag) {
					[existing, ..] => Stopped("a ${if existing.draft == "false" "release" else "draft release"} for ${made.tag} already exists: ${existing.html_url}")
					[] if listed.len() < per_page => Ask(CreateDraft, AwaitDraft)
					[] if page >= max_pages => Stopped("more than ${(max_pages * per_page).to_str()} releases; refusing to guess whether one exists for ${made.tag}")
					[] => Ask(ListReleases(page + 1), AwaitReleases(page + 1))
				}
			(AwaitDraft, Created(release)) => {
				draft = { id: release.id, url: release.html_url, upload: upload_root(release.upload_url) }
				if release.draft != "true" or release.tag_name != made.tag {
					unpublished(made, draft, "GitHub did not create the requested draft")
				} else if draft.upload == "" {
					unpublished(made, draft, "the draft has no https upload URL")
				} else {
					upload(draft, made.assets)
				}
			}
			# Whether the request created a draft is not known when its answer is lost or unreadable.
			(AwaitDraft, Failed(reason)) => Stopped("${reason}. If the releases page now shows a draft for ${made.tag}, delete it before running this job again")
			(Uploading({ draft, asset, rest }), Uploaded(name)) =>
				if name == asset.name upload(draft, rest) else unpublished(made, draft, "GitHub stored ${asset.name} as ${name}")
			(Listing({ draft, page, records }), Assets(listed)) => {
				held = records.concat(listed)
				if listed.len() == per_page and page < max_pages {
					list_assets(draft, page + 1, held)
				} else {
					match check_inventory(made.assets, held) {
						Ok({}) => download(draft, held.sort_with(|left, right| by_name(left.name, right.name)))
						Err(reason) => unpublished(made, draft, reason)
					}
				}
			}
			(Downloading({ draft, record, rest }), Downloaded({ size, digest })) =>
				match made.assets.keep_if(|asset| asset.name == record.name) {
					[asset] if size == asset.size and digest_of(digest) == Ok(asset.sha256) => download(draft, rest)
					_ => unpublished(made, draft, "${record.name} downloaded from the draft differs from the tested file")
				}
			(AwaitPublish(draft), Accepted) => Ask(ReadBack(draft.id), AwaitReadBack({ draft, failure: "" }))
			(AwaitPublish(draft), Failed(reason)) => Ask(ReadBack(draft.id), AwaitReadBack({ draft, failure: reason }))
			(AwaitReadBack({ draft, failure }), Failed(reason)) =>
				Stopped("${if failure == "" reason else failure}. The release could not be read after the publish request, so ${described(draft)} may or may not be published. Look at the releases page: if ${made.tag} is published, do not run this job again")
			(AwaitReadBack({ draft, failure }), Release(release)) =>
				if release.draft != "false" {
					Stopped("${if failure == "" "GitHub accepted the publish request without publishing" else failure}. Nothing was published: ${described(draft)} is verified but left unpublished and the tag ${made.tag} has not been created. Delete the draft, then run this job again")
				} else if release.tag_name != made.tag or release.prerelease != (if made.prerelease "true" else "false") {
					Stopped("${release.html_url} is published, but not as the ${if made.prerelease "prerelease" else "release"} ${made.tag}; do not run this job again")
				} else if release.immutable != "true" {
					Stopped("${release.html_url} is published with the verified assets, but it is not immutable. Enable immutable releases in the repository settings; do not run this job again")
				} else {
					Published(release.html_url)
				}
			(AwaitCommit, Failed(reason)) => Stopped(reason)
			(AwaitTag, Failed(reason)) => Stopped(reason)
			(AwaitReleases(_), Failed(reason)) => Stopped(reason)
			(Uploading({ draft, .. }), Failed(reason)) => unpublished(made, draft, reason)
			(Listing({ draft, .. }), Failed(reason)) => unpublished(made, draft, reason)
			(Downloading({ draft, .. }), Failed(reason)) => unpublished(made, draft, reason)
			(Uploading({ draft, .. }), _) => unpublished(made, draft, out_of_turn)
			(Listing({ draft, .. }), _) => unpublished(made, draft, out_of_turn)
			(Downloading({ draft, .. }), _) => unpublished(made, draft, out_of_turn)
			_ => Stopped(out_of_turn)
		}

	## The `gh api` call for `request`.
	##
	## Every request names the repository and, once the draft exists, its
	## numeric identifier: nothing is looked up by tag or by name, so a draft
	## this run did not create is never adopted.
	call : Plan, Remote -> GitHub.Call
	call = |made, request| {
		root = "repos/${made.repository}"
		match request {
			CommitExists(sha) => { ..rest, path: "${root}/git/commits/${sha}", jq: "{sha}" }
			# `git/ref` answers for exactly this tag; `git/refs` would also match longer names.
			TagExists(tag) => { ..rest, path: "${root}/git/ref/tags/${tag}", jq: "{ref}" }
			ListReleases(page) => { ..rest, path: "${root}/releases?per_page=${per_page.to_str()}&page=${page.to_str()}", jq: "[.[] | {id, tag_name: (.tag_name // \"\"), draft: (.draft | tostring), html_url: (.html_url // \"\")}]" }
			# The notes are sent as written. `generate_release_notes` is never set, and a draft is never the latest release.
			CreateDraft => {
				..rest,
				method: "POST",
				path: "${root}/releases",
				jq: "{id, tag_name: (.tag_name // \"\"), draft: (.draft | tostring), html_url: (.html_url // \"\"), upload_url: (.upload_url // \"\")}",
				send: Json(Json.to_str({ tag_name: made.tag, target_commitish: made.target, name: made.tag, body: made.notes, draft: Bool.True, prerelease: made.prerelease, make_latest: "false" })),
			}
			Upload({ url, asset }) => { ..rest, method: "POST", path: "${url}?name=${asset.name}", jq: "{name: (.name // \"\")}", send: File(asset.path) }
			ListAssets({ release, page }) => { ..rest, path: "${root}/releases/${release.to_str()}/assets?per_page=${per_page.to_str()}&page=${page.to_str()}", jq: "[.[] | {id, name, size, state: (.state // \"\")}]" }
			# The bytes of the asset, not its description.
			Download({ id, .. }) => { ..rest, path: "${root}/releases/assets/${id.to_str()}", accept: "application/octet-stream" }
			# A prerelease is never the latest release; any other version is.
			Publish(release) => {
				..rest,
				method: "PATCH",
				path: "${root}/releases/${release.to_str()}",
				jq: "{id}",
				send: Json(Json.to_str({ draft: Bool.False, prerelease: made.prerelease, make_latest: if made.prerelease "false" else "true" })),
			}
			ReadBack(release) => { ..rest, path: "${root}/releases/${release.to_str()}", jq: "{tag_name: (.tag_name // \"\"), draft: (.draft | tostring), prerelease: (.prerelease | tostring), immutable: (.immutable | tostring), html_url: (.html_url // \"\")}" }
		}
	}

	## How long `request` may take, in milliseconds, and how many bytes of its
	## answer are kept. A download is cut off one byte past the tested size,
	## which is enough to show that it is too long.
	budget : Remote -> { timeout_ms : U64, capture : U64 }
	budget = |request|
		match request {
			Upload(_) => { timeout_ms: 1_800_000, capture: 16_777_216 }
			Download({ limit, .. }) => { timeout_ms: 600_000, capture: limit + 1 }
			_ => { timeout_ms: 120_000, capture: 16_777_216 }
		}

	## Read what `gh` printed for `request`. The bytes of a download are not
	## read here: the script writes them to a new file and reports its size
	## and digest as `Downloaded`.
	reply : Plan, Remote, GitHub.Output -> Reply
	reply = |made, request, output| {
		asked = PublishRelease.call(made, request)
		listed : Try(List(Listed), _)
		listed = Json.parse(output.stdout)
		created : Try({ id : U64, tag_name : Str, draft : Str, html_url : Str, upload_url : Str }, _)
		created = Json.parse(output.stdout)
		uploaded : Try({ name : Str }, _)
		uploaded = Json.parse(output.stdout)
		records : Try(List(Record), _)
		records = Json.parse(output.stdout)
		release : Try({ tag_name : Str, draft : Str, prerelease : Str, immutable : Str, html_url : Str }, _)
		release = Json.parse(output.stdout)
		unexpected = Failed(GitHub.unexpected(asked))
		match (GitHub.answer(asked, output), request) {
			(Failed(reason), _) => Failed(reason)
			# An absent commit or tag is an answer; an absent anything else is a failure.
			(Missing(_), CommitExists(_)) => Exists(Bool.False)
			(Missing(_), TagExists(_)) => Exists(Bool.False)
			(Missing(reason), _) => Failed(reason)
			(Body(_), CommitExists(_)) => Exists(Bool.True)
			(Body(_), TagExists(_)) => Exists(Bool.True)
			(Body(_), ListReleases(_)) => listed.map_ok(|read| Releases(read)) ?? unexpected
			(Body(_), CreateDraft) => created.map_ok(|read| Created(read)) ?? unexpected
			(Body(_), Upload(_)) => uploaded.map_ok(|read| Uploaded(read.name)) ?? unexpected
			(Body(_), ListAssets(_)) => records.map_ok(|read| Assets(read)) ?? unexpected
			(Body(_), Download(_)) => unexpected
			(Body(_), Publish(_)) => Accepted
			(Body(_), ReadBack(_)) => release.map_ok(|read| Release(read)) ?? unexpected
		}
	}

	## The listing `sha256sum` prints and `sha256sum --check` reads, in name order.
	digest_listing : Plan -> Str
	digest_listing = |made| Str.join_with(made.assets.map(|asset| "${asset.sha256}  ${asset.name}\n"), "")

	## The part of the job summary for a published release.
	summary : Plan, Str -> Str
	summary = |made, url| "### ${if made.prerelease "Prerelease" else "Release"} ${made.tag}\n\n${url}\n\nSHA-256 of each published asset, verified against the tested files:\n\n```\n${PublishRelease.digest_listing(made)}```\n"
}

# The body of a release may hold this many characters.
max_notes_characters = 125000

per_page = 100

# GitHub lists at most this many pages of releases.
max_pages = 100

out_of_turn = "an answer arrived that does not belong to the step being taken"

# Every request is JSON under the API version that reports whether a release is immutable.
rest : GitHub.Call
rest = { method: "GET", path: "", version: "2026-03-10", accept: "application/vnd.github+json", jq: "", send: Nothing }

# == Inputs

checked_inputs : PublishRelease.Inputs -> Try({ tag : Str, prerelease : Bool }, Str)
checked_inputs = |inputs| {
	prerelease = checked_prerelease(inputs.prerelease, inputs.version)?
	tag = checked_tag(inputs.tag, inputs.version)?
	if !is_commit(inputs.target) {
		Err("target must be a full 40-character lowercase commit SHA")
	} else if inputs.notes_file == "" {
		Err("notes-file is required")
	} else if !inputs.workspace.starts_with("/") {
		Err("GITHUB_WORKSPACE must be the absolute path of the workspace")
	} else {
		Ok({ tag, prerelease })
	}
}

# Whether `version` has a prerelease suffix. A version is `x.y.z` or
# `x.y.z-suffix`: three numbers without leading zeros, and a suffix of letters
# and digits in parts joined by single dots.
is_prerelease_version : Str -> Try(Bool, Str)
is_prerelease_version = |version| {
	(core, suffix) = match Str.split_first(version, "-") {
		Ok(parts) => (parts.before, [parts.after])
		Err(_) => (version, [])
	}
	numbers = Str.split_on(core, ".")
	well_formed = numbers.len() == 3 and numbers.all(|number| GitHub.is_digits(number) and (number == "0" or !number.starts_with("0"))) and suffix.all(|text| Str.split_on(text, ".").all(|part| GitHub.is_plain(part, "")))
	if well_formed Ok(!suffix.is_empty()) else Err("version must be a release version such as 0.12.0 or 0.12.0-rc1")
}

checked_prerelease : Str, Str -> Try(Bool, Str)
checked_prerelease = |value, version|
	match (value, is_prerelease_version(version)) {
		("true", Ok(suffixed)) => if suffixed Ok(Bool.True) else Err("prerelease is true but ${version} has no prerelease suffix")
		("false", Ok(suffixed)) => if suffixed Err("prerelease is false but ${version} is a prerelease version") else Ok(Bool.False)
		("true", Err(reason)) => Err(reason)
		("false", Err(reason)) => Err(reason)
		_ => Err("prerelease must be exactly true or false")
	}

# The tag is the version, or the version behind a package name: 0.12.0 or kit-0.12.0.
checked_tag : Str, Str -> Try(Str, Str)
checked_tag = |tag, version| {
	suffix = "-${version}"
	name = tag.drop_suffix(suffix)
	named = tag.ends_with(suffix) and Str.split_on(Str.replace_each(Str.replace_each(name, ".", "-"), "_", "-"), "-").all(|part| GitHub.is_plain(part, ""))
	if tag == "" or tag == version {
		Ok(version)
	} else if named {
		Ok(tag)
	} else {
		Err("tag must be ${version} or <name>-${version}, where <name> is letters and digits joined by single '.', '_' or '-'")
	}
}

is_commit : Str -> Bool
is_commit = |target| is_hex(target, 40)

is_hex : Str, U64 -> Bool
is_hex = |text, length| {
	bytes = Str.to_utf8(text)
	bytes.len() == length and bytes.all(|byte| (byte >= '0' and byte <= '9') or (byte >= 'a' and byte <= 'f'))
}

checked_notes : Str, [Text(Str), NotText, SymLink, NotRegular, Absent] -> Try(Str, Str)
checked_notes = |path, notes|
	match notes {
		Absent => Err("notes-file does not exist: ${path}")
		SymLink => Err("notes-file is a symbolic link: ${path}")
		NotRegular => Err("notes-file is not a regular file: ${path}")
		NotText => Err("notes-file is not UTF-8 text: ${path}")
		Text(text) if text.trim() == "" => Err("notes-file is empty: ${path}")
		Text(text) if GitHub.count(text) > max_notes_characters => Err("notes-file is longer than the ${max_notes_characters.to_str()} characters a release body may hold")
		Text(text) => Ok(text)
	}

# == Assets

# `path` under the workspace, unless it is already absolute.
within : Str, Str -> Str
within = |workspace, path|
	if path.starts_with("/") {
		path
	} else if path == "" {
		without_trailing_slashes(workspace)
	} else {
		"${without_trailing_slashes(workspace)}/${path}"
	}

without_trailing_slashes : Str -> Str
without_trailing_slashes = |text| if text.ends_with("/") and text != "/" without_trailing_slashes(text.drop_suffix("/")) else text

file_name : Str -> Str
file_name = |path|
	match Str.split_last(path, "/") {
		Ok({ after, .. }) => after
		Err(_) => path
	}

# GitHub rewrites other characters in an asset name, which would publish a
# file under a name nobody tested.
asset_name : Str -> Bool
asset_name = |name| GitHub.is_plain(name, "._-") and !name.starts_with(".") and !name.starts_with("_") and !name.starts_with("-")

# The segments of one glob. A glob is a path relative to the workspace, in
# which `*` stands for any run of characters and `?` for any one character
# within a single segment. `.` segments and repeated slashes are ignored.
segments : Str -> Try(List(Str), Str)
segments = |pattern| {
	parts = Str.split_on(pattern, "/").keep_if(|part| part != "" and part != ".")
	if pattern.starts_with("/") {
		Err("assets glob must be relative to the workspace: ${pattern}")
	} else if parts.contains("..") {
		Err("assets glob must stay inside the workspace, without '..': ${pattern}")
	} else if pattern.contains("**") or pattern.contains("[") or pattern.contains("\\") {
		Err("assets glob may use only '*' and '?' within one path segment, not '**', '[' or a backslash: ${pattern}")
	} else if parts.is_empty() {
		Err("assets glob names no file: ${pattern}")
	} else {
		Ok(parts)
	}
}

# Whether the directory entry `name` matches one segment of a glob. A name
# that begins with a dot is matched only by a segment that begins with one.
matches : Str, Str -> Bool
matches = |segment, name| (segment.starts_with(".") or !name.starts_with(".")) and wild(Str.to_utf8(segment), Str.to_utf8(name))

wild : List(U8), List(U8) -> Bool
wild = |pattern, text|
	match (pattern, text) {
		([], []) => Bool.True
		([], _) => Bool.False
		([first, .. as others], _) if first == '*' => wild(others, text) or (!text.is_empty() and wild(pattern, text.drop_first(1)))
		([first, .. as others], [_, ..]) if first == '?' => wild(others, after_character(text))
		([first, .. as others], [byte, .. as later]) => first == byte and wild(others, later)
		_ => Bool.False
	}

# `text` without its first character, which is one byte and its continuation bytes.
after_character : List(U8) -> List(U8)
after_character = |text|
	match text.drop_first(1) {
		[byte, ..] if byte >= 128 and byte < 192 => after_character(text.drop_first(1))
		others => others
	}

# Expand one glob per line, a segment at a time. Every glob must match, and a
# file matched by two globs is one asset.
expand : PublishRelease.Gathering -> PublishRelease.Collecting
expand = |state|
	match state.matching {
		Idle =>
			match state.patterns {
				[] => inspect({ ..state, paths: GitHub.sort(GitHub.unique(state.matched)) })
				[pattern, .. as later] =>
					match segments(pattern) {
						Ok([segment, .. as others]) => expand({ ..state, patterns: later, matching: Matching({ pattern, segment, rest: others, directories: [""], hits: [] }) })
						Ok([]) => Rejected("assets glob names no file: ${pattern}")
						Err(reason) => Rejected(reason)
					}
			}
		Matching(now) =>
			match (now.directories, now.rest) {
				([directory, .. as others], _) => Look(ListDirectory(within(state.inputs.workspace, directory)), { ..state, waiting: ForNames(directory), matching: Matching({ ..now, directories: others }) })
				([], _) if now.hits.is_empty() => Rejected("assets glob matched no file: ${now.pattern}")
				([], []) => expand({ ..state, matching: Idle, matched: state.matched.concat(now.hits) })
				([], [segment, .. as others]) => expand({ ..state, matching: Matching({ ..now, segment, rest: others, directories: now.hits, hits: [] }) })
			}
	}

# Look at each matched path in order, then finish the plan.
inspect : PublishRelease.Gathering -> PublishRelease.Collecting
inspect = |state|
	match state.paths {
		[path, .. as later] => {
			absolute = within(state.inputs.workspace, path)
			Look(Inspect(absolute), { ..state, waiting: ForKind(absolute), paths: later })
		}
		[] if state.assets.is_empty() => Rejected("assets names no file")
		[] if !GitHub.valid_repository(state.inputs.repository) => Rejected("GITHUB_REPOSITORY must be an owner/name pair")
		[] if !GitHub.valid_token(state.inputs.token) => Rejected("token must not be empty or contain whitespace")
		[] =>
			Planned({
				version: state.inputs.version,
				tag: state.tag,
				prerelease: state.prerelease,
				target: state.inputs.target,
				notes: state.notes,
				assets: state.assets.sort_with(|left, right| by_name(left.name, right.name)),
				repository: state.inputs.repository,
			})
	}

by_name : Str, Str -> [Before, Same, After]
by_name = |left, right| if left == right Same else if GitHub.sort([left, right]) == [left, right] Before else After

# The digest in a line `sha256sum` printed. It marks a file name it had to
# escape with a leading backslash.
digest_of : Str -> Try(Str, [NoDigest])
digest_of = |printed|
	match Str.split_first(printed.drop_prefix("\\"), " ") {
		Ok({ before, .. }) if is_hex(before, 64) => Ok(before)
		_ => Err(NoDigest)
	}

# == Publication

described : PublishRelease.Draft -> Str
described = |draft| "the draft release ${draft.url} (id ${draft.id.to_str()})"

# The end of a run that created a draft and did not publish it.
unpublished : PublishRelease.Plan, PublishRelease.Draft, Str -> PublishRelease.Publishing
unpublished = |made, draft, reason|
	Stopped("${reason}. Nothing was published: ${described(draft)} is left unpublished and the tag ${made.tag} has not been created. Inspect the draft, delete it, then run this job again")

# Where the draft takes uploads: GitHub's URL template without its `{?name,label}`
# part, and empty unless it is a plain https URL.
upload_root : Str -> Str
upload_root = |template| {
	url = match Str.split_first(template, "{") {
		Ok({ before, .. }) => before
		Err(_) => template
	}
	if url.starts_with("https://") and GitHub.is_plain(url.drop_prefix("https://"), "./_-") url else ""
}

upload : PublishRelease.Draft, List(PublishRelease.Asset) -> PublishRelease.Publishing
upload = |draft, assets|
	match assets {
		[asset, .. as others] => Ask(Upload({ url: draft.upload, asset }), Uploading({ draft, asset, rest: others }))
		[] => list_assets(draft, 1, [])
	}

list_assets : PublishRelease.Draft, U64, List(PublishRelease.Record) -> PublishRelease.Publishing
list_assets = |draft, page, records| Ask(ListAssets({ release: draft.id, page }), Listing({ draft, page, records }))

# Read each asset back in name order. With none left, every check has passed.
download : PublishRelease.Draft, List(PublishRelease.Record) -> PublishRelease.Publishing
download = |draft, records|
	match records {
		[record, .. as others] => Ask(Download({ id: record.id, name: record.name, limit: record.size }), Downloading({ draft, record, rest: others }))
		[] => Ask(Publish(draft.id), AwaitPublish(draft))
	}

# The draft must hold exactly the planned names, each fully uploaded and of the tested size.
check_inventory : List(PublishRelease.Asset), List(PublishRelease.Record) -> Try({}, Str)
check_inventory = |assets, records| {
	wanted = assets.map(|asset| asset.name)
	held = records.map(|record| record.name)
	missing = GitHub.sort(wanted.keep_if(|name| !held.contains(name)))
	extra = GitHub.sort(GitHub.unique(held.keep_if(|name| !wanted.contains(name))))
	if !missing.is_empty() {
		Err("the draft is missing ${Str.join_with(missing, ", ")}")
	} else if !extra.is_empty() {
		Err("the draft holds ${Str.join_with(extra, ", ")}, which is not a tested file")
	} else if held.len() != wanted.len() {
		Err("the draft lists an asset name more than once")
	} else {
		match records.keep_errs(|record| as_tested(assets, record)) {
			[reason, ..] => Err(reason)
			[] => Ok({})
		}
	}
}

as_tested : List(PublishRelease.Asset), PublishRelease.Record -> Try({}, Str)
as_tested = |assets, record|
	if record.state != "uploaded" {
		Err("the upload of ${record.name} did not complete")
	} else if assets.any(|asset| asset.name == record.name and asset.size == record.size) {
		Ok({})
	} else {
		Err("${record.name} in the draft differs in size from the tested file")
	}

# Tests. `Entry` is a file on a pretend disk and `Hub` a pretend repository.
# `gather` and `release` run the same `collect`, `found`, `publish` and
# `answered` the script uses against them, recording every step in order.

Entry : { path : Str, kind : [File, SymLink, Directory], size : U64, digest : Str, text : Str }

file : Str, U64, Str -> Entry
file = |path, size, digit| { path: "/w/${path}", kind: File, size, digest: Str.repeat(digit, 64), text: "" }

bundle = "5Yr6CbZ1hPrVvXkEDkPjYkEkPQ9x.tar.zst"

notes_text = "# example 0.12.0\n\nStreaming decoders.\n"

candidate : List(Entry)
candidate = [
	file("candidate/dist/${bundle}", 12, "1"),
	file("candidate/dist/example-examples-0.12.0.zip", 14, "2"),
	file("candidate/.docs-out/example-0.12.0.pdf", 11, "3"),
	{ ..file("candidate/.release/release-notes.md", 38, "4"), text: notes_text },
]

names = ["5Yr6CbZ1hPrVvXkEDkPjYkEkPQ9x.tar.zst", "example-0.12.0.pdf", "example-examples-0.12.0.zip"]

target = Str.repeat("a", 40)

given : PublishRelease.Inputs
given = {
	version: "0.12.0",
	prerelease: "false",
	notes_file: "candidate/.release/release-notes.md",
	assets: "candidate/dist/*\ncandidate/.docs-out/*.pdf\n",
	tag: "",
	target,
	repository: "o/r",
	token: "secret-token",
	workspace: "/w",
}

# What is at `path`: the entry itself, or a directory that an entry is under.
at : List(Entry), Str -> [File(Entry), SymLink, Directory, Nothing]
at = |disk, path|
	match disk.keep_if(|entry| entry.path == path) {
		[entry, ..] =>
			match entry.kind {
				File => File(entry)
				SymLink => SymLink
				Directory => Directory
			}
		[] => if disk.any(|entry| entry.path.starts_with("${path}/")) Directory else Nothing
	}

look : List(Entry), PublishRelease.Local -> PublishRelease.Found
look = |disk, request|
	match request {
		ReadNotes(path) =>
			match at(disk, path) {
				File(entry) => Notes(Text(entry.text))
				SymLink => Notes(SymLink)
				Directory => Notes(NotRegular)
				Nothing => Notes(Absent)
			}
		ListDirectory(path) =>
			Names(
				GitHub.unique(
					disk.keep_if(|entry| entry.path.starts_with("${path}/")).map(
						|entry|
							match Str.split_first(entry.path.drop_prefix("${path}/"), "/") {
								Ok({ before, .. }) => before
								Err(_) => entry.path.drop_prefix("${path}/")
							},
					),
				),
			)
		Inspect(path) =>
			match at(disk, path) {
				File(entry) => Kind(File(entry.size))
				SymLink => Kind(SymLink)
				Directory => Kind(Other)
				Nothing => Kind(Absent)
			}
		Hash(path) =>
			match at(disk, path) {
				File(entry) => Digest("${entry.digest}  ${path}\n")
				_ => Unreadable("sha256sum could not read ${path}")
			}
	}

Gathered : { looked : List(PublishRelease.Local), outcome : Try(PublishRelease.Plan, Str) }

walk : List(Entry), List(PublishRelease.Local), PublishRelease.Collecting -> Gathered
walk = |disk, looked, next|
	match next {
		Look(request, state) => walk(disk, looked.append(request), PublishRelease.found(state, look(disk, request)))
		Planned(made) => { looked, outcome: Ok(made) }
		Rejected(reason) => { looked, outcome: Err(reason) }
	}

gather : PublishRelease.Inputs, List(Entry) -> Gathered
gather = |inputs, disk| walk(disk, [], PublishRelease.collect(inputs))

# Why the inputs were rejected, or "planned".
rejection : PublishRelease.Inputs, List(Entry) -> Str
rejection = |inputs, disk|
	match gather(inputs, disk).outcome {
		Err(reason) => reason
		Ok(_) => "planned"
	}

unplanned : PublishRelease.Plan
unplanned = { version: "", tag: "", prerelease: Bool.False, target: "", notes: "", assets: [], repository: "" }

planned : PublishRelease.Inputs -> PublishRelease.Plan
planned = |inputs| gather(inputs, candidate).outcome ?? unplanned

final : PublishRelease.Plan
final = planned(given)

rc : PublishRelease.Plan
rc = planned({ ..given, version: "0.12.0-rc1", prerelease: "true" })

# Inputs

# The plan names the tested files, their sizes and digests, and the notes as written.
expect final
	== {
		version: "0.12.0",
		tag: "0.12.0",
		prerelease: Bool.False,
		target,
		notes: notes_text,
		assets: [
			{ name: bundle, path: "/w/candidate/dist/${bundle}", size: 12, sha256: Str.repeat("1", 64) },
			{ name: "example-0.12.0.pdf", path: "/w/candidate/.docs-out/example-0.12.0.pdf", size: 11, sha256: Str.repeat("3", 64) },
			{ name: "example-examples-0.12.0.zip", path: "/w/candidate/dist/example-examples-0.12.0.zip", size: 14, sha256: Str.repeat("2", 64) },
		],
		repository: "o/r",
	}

# Malformed versions are rejected.
expect ["", "latest", "v0.12.0", "0.12", "0.12.0.1", "0.012.0", "0.12.0 ", " 0.12.0", "0.12.0\n0.13.0", "0.12.0-", "0.12.0-rc_1", "0.12.0+build", "0.12.0/x", "main", "0.12.0-rc-1", "0.12.0-rc..1"].all(|version| rejection({ ..given, version }, candidate) == "version must be a release version such as 0.12.0 or 0.12.0-rc1")
expect is_prerelease_version("0.12.0") == Ok(Bool.False) and is_prerelease_version("10.0.3") == Ok(Bool.False) and is_prerelease_version("0.12.0-rc1") == Ok(Bool.True) and is_prerelease_version("0.12.0-rc.2") == Ok(Bool.True)

# `prerelease` is exactly true or false.
expect ["", "True", "FALSE", "1", "0", "yes", "true ", " false", "true\n"].all(|prerelease| rejection({ ..given, prerelease }, candidate) == "prerelease must be exactly true or false")

# `prerelease` must agree with the version.
expect rejection({ ..given, version: "0.12.0-rc1" }, candidate) == "prerelease is false but 0.12.0-rc1 is a prerelease version"
expect rejection({ ..given, prerelease: "true" }, candidate) == "prerelease is true but 0.12.0 has no prerelease suffix"
expect rc.prerelease and rc.tag == "0.12.0-rc1" and planned({ ..given, version: "0.12.0-rc.2", prerelease: "true" }).prerelease

# The tag defaults to the version and may carry a package name.
expect final.tag == "0.12.0" and planned({ ..given, tag: "0.12.0" }).tag == "0.12.0" and planned({ ..given, tag: "kit-0.12.0" }).tag == "kit-0.12.0" and planned({ ..given, tag: "roc-kit.core-0.12.0" }).tag == "roc-kit.core-0.12.0"
expect planned({ ..given, version: "0.1.0-rc1", prerelease: "true", tag: "kit-0.1.0-rc1" }).tag == "kit-0.1.0-rc1"

# A tag that is unsafe in a ref, or names another version, is rejected.
expect
	[
		"v0.12.0",
		"kit-0.13.0",
		"kit-0.12.0-rc1",
		"kit",
		"-0.12.0",
		"kit--0.12.0",
		"kit/0.12.0",
		"refs/tags/kit-0.12.0",
		"kit..x-0.12.0",
		".kit-0.12.0",
		"kit.-0.12.0",
		"kit 0.12.0",
		"kit-0.12.0 ",
		"kit-0.12.0\n",
		"kit~1-0.12.0",
		"kit:x-0.12.0",
		"kit?-0.12.0",
		"../kit-0.12.0",
		"kit@{0}-0.12.0",
		"0.12.0-0.12.0x",
	].all(|tag| rejection({ ..given, tag }, candidate) == "tag must be 0.12.0 or <name>-0.12.0, where <name> is letters and digits joined by single '.', '_' or '-'")

# The target is a full commit SHA.
expect ["", "main", "refs/heads/main", Str.repeat("a", 39), Str.repeat("a", 41), Str.repeat("A", 40), Str.repeat("g", 40), "${target}\n", "HEAD", "aaaaaaa"].all(|sha| rejection({ ..given, target: sha }, candidate) == "target must be a full 40-character lowercase commit SHA")

# The notes file is required, and is authored text in a regular file.
expect rejection({ ..given, notes_file: "" }, candidate) == "notes-file is required"
expect rejection({ ..given, notes_file: "candidate/.release/missing.md" }, candidate) == "notes-file does not exist: /w/candidate/.release/missing.md"
expect rejection({ ..given, notes_file: "candidate/.release" }, candidate) == "notes-file is not a regular file: /w/candidate/.release"
expect rejection({ ..given, notes_file: "blank.md" }, candidate.append({ ..file("blank.md", 2, "5"), text: " \n" })) == "notes-file is empty: /w/blank.md"
expect rejection({ ..given, notes_file: "linked.md" }, candidate.append({ ..file("linked.md", 38, "5"), kind: SymLink })) == "notes-file is a symbolic link: /w/linked.md"
expect rejection({ ..given, notes_file: "long.md" }, candidate.append({ ..file("long.md", 125001, "5"), text: Str.repeat("x", 125001) })) == "notes-file is longer than the 125000 characters a release body may hold"
expect checked_notes("/w/n.md", NotText) == Err("notes-file is not UTF-8 text: /w/n.md") and checked_notes("/w/n.md", Text(Str.repeat("é", 125000))) == Ok(Str.repeat("é", 125000))
expect gather({ ..given, notes_file: "/elsewhere/notes.md" }, candidate).looked == [ReadNotes("/elsewhere/notes.md")]

# The workspace, the repository and the token are checked before any request.
expect rejection({ ..given, workspace: "" }, candidate) == "GITHUB_WORKSPACE must be the absolute path of the workspace" and rejection({ ..given, workspace: "relative" }, candidate) == "GITHUB_WORKSPACE must be the absolute path of the workspace"
expect ["o/r/x", "../r", "o", ""].all(|repository| rejection({ ..given, repository }, candidate) == "GITHUB_REPOSITORY must be an owner/name pair")
expect ["", "two words", "line\nbreak"].all(|token| rejection({ ..given, token }, candidate) == "token must not be empty or contain whitespace")
# The plan does not hold the token, so no request or message built from it can.
expect !Str.inspect(final).contains("secret-token")

# Assets

# The files are looked at in a fixed order: the notes, each directory a glob walks, then each match and its digest.
expect gather(given, candidate).looked
	== [
		ReadNotes("/w/candidate/.release/release-notes.md"),
		ListDirectory("/w"),
		ListDirectory("/w/candidate"),
		ListDirectory("/w/candidate/dist"),
		ListDirectory("/w"),
		ListDirectory("/w/candidate"),
		ListDirectory("/w/candidate/.docs-out"),
		Inspect("/w/candidate/.docs-out/example-0.12.0.pdf"),
		Hash("/w/candidate/.docs-out/example-0.12.0.pdf"),
		Inspect("/w/candidate/dist/${bundle}"),
		Hash("/w/candidate/dist/${bundle}"),
		Inspect("/w/candidate/dist/example-examples-0.12.0.zip"),
		Hash("/w/candidate/dist/example-examples-0.12.0.zip"),
	]

# An unmatched glob is an error even when the others match.
expect rejection({ ..given, assets: "${given.assets}candidate/dist/*.sbom\n" }, candidate) == "assets glob matched no file: candidate/dist/*.sbom"
expect rejection({ ..given, assets: "candidate/none/*" }, candidate) == "assets glob matched no file: candidate/none/*"

# No assets is an error.
expect ["", "\n\n", "   \n"].all(|assets| rejection({ ..given, assets }, candidate) == "assets names no file")

# Two assets with one file name are rejected.
expect rejection({ ..given, assets: "${given.assets}candidate/other/*\n" }, candidate.append(file("candidate/other/${bundle}", 18, "6"))) == "two assets share the name ${bundle}: /w/candidate/dist/${bundle} and /w/candidate/other/${bundle}"

# One file matched by two globs is one asset.
expect planned({ ..given, assets: "${given.assets}candidate/dist/*.zip\n./candidate//dist/./*.zip\r\n  candidate/dist/example-examples-0.12.0.zip  \n" }) == final

# A symbolic link is rejected, whether or not it leads anywhere.
expect rejection(given, candidate.append({ ..file("candidate/dist/linked.zip", 14, "2"), kind: SymLink })) == "asset is a symbolic link: /w/candidate/dist/linked.zip"

# A directory matched by a glob is rejected.
expect rejection(given, candidate.append({ ..file("candidate/dist/nested", 0, "0"), kind: Directory })) == "asset is not a regular file: /w/candidate/dist/nested"
expect rejection({ ..given, assets: "candidate/*" }, candidate) == "asset is not a regular file: /w/candidate/dist"

# An empty asset is rejected.
expect rejection(given, candidate.append(file("candidate/dist/empty.zip", 0, "7"))) == "asset is empty: /w/candidate/dist/empty.zip"

# Names GitHub would rewrite are rejected.
expect ["has space.zip", "plus+plus.zip", "tilde~.zip", "-leading.zip", "café.zip", "_leading.zip"].all(|name| rejection(given, candidate.append(file("candidate/dist/${name}", 1, "8"))) == "asset name must use only letters, digits, '.', '_' and '-': ${name}")
expect asset_name(bundle) and asset_name("a_b-c.1") and !asset_name(".hidden") and !asset_name("")

# A digest that `sha256sum` did not print is an error, not an empty digest.
expect digest_of("${Str.repeat("1", 64)}  /w/a.zip\n") == Ok(Str.repeat("1", 64)) and digest_of("\\${Str.repeat("1", 64)}  /w/a\\nb.zip\n") == Ok(Str.repeat("1", 64))
expect digest_of("") == Err(NoDigest) and digest_of("sha256sum: /w/a.zip: No such file\n") == Err(NoDigest) and digest_of("${Str.repeat("1", 63)}  /w/a.zip\n") == Err(NoDigest) and digest_of("${Str.repeat("G", 64)}  x\n") == Err(NoDigest)

# A glob is a relative path inside the workspace, with `*` and `?` in one segment.
expect segments("candidate/dist/*") == Ok(["candidate", "dist", "*"]) and segments("./a//b/./c?.zip") == Ok(["a", "b", "c?.zip"])
expect rejection({ ..given, assets: "/etc/passwd" }, candidate) == "assets glob must be relative to the workspace: /etc/passwd"
expect rejection({ ..given, assets: "candidate/../../secret" }, candidate) == "assets glob must stay inside the workspace, without '..': candidate/../../secret"
expect ["candidate/**/*.zip", "candidate/dist/[ab]*", "candidate/dist/a\\*"].all(|pattern| rejection({ ..given, assets: pattern }, candidate).starts_with("assets glob may use only '*' and '?' within one path segment"))
expect rejection({ ..given, assets: "./" }, candidate) == "assets glob names no file: ./"
expect matches("*", "a.zip") and matches("*.zip", "a.zip") and matches("a*", "a") and matches("*a*", "bab") and matches("a?c", "abc") and matches("a?c", "aéc") and matches("*.tar.zst", bundle) and matches("a.zip", "a.zip")
expect !matches("*.zip", "a.pdf") and !matches("a?c", "ac") and !matches("a?c", "abbc") and !matches("a", "ab") and !matches("ab", "a") and !matches("?", "") and !matches("A.zip", "a.zip")
# A name that begins with a dot is matched only by a segment that begins with one.
expect !matches("*", ".hidden") and !matches("?hidden", ".hidden") and matches(".*", ".hidden") and matches(".docs-out", ".docs-out")
expect planned({ ..given, assets: "candidate/*/example-*" }).assets.map(|asset| asset.name) == ["example-examples-0.12.0.zip"]
expect planned({ ..given, assets: "candidate/.*/*.pdf\ncandidate/dist/?Yr*\n" }).assets.map(|asset| asset.name) == [bundle, "example-0.12.0.pdf"]

# The digest listing is in the format of `sha256sum`, in name order.
expect PublishRelease.digest_listing(final) == "${Str.repeat("1", 64)}  ${bundle}\n${Str.repeat("3", 64)}  example-0.12.0.pdf\n${Str.repeat("2", 64)}  example-examples-0.12.0.zip\n"
expect PublishRelease.summary(final, "https://github.com/o/r/releases/tag/0.12.0") == "### Release 0.12.0\n\nhttps://github.com/o/r/releases/tag/0.12.0\n\nSHA-256 of each published asset, verified against the tested files:\n\n```\n${PublishRelease.digest_listing(final)}```\n"
expect PublishRelease.summary(rc, "u").starts_with("### Prerelease 0.12.0-rc1\n\nu\n")

# Publication

Hub : {
	commits : List(Str),
	tags : List(Str),
	releases : List(PublishRelease.Listed),
	stored : List(PublishRelease.Record),
	published : Bool,
	immutable : Bool,
	asked : List(PublishRelease.Remote),
	fault : [Sound, Corrupt(Str), Drop(Str), Extra(Str), Resize(Str, U64), Grow(Str), Rename, NoUploadUrl, NotADraft, FailCreate, FailUpload, FailList, FailPublish, LostPublish, IgnoredPublish, FailReadBack, OtherTag],
}

hub : Hub
hub = { commits: [target], tags: [], releases: [], stored: [], published: Bool.False, immutable: Bool.True, asked: [], fault: Sound }

draft_url = "https://github.com/o/r/releases/tag/untagged-1f2e"

uploads = "https://uploads.github.com/repos/o/r/releases/7/assets"

respond : PublishRelease.Plan, Hub, PublishRelease.Remote -> (Hub, PublishRelease.Reply)
respond = |made, before, request| {
	now = { ..before, asked: before.asked.append(request) }
	text = |flag| if flag "true" else "false"
	match request {
		CommitExists(sha) => (now, Exists(now.commits.contains(sha)))
		TagExists(tag) => (now, Exists(now.tags.contains(tag)))
		ListReleases(page) => (now, Releases(now.releases.sublist({ start: (page - 1) * 100, len: 100 })))
		CreateDraft =>
			match now.fault {
				FailCreate => (now, Failed("POST repos/o/r/releases failed: no answer within 120 seconds"))
				_ => (now, Created({ id: 7, tag_name: made.tag, draft: text(now.fault != NotADraft), html_url: draft_url, upload_url: if now.fault == NoUploadUrl "http://uploads.example/x{?name}" else "${uploads}{?name,label}" }))
			}
		Upload({ asset, .. }) => {
			size = match now.fault {
				Resize(name, other) if name == asset.name => other
				_ => asset.size
			}
			kept = { ..now, stored: now.stored.append({ id: 100 + now.stored.len(), name: asset.name, size, state: "uploaded" }) }
			match now.fault {
				FailUpload => (now, Failed("GitHub answered POST ${uploads} with HTTP 502: Bad Gateway"))
				Rename => (kept, Uploaded("renamed"))
				_ => (kept, Uploaded(asset.name))
			}
		}
		ListAssets({ page, .. }) => {
			held = match now.fault {
				Drop(name) => now.stored.keep_if(|record| record.name != name)
				Extra(name) => now.stored.append({ id: 999, name, size: 1, state: "uploaded" })
				_ => now.stored
			}
			if now.fault == FailList (now, Failed("GitHub answered GET repos/o/r/releases/7/assets with HTTP 500: Server Error")) else (now, Assets(held.sublist({ start: (page - 1) * 100, len: 100 })))
		}
		Download({ name, limit, .. }) => {
			sha256 = made.assets.keep_if(|asset| asset.name == name).map(|asset| asset.sha256).first() ?? ""
			match now.fault {
				Corrupt(other) if other == name => (now, Downloaded({ size: limit, digest: "${Str.repeat("f", 64)}  /d/${name}\n" }))
				Grow(other) if other == name => (now, Downloaded({ size: limit + 1, digest: "${sha256}  /d/${name}\n" }))
				_ => (now, Downloaded({ size: limit, digest: "${sha256}  /d/${name}\n" }))
			}
		}
		Publish(_) =>
			match now.fault {
				FailPublish => (now, Failed("GitHub answered PATCH repos/o/r/releases/7 with HTTP 422: Validation Failed"))
				LostPublish => ({ ..now, published: Bool.True, tags: now.tags.append(made.tag) }, Failed("PATCH repos/o/r/releases/7 failed: no answer within 120 seconds"))
				IgnoredPublish => (now, Accepted)
				_ => ({ ..now, published: Bool.True, tags: now.tags.append(made.tag) }, Accepted)
			}
		ReadBack(_) =>
			match now.fault {
				FailReadBack => (now, Failed("GET repos/o/r/releases/7 failed: no answer within 120 seconds"))
				_ =>
					(
						now,
						Release({
							tag_name: if now.fault == OtherTag "9.9.9" else made.tag,
							draft: text(!now.published),
							prerelease: text(made.prerelease),
							immutable: text(now.published and now.immutable),
							html_url: if now.published "https://github.com/o/r/releases/tag/${made.tag}" else draft_url,
						}),
					)
			}
	}
}

Released : { after : Hub, outcome : Try(Str, Str) }

carry : PublishRelease.Plan, Hub, PublishRelease.Publishing -> Released
carry = |made, before, next|
	match next {
		Ask(request, stage) => {
			(now, answer) = respond(made, before, request)
			carry(made, now, PublishRelease.answered(made, stage, answer))
		}
		Published(url) => { after: before, outcome: Ok(url) }
		Stopped(reason) => { after: before, outcome: Err(reason) }
	}

release : PublishRelease.Plan, Hub -> Released
release = |made, before| carry(made, before, PublishRelease.publish(made))

# The kind of each request, in order.
kinds : Hub -> List(Str)
kinds = |now|
	now.asked.map(
		|request|
			match request {
				CommitExists(_) => "commit"
				TagExists(_) => "tag"
				ListReleases(_) => "releases"
				CreateDraft => "create"
				Upload(_) => "upload"
				ListAssets(_) => "assets"
				Download(_) => "download"
				Publish(_) => "publish"
				ReadBack(_) => "read back"
			},
	)

reason : Released -> Str
reason = |ran|
	match ran.outcome {
		Err(text) => text
		Ok(_) => "published"
	}

# Whether a run ended with a draft and nothing public, and said so.
left_as_draft : Released -> Bool
left_as_draft = |ran|
	[
		!kinds(ran.after).contains("publish"),
		!ran.after.published,
		!ran.after.tags.contains("0.12.0"),
		reason(ran).ends_with(". Nothing was published: the draft release ${draft_url} (id 7) is left unpublished and the tag 0.12.0 has not been created. Inspect the draft, delete it, then run this job again"),
	].all(|held| held)

asset : U64 -> PublishRelease.Asset
asset = |index| final.assets.get(index) ?? { name: "", path: "", size: 0, sha256: "" }

# A successful run checks, stages, verifies, publishes and reads back, in that order.
expect release(final, hub).outcome == Ok("https://github.com/o/r/releases/tag/0.12.0")
expect release(final, hub).after.asked == [
	CommitExists(target),
	TagExists("0.12.0"),
	ListReleases(1),
	CreateDraft,
	Upload({ url: uploads, asset: asset(0) }),
	Upload({ url: uploads, asset: asset(1) }),
	Upload({ url: uploads, asset: asset(2) }),
	ListAssets({ release: 7, page: 1 }),
	Download({ id: 100, name: bundle, limit: 12 }),
	Download({ id: 101, name: "example-0.12.0.pdf", limit: 11 }),
	Download({ id: 102, name: "example-examples-0.12.0.zip", limit: 14 }),
	Publish(7),
	ReadBack(7),
]

# A release candidate goes through the same steps under its own tag.
expect release(rc, hub).outcome == Ok("https://github.com/o/r/releases/tag/0.12.0-rc1") and release(rc, hub).after.asked.contains(TagExists("0.12.0-rc1"))

# A prefixed tag is the one checked and created, whatever other tags exist.
expect {
	ran = release(planned({ ..given, tag: "kit-0.12.0" }), { ..hub, tags: ["0.12.0"] })
	ran.outcome == Ok("https://github.com/o/r/releases/tag/kit-0.12.0") and ran.after.asked.contains(TagExists("kit-0.12.0")) and !ran.after.asked.contains(TagExists("0.12.0"))
}

# A target outside the repository stops before anything is created.
expect {
	ran = release(planned({ ..given, target: Str.repeat("b", 40) }), hub)
	reason(ran) == "target ${Str.repeat("b", 40)} is not a commit in this repository" and kinds(ran.after) == ["commit"]
}

# An existing tag stops before anything is created.
expect {
	ran = release(final, { ..hub, tags: ["0.12.0"] })
	reason(ran) == "the tag 0.12.0 already exists; a version is never published twice" and kinds(ran.after) == ["commit", "tag"]
}

# An existing release stops before anything is created.
expect {
	ran = release(final, { ..hub, releases: [{ id: 3, tag_name: "0.12.0", draft: "false", html_url: "https://github.com/o/r/releases/tag/0.12.0" }] })
	reason(ran) == "a release for 0.12.0 already exists: https://github.com/o/r/releases/tag/0.12.0" and kinds(ran.after) == ["commit", "tag", "releases"]
}

# An existing draft is never adopted: it stops the run before anything is created or uploaded.
expect {
	ran = release(final, { ..hub, releases: [{ id: 3, tag_name: "0.12.0", draft: "true", html_url: "https://github.com/o/r/releases/tag/untagged-9" }] })
	reason(ran) == "a draft release for 0.12.0 already exists: https://github.com/o/r/releases/tag/untagged-9" and kinds(ran.after) == ["commit", "tag", "releases"]
}

# A draft is found on any page of the release list, and other versions are not mistaken for it.
expect {
	others = List.repeat({ id: 1, tag_name: "0.11.0", draft: "false", html_url: "u" }, 100)
	ran = release(final, { ..hub, releases: others.concat([{ id: 2, tag_name: "0.12.0-rc1", draft: "false", html_url: "u" }, { id: 3, tag_name: "0.12.0", draft: "null", html_url: "https://github.com/o/r/releases/tag/untagged-9" }]) })
	reason(ran) == "a draft release for 0.12.0 already exists: https://github.com/o/r/releases/tag/untagged-9" and ran.after.asked == [CommitExists(target), TagExists("0.12.0"), ListReleases(1), ListReleases(2)]
}
expect kinds(release(final, { ..hub, releases: List.repeat({ id: 1, tag_name: "0.11.0", draft: "false", html_url: "u" }, 100) }).after).take_first(5) == ["commit", "tag", "releases", "releases", "create"]

# A failed read-only check stops before anything is created.
expect PublishRelease.answered(final, AwaitCommit, Failed("x")) == Stopped("x") and PublishRelease.answered(final, AwaitTag, Failed("x")) == Stopped("x") and PublishRelease.answered(final, AwaitReleases(1), Failed("x")) == Stopped("x")

# A creation whose answer is lost may have left a draft, and the error says to look.
expect {
	ran = release(final, { ..hub, fault: FailCreate })
	reason(ran) == "POST repos/o/r/releases failed: no answer within 120 seconds. If the releases page now shows a draft for 0.12.0, delete it before running this job again" and kinds(ran.after) == ["commit", "tag", "releases", "create"]
}

# What GitHub created must be the requested draft.
expect left_as_draft(release(final, { ..hub, fault: NotADraft })) and reason(release(final, { ..hub, fault: NotADraft })).starts_with("GitHub did not create the requested draft. ")
expect PublishRelease.answered(final, AwaitDraft, Created({ id: 7, tag_name: "0.12.1", draft: "true", html_url: draft_url, upload_url: "${uploads}{?name,label}" })) == unpublished(final, { id: 7, url: draft_url, upload: uploads }, "GitHub did not create the requested draft")

# Nothing is uploaded to an upload URL that is not https.
expect {
	ran = release(final, { ..hub, fault: NoUploadUrl })
	left_as_draft(ran) and reason(ran).starts_with("the draft has no https upload URL. ") and !kinds(ran.after).contains("upload")
}
expect upload_root("${uploads}{?name,label}") == uploads and upload_root("http://uploads.example/x{?name}") == "" and upload_root("") == "" and upload_root("https://uploads.github.com/x?y=1{?name}") == ""

# A failed upload leaves the draft unpublished.
expect {
	ran = release(final, { ..hub, fault: FailUpload })
	left_as_draft(ran) and reason(ran).starts_with("GitHub answered POST ${uploads} with HTTP 502: Bad Gateway. ") and kinds(ran.after) == ["commit", "tag", "releases", "create", "upload"]
}

# An upload GitHub stored under another name leaves the draft unpublished.
expect left_as_draft(release(final, { ..hub, fault: Rename })) and reason(release(final, { ..hub, fault: Rename })).starts_with("GitHub stored ${bundle} as renamed. ")

# An asset missing from the draft leaves it unpublished, and nothing is downloaded.
expect {
	ran = release(final, { ..hub, fault: Drop("example-0.12.0.pdf") })
	left_as_draft(ran) and reason(ran).starts_with("the draft is missing example-0.12.0.pdf. ") and !kinds(ran.after).contains("download")
}

# An extra asset in the draft leaves it unpublished.
expect left_as_draft(release(final, { ..hub, fault: Extra("injected.zip") })) and reason(release(final, { ..hub, fault: Extra("injected.zip") })).starts_with("the draft holds injected.zip, which is not a tested file. ")

# A truncated or a padded upload is a mismatch.
expect [11, 13].all(|size| left_as_draft(release(final, { ..hub, fault: Resize(bundle, size) })) and reason(release(final, { ..hub, fault: Resize(bundle, size) })).starts_with("${bundle} in the draft differs in size from the tested file. "))

# An inventory that is incomplete or repeats a name is rejected.
expect {
	records = final.assets.map_with_index(|item, index| { id: index, name: item.name, size: item.size, state: "uploaded" })
	[
		check_inventory(final.assets, records) == Ok({}),
		check_inventory(final.assets, records.concat(records.take_first(1))) == Err("the draft lists an asset name more than once"),
		check_inventory(final.assets, records.map(|record| if record.id == 0 { ..record, state: "starter" } else record)) == Err("the upload of ${bundle} did not complete"),
		check_inventory(final.assets, []) == Err("the draft is missing ${Str.join_with(names, ", ")}"),
	].all(|held| held)
}
expect left_as_draft(release(final, { ..hub, fault: FailList })) and reason(release(final, { ..hub, fault: FailList })).starts_with("GitHub answered GET repos/o/r/releases/7/assets with HTTP 500: Server Error. ")

# A digest mismatch leaves the draft unpublished.
expect {
	ran = release(final, { ..hub, fault: Corrupt(bundle) })
	left_as_draft(ran) and reason(ran).starts_with("${bundle} downloaded from the draft differs from the tested file. ") and kinds(ran.after).count_if(|kind| kind == "download") == 1
}

# A download longer than its listed size is a mismatch, and is cut off one byte past it.
expect left_as_draft(release(final, { ..hub, fault: Grow(bundle) })) and reason(release(final, { ..hub, fault: Grow(bundle) })).starts_with("${bundle} downloaded from the draft differs from the tested file. ")
expect PublishRelease.budget(Download({ id: 100, name: bundle, limit: 12 })) == { timeout_ms: 600_000, capture: 13 }
expect PublishRelease.budget(Upload({ url: uploads, asset: asset(0) })).timeout_ms == 1_800_000 and PublishRelease.budget(Publish(7)) == { timeout_ms: 120_000, capture: 16_777_216 }

# A download that could not be read back, or whose digest was not printed, is a mismatch too.
expect {
	stage = Downloading({ draft: { id: 7, url: draft_url, upload: uploads }, record: { id: 100, name: bundle, size: 12, state: "uploaded" }, rest: [] })
	[
		PublishRelease.answered(final, stage, Downloaded({ size: 12, digest: "" })) == unpublished(final, { id: 7, url: draft_url, upload: uploads }, "${bundle} downloaded from the draft differs from the tested file"),
		PublishRelease.answered(final, stage, Failed("the download failed")) == unpublished(final, { id: 7, url: draft_url, upload: uploads }, "the download failed"),
		PublishRelease.answered(final, stage, Downloaded({ size: 12, digest: "${Str.repeat("1", 64)}  /d/x\n" })) == Ask(Publish(7), AwaitPublish({ id: 7, url: draft_url, upload: uploads })),
	].all(|held| held)
}

# A rejected publish request reports the verified draft.
expect {
	ran = release(final, { ..hub, fault: FailPublish })
	[
		reason(ran) == "GitHub answered PATCH repos/o/r/releases/7 with HTTP 422: Validation Failed. Nothing was published: the draft release ${draft_url} (id 7) is verified but left unpublished and the tag 0.12.0 has not been created. Delete the draft, then run this job again",
		!ran.after.published,
		kinds(ran.after).take_last(2) == ["publish", "read back"],
	].all(|held| held)
}
expect reason(release(final, { ..hub, fault: IgnoredPublish })).starts_with("GitHub accepted the publish request without publishing. Nothing was published: ")

# A publish request whose answer was lost is recognised as published.
expect release(final, { ..hub, fault: LostPublish }).outcome == Ok("https://github.com/o/r/releases/tag/0.12.0")

# A state that cannot be read after the publish request is not reported as unpublished.
expect {
	text = reason(release(final, { ..hub, fault: FailReadBack }))
	[
		text == "GET repos/o/r/releases/7 failed: no answer within 120 seconds. The release could not be read after the publish request, so the draft release ${draft_url} (id 7) may or may not be published. Look at the releases page: if 0.12.0 is published, do not run this job again",
		!text.contains("Nothing was published"),
	].all(|held| held)
}

# A release that is not immutable is reported after publication.
expect {
	text = reason(release(final, { ..hub, immutable: Bool.False }))
	[
		text == "https://github.com/o/r/releases/tag/0.12.0 is published with the verified assets, but it is not immutable. Enable immutable releases in the repository settings; do not run this job again",
		!text.contains("Nothing was published"),
	].all(|held| held)
}

# A release published under another tag, or as the wrong kind, is reported as published.
expect reason(release(final, { ..hub, fault: OtherTag })) == "https://github.com/o/r/releases/tag/0.12.0 is published, but not as the release 0.12.0; do not run this job again"
expect PublishRelease.answered(rc, AwaitReadBack({ draft: { id: 7, url: draft_url, upload: uploads }, failure: "" }), Release({ tag_name: "0.12.0-rc1", draft: "false", prerelease: "false", immutable: "true", html_url: "u" })) == Stopped("u is published, but not as the prerelease 0.12.0-rc1; do not run this job again")

# Requests

json = "application/vnd.github+json"

get : Str, Str -> GitHub.Call
get = |path, jq| { method: "GET", path, version: "2026-03-10", accept: json, jq, send: Nothing }

# The tag lookup is exact, and the commit lookup names the full SHA.
expect PublishRelease.call(final, TagExists("kit-0.1.0")) == get("repos/o/r/git/ref/tags/kit-0.1.0", "{ref}")
expect PublishRelease.call(final, CommitExists(target)) == get("repos/o/r/git/commits/${target}", "{sha}")
expect PublishRelease.call(final, ListReleases(2)).path == "repos/o/r/releases?per_page=100&page=2" and PublishRelease.call(final, ListAssets({ release: 7, page: 3 })).path == "repos/o/r/releases/7/assets?per_page=100&page=3"

# The draft is created at the target with the authored notes, and is never the latest release.
expect PublishRelease.call(rc, CreateDraft).send == Json("{\"body\":\"# example 0.12.0\\n\\nStreaming decoders.\\n\",\"draft\":true,\"make_latest\":\"false\",\"name\":\"0.12.0-rc1\",\"prerelease\":true,\"tag_name\":\"0.12.0-rc1\",\"target_commitish\":\"${target}\"}")
expect PublishRelease.call(rc, CreateDraft).method == "POST" and PublishRelease.call(rc, CreateDraft).path == "repos/o/r/releases"
expect PublishRelease.call(final, CreateDraft).send == Json("{\"body\":\"# example 0.12.0\\n\\nStreaming decoders.\\n\",\"draft\":true,\"make_latest\":\"false\",\"name\":\"0.12.0\",\"prerelease\":false,\"tag_name\":\"0.12.0\",\"target_commitish\":\"${target}\"}")

# Publishing sets prerelease and latest explicitly: a prerelease is never the latest release.
expect PublishRelease.call(rc, Publish(7)) == { method: "PATCH", path: "repos/o/r/releases/7", version: "2026-03-10", accept: json, jq: "{id}", send: Json("{\"draft\":false,\"make_latest\":\"false\",\"prerelease\":true}") }
expect PublishRelease.call(final, Publish(7)).send == Json("{\"draft\":false,\"make_latest\":\"true\",\"prerelease\":false}")

# An upload goes to the draft's own upload URL, under the tested name, from the tested file.
expect PublishRelease.call(final, Upload({ url: uploads, asset: asset(0) })) == { method: "POST", path: "${uploads}?name=${bundle}", version: "2026-03-10", accept: json, jq: "{name: (.name // \"\")}", send: File("/w/candidate/dist/${bundle}") }

# A download asks for the bytes of one asset by its identifier.
expect PublishRelease.call(final, Download({ id: 101, name: "a.zip", limit: 5 })) == { method: "GET", path: "repos/o/r/releases/assets/101", version: "2026-03-10", accept: "application/octet-stream", jq: "", send: Nothing }

# Only three requests can change the repository, and the token is in none of them.
expect [CommitExists(target), TagExists("0.12.0"), ListReleases(1), ListAssets({ release: 7, page: 1 }), Download({ id: 100, name: bundle, limit: 12 }), ReadBack(7)].all(|request| PublishRelease.call(final, request).method == "GET" and PublishRelease.call(final, request).send == Nothing)
expect [CreateDraft, Upload({ url: uploads, asset: asset(0) }), Publish(7), ReadBack(7), ListReleases(1)].all(|request| !Str.inspect(GitHub.args(PublishRelease.call(final, request))).contains("secret-token") and !Str.inspect(PublishRelease.call(final, request).send).contains("secret-token"))

# Answers

printed : Str -> GitHub.Output
printed = |stdout| { succeeded: Bool.True, stdout, stderr: "" }

answering : Str -> GitHub.Output
answering = |status| { succeeded: Bool.False, stdout: "{\"message\":\"Validation Failed\",\"status\":\"${status}\"}", stderr: "gh: Validation Failed (HTTP ${status})" }

# A lookup tells an absent tag or commit from a failed request.
expect PublishRelease.reply(final, TagExists("kit-0.1.0"), printed("{\"ref\":\"refs/tags/kit-0.1.0\"}")) == Exists(Bool.True)
expect PublishRelease.reply(final, TagExists("kit-0.1.0"), answering("404")) == Exists(Bool.False) and PublishRelease.reply(final, CommitExists(target), answering("404")) == Exists(Bool.False)
expect PublishRelease.reply(final, TagExists("kit-0.1.0"), answering("403")) == Failed("GitHub answered GET repos/o/r/git/ref/tags/kit-0.1.0 with HTTP 403: Validation Failed")
# Anything else that is absent is a failure.
expect PublishRelease.reply(final, ReadBack(7), answering("404")) == Failed("GitHub answered GET repos/o/r/releases/7 with HTTP 404") and PublishRelease.reply(final, ListReleases(1), answering("404")) == Failed("GitHub answered GET repos/o/r/releases with HTTP 404")

expect PublishRelease.reply(final, ListReleases(1), printed("[{\"draft\":\"true\",\"html_url\":\"https://github.com/o/r/releases/tag/untagged-9\",\"id\":3,\"tag_name\":\"0.12.0\"}]")) == Releases([{ id: 3, tag_name: "0.12.0", draft: "true", html_url: "https://github.com/o/r/releases/tag/untagged-9" }])
expect PublishRelease.reply(final, CreateDraft, printed("{\"draft\":\"true\",\"html_url\":\"${draft_url}\",\"id\":7,\"tag_name\":\"0.12.0\",\"upload_url\":\"${uploads}{?name,label}\"}")) == Created({ id: 7, tag_name: "0.12.0", draft: "true", html_url: draft_url, upload_url: "${uploads}{?name,label}" })
expect PublishRelease.reply(final, Upload({ url: uploads, asset: asset(0) }), printed("{\"name\":\"${bundle}\"}")) == Uploaded(bundle)
expect PublishRelease.reply(final, ListAssets({ release: 7, page: 1 }), printed("[{\"id\":100,\"name\":\"a.zip\",\"size\":5,\"state\":\"uploaded\"}]")) == Assets([{ id: 100, name: "a.zip", size: 5, state: "uploaded" }])
expect PublishRelease.reply(final, Publish(7), printed("{\"id\":7}")) == Accepted and PublishRelease.reply(final, Publish(7), answering("422")) == Failed("GitHub answered PATCH repos/o/r/releases/7 with HTTP 422: Validation Failed")
expect PublishRelease.reply(final, ReadBack(7), printed("{\"draft\":\"false\",\"html_url\":\"u\",\"immutable\":\"true\",\"prerelease\":\"false\",\"tag_name\":\"0.12.0\"}")) == Release({ tag_name: "0.12.0", draft: "false", prerelease: "false", immutable: "true", html_url: "u" })
# An answer that is not in the declared shape is a failure, never a default.
expect [ListReleases(1), CreateDraft, Upload({ url: uploads, asset: asset(0) }), ListAssets({ release: 7, page: 1 }), ReadBack(7)].all(|request| ["", "{}", "[{}]", "{\"message\":\"x\"}", "<html>"].all(|stdout| PublishRelease.reply(final, request, printed(stdout)) == Failed(GitHub.unexpected(PublishRelease.call(final, request)))))
# The bytes of a download are never read as an answer.
expect PublishRelease.reply(final, Download({ id: 100, name: bundle, limit: 12 }), printed("bytes")) == Failed("GitHub's answer to GET repos/o/r/releases/assets/100 was not in the expected shape")
expect PublishRelease.reply(final, Download({ id: 100, name: bundle, limit: 12 }), answering("502")) == Failed("GitHub answered GET repos/o/r/releases/assets/100 with HTTP 502: Validation Failed")
