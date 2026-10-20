## Read-only downstream inventory. A SHA is a shared-code version; tool release
## tags are a separate version scheme. Unknown evidence never becomes current.
Downstreams := [].{
	Reference : { file : Str, target : Str, revision : Str }
	Snapshot : { repository : Str, head : Str, references : List(Reference), problem : Str }

	repositories = ["aoc", "basic-ssg", "roc-ansi", "roc-blueprint", "roc-fuzz", "roc-graph-layout", "roc-pandoc", "roc-parser", "roc-pdf", "roc-platform-template-go", "roc-platform-template-rust", "roc-platform-template-zig", "roc-ray", "roc-signals", "roc-time", "roc-wasm4", "weaver"]
	owner = "lukewilliamboswell"
	upstream = "${owner}/roc-automation"

	sha : Str -> Bool
	sha = |value| Str.to_utf8(value).len() == 40 and Str.to_utf8(value).all(|byte| (byte >= '0' and byte <= '9') or (byte >= 'a' and byte <= 'f'))

	## Only inert workflow and composite-action source is read, never executed.
	source_file : Str -> Bool
	source_file = |path|
		(path.starts_with(".github/workflows/") and (path.ends_with(".yml") or path.ends_with(".yaml"))) or path.ends_with("/action.yml") or path.ends_with("/action.yaml")

	safe_path : Str -> Bool
	safe_path = |path| path != "" and !path.starts_with("/") and Str.split_on(path, "/").all(|part| part != "." and part != ".." and part != "" and Str.to_utf8(part).all(|byte| (byte >= 'a' and byte <= 'z') or (byte >= 'A' and byte <= 'Z') or (byte >= '0' and byte <= '9') or ['-', '_', '.'].contains(byte)))

	lower = |value| Str.from_utf8_lossy(Str.to_utf8(value).map(|byte| if byte >= 'A' and byte <= 'Z' byte + 32 else byte))

	## Incomplete trees and symbolic links cannot establish a complete inventory.
	source_paths : Str -> Try(List(Str), Str)
	source_paths = |source| {
		parsed : Try({ truncated : Bool, tree : List({ path : Str, type : Str, mode : Str }) }, _)
		parsed = Json.parse(source)
		entries = match parsed {
			Ok({ truncated: Bool.False, tree }) => tree.keep_if(|entry| source_file(entry.path))
			_ => return Err("incomplete or unreadable repository tree")
		}
		if entries.all(|entry| entry.type == "blob" and ["100644", "100755"].contains(entry.mode) and safe_path(entry.path)) {
			Ok(entries.map(|entry| entry.path))
		} else {
			Err("unsupported workflow/action path or file type")
		}
	}

	get_args : Str, Str, Str -> List(Str)
	get_args = |path, accept, jq| ["api", "--method", "GET", "-H", "Accept: ${accept}", path].concat(if jq == "" [] else ["--jq", jq])

	## A deliberately narrow reader of literal block-style uses lines. Unhandled
	## syntax mentioning this repository makes the snapshot unknown, not current.
	references : Str, Str -> Try(List(Reference), Str)
	references = |file, source| {
		var $found = []
		lines = Str.split_on(source, "\n").map(Str.trim).keep_if(|line| !line.starts_with("#")).map(
			|line| match Str.split_first(line, " #") {
				Ok({ before, .. }) => before
				Err(_) => line
			},
		).keep_if(|line| lower(line).contains("${upstream}/"))
		for uncommented in lines {
			value = match Str.split_first(uncommented, "uses:") {
				Ok({ before, after }) if ["", "-"].contains(before.trim()) => after.trim()
				_ => return Err("unsupported reference syntax in ${file}")
			}
			literal = value.drop_prefix("\"").drop_suffix("\"").drop_prefix("'").drop_suffix("'")
			match Str.split_last(literal, "@") {
				Ok({ before, after }) if lower(before).starts_with("${upstream}/") and safe_path(before) and after != "" and !after.contains(" ") and !after.contains("\t") => {
					$found = $found.append({ file, target: before, revision: after })
				}
				_ => return Err("unreadable reference in ${file}")
			}
		}
		Ok($found)
	}

	## All direct references must name the exact observed upstream main commit.
	status : Snapshot, Str -> { message : Str, color : Str }
	status = |snapshot, latest| {
		if snapshot.problem != "" or !sha(latest) {
			return { message: "unknown", color: "lightgrey" }
		}
		refs = snapshot.references.map(|ref| ref.revision)
		if refs.is_empty() {
			return { message: "not configured", color: "lightgrey" }
		}
		if !refs.all(sha) {
			return { message: "unpinned reference", color: "orange" }
		}
		versions = Str.join_with(unique(refs).map(short), ", ")
		if refs.all(|ref| ref == latest) {
			{ message: "${versions} (latest)", color: "brightgreen" }
		} else {
			{ message: "${versions} (upgrade available)", color: "orange" }
		}
	}

	badge : Snapshot, Str, Str -> Str
	badge = |snapshot, latest, checked| {
		result = status(snapshot, latest)
		date = Str.from_utf8_lossy(Str.to_utf8(checked).take_first(10))
		Json.to_str({ schemaVersion: 1, label: "checked ${date}", message: result.message, color: result.color, cacheSeconds: 3600 })
	}

	short : Str -> Str
	short = |value| Str.from_utf8_lossy(Str.to_utf8(value).take_first(7))

	unique : List(Str) -> List(Str)
	unique = |values| values.fold([], |seen, value| if seen.contains(value) seen else seen.append(value))

	## Escape every piece of API/source text before putting it in the report.
	html : Str -> Str
	html = |value| value.replace_each("&", "&amp;").replace_each("<", "&lt;").replace_each(">", "&gt;").replace_each("\"", "&quot;").replace_each("'", "&#39;")

	report : List(Snapshot), Str, Str -> Str
	report = |snapshots, latest, checked| {
		rows = Str.join_with(
			snapshots.map(
				|snapshot| {
					links = Str.join_with(
						snapshot.references.map(
							|ref| {
								"<li><a href=\"https://github.com/${html(snapshot.repository)}/blob/${html(snapshot.head)}/${html(ref.file)}\">${html(ref.file)}</a>: <code>${html(ref.target)}@${html(ref.revision)}</code></li>"
							},
						),
						"",
					)
					"<tr><td>${html(snapshot.repository)}</td><td>${html(status(snapshot, latest).message)}<p>${html(snapshot.problem)}</p><ul>${links}</ul></td></tr>"
				},
			),
			"\n",
		)
		"<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width\"><title>Downstream automation versions</title><h1>Downstream automation versions</h1><p>Checked ${html(checked)} UTC against roc-automation main: <a href=\"https://github.com/${upstream}/commit/${html(latest)}\"><code>${html(latest)}</code></a>.</p><p>Direct references in default-branch workflows and composite actions only. All SHAs must equal the observed main commit to be latest. Mixed versions are listed separately. This is an inventory, not evidence of CI success or security compliance. A newer timestamp requires a successful Documentation deployment.</p><table><thead><tr><th>Repository</th><th>Version and source</th></tr></thead><tbody>${rows}</tbody></table></html>"
	}
}

current = Str.repeat("a", 40)

older = Str.repeat("b", 40)

sample = { repository: "o/r", head: older, references: [{ file: ".github/workflows/ci.yml", target: "lukewilliamboswell/roc-automation/actions/setup-blueprint", revision: current }], problem: "" }

expect Downstreams.status(sample, current).color == "brightgreen"
expect Downstreams.status({ ..sample, references: sample.references.append({ file: "f", target: "t", revision: older }) }, current).message == "aaaaaaa, bbbbbbb (upgrade available)"
expect Downstreams.status({ ..sample, problem: "HTTP 403" }, current).message == "unknown"
expect Downstreams.status(sample, "").message == "unknown"
expect Downstreams.status({ ..sample, references: [] }, current).message == "not configured"
expect Downstreams.status({ ..sample, references: [{ file: "f", target: "t", revision: "main" }] }, current).message == "unpinned reference"
expect !Downstreams.sha("abc1234") and !Downstreams.sha(Str.repeat("g", 40))
expect Downstreams.references("ci.yml", "# uses: lukewilliamboswell/roc-automation/a@main\n- uses: 'lukewilliamboswell/roc-automation/actions/setup-blueprint@${current}' # reviewed\r\n") == Ok([{ file: "ci.yml", target: "lukewilliamboswell/roc-automation/actions/setup-blueprint", revision: current }])
expect Downstreams.references("f", "uses: actions/checkout@abc # lukewilliamboswell/roc-automation/a@main") == Ok([])
expect Downstreams.references("f", "- {uses: lukewilliamboswell/roc-automation/a@main}").is_err()
expect Downstreams.references("f", "uses: lukewilliamboswell/roc-automation/a").is_err()
expect Downstreams.references("f", Str.concat("uses: lukewilliamboswell/roc-automation/a@$", "{{ inputs.ref }}")).is_err()
expect !Downstreams.safe_path("../secret") and !Downstreams.safe_path("a?ref=main") and !Downstreams.safe_path("a//b")
expect Downstreams.source_file(".github/workflows/ci.yaml") and Downstreams.source_file("actions/test/action.yml") and !Downstreams.source_file("src/main.roc")
expect Downstreams.html("<script>&\"'") == "&lt;script&gt;&amp;&quot;&#39;"
expect Downstreams.badge(sample, current, "2026-10-21").contains("2026-10-21")
expect Downstreams.source_paths("{\"truncated\":true,\"tree\":[]}").is_err()
expect Downstreams.source_paths("{\"tree\":[]}").is_err()
expect Downstreams.source_paths("not JSON").is_err()
expect Downstreams.source_paths("{\"truncated\":false,\"tree\":[]}") == Ok([])
expect Downstreams.source_paths("{\"truncated\":false,\"tree\":[{\"path\":\".github/workflows/ci.yml\",\"type\":\"blob\",\"mode\":\"120000\"}]}").is_err()
expect Downstreams.references("f", "uses: LukeWilliamBoswell/roc-automation/actions/setup-blueprint@${current}").is_ok()
expect Downstreams.get_args("repos/o/r", "application/json", "") == ["api", "--method", "GET", "-H", "Accept: application/json", "repos/o/r"]
expect Downstreams.get_args("repos/o/r", "application/json", ".sha") == ["api", "--method", "GET", "-H", "Accept: application/json", "repos/o/r", "--jq", ".sha"]
