import GitHub

## Read-only downstream inventory. A SHA is a shared-code version; tool release
## tags are a separate version scheme. Unknown evidence never becomes current.
DownstreamStatus := [].{
	Reference : { file : Str, target : Str, revision : Str }
	Snapshot : { repository : Str, head : Str, references : List(Reference), problem : Str }
	CompatibilityResult : { message : Str, color : Str, url : Str, date : Str, detail : Str }
	Run : { id : U64, head_sha : Str, head_branch : Str, event : Str, status : Str, conclusion : Str, updated_at : Str }
	Jobs : { total_count : U64, jobs : List({ name : Str, status : Str, conclusion : Str, steps : List({ name : Str, conclusion : Str }) }) }
	Entry : { automation : Snapshot, compatibility : CompatibilityResult }
	compatibility_workflow = "${upstream}/.github/workflows/nightly-compatibility.yml"

	caller : Snapshot -> Try(Reference, Str)
	caller = |snapshot| match snapshot.references.keep_if(|ref| lower(ref.target) == compatibility_workflow) {
		[ref] if sha(ref.revision) => Ok(ref)
		[] => Err("adopt shared nightly")
		_ => Err("review nightly configuration")
	}

	unknown = |repository, detail| { message: "not verified · investigate", color: "lightgrey", url: "https://github.com/${repository}/actions", date: "", detail }

	## Fixed verdict steps belong to the central workflow, whose current pinned
	## revision must also appear in the run's immutable caller. Controller success
	## and legacy workflow badges are never compatibility evidence.
	compatibility : Snapshot, Run, Jobs, Str, Str -> CompatibilityResult
	compatibility = |snapshot, ran, jobs, caller_source, cutoff| {
		url = "https://github.com/${snapshot.repository}/actions/runs/${ran.id.to_str()}"
		base = { message: "not verified · inspect run", color: "orange", url, date: short_date(ran.updated_at), detail: "The shared verdict could not establish compatibility." }
		ref = match caller(snapshot) {
			Ok(value) => value
			Err(reason) => return { ..base, detail: reason }
		}
		old_refs = match references(ref.file, caller_source) {
			Ok(values) => values
			Err(_) => return base
		}
		if !old_refs.contains(ref) or ran.event != "schedule" or !sha(ran.head_sha) {
			return { ..base, message: "new configuration · rerun nightly" }
		}
		if ran.status != "completed" {
			return { ..base, message: "running · wait for result", color: "blue" }
		}
		if jobs.total_count != jobs.jobs.len() {
			return { ..base, detail: "Incomplete job inventory; cannot establish a pass." }
		}
		verdict = match jobs.jobs.keep_if(|job| job.name == "Compatibility verdict" or job.name.ends_with(" / Compatibility verdict")) {
			[value] => value
			_ => return base
		}
		did = |name, conclusion| verdict.steps.any(|step| step.name == name and step.conclusion == conclusion)
		if did("Published examples failed", "failure") {
			return { ..base, message: "failed · investigate / patch release", color: "red", detail: "Published examples failed. Inspect target diagnostics; repair incompatibility in a new release." }
		}
		if did("Compatibility evidence incomplete", "failure") {
			return { ..base, message: "not tested · fix setup / rerun", detail: "Inspect missing targets, skipped cases, timeout or prerequisite setup; rerun all jobs." }
		}
		if ran.conclusion != "success" or verdict.status != "completed" or verdict.conclusion != "success" or !did("Classify target evidence", "success") or !did("All published examples passed", "success") {
			return base
		}
		if cutoff == "" or ran.updated_at == "" or GitHub.sort([ran.updated_at, cutoff]) == [ran.updated_at, cutoff] {
			return { ..base, message: "stale pass · rerun nightly", detail: "The last pass is at least 48 hours old. Refresh validation before relying on it." }
		}
		{ ..base, message: "passed · no action", color: "brightgreen", detail: "Every release target passed the shared compatibility gate. Compiler warnings remain visible and nonfatal." }
	}

	short_date = |value| Str.from_utf8_lossy(Str.to_utf8(value).take_first(10))
	compatibility_badge : CompatibilityResult, Str -> Str
	compatibility_badge = |result, checked| Json.to_str({ schemaVersion: 1, label: if result.date == "" "checked ${short_date(checked)}" else "run ${result.date}", message: result.message, color: result.color, cacheSeconds: 3600 })

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
			return { message: "unknown · investigate", color: "lightgrey" }
		}
		refs = snapshot.references.map(|ref| ref.revision)
		if refs.is_empty() {
			return { message: "not adopted · review setup", color: "lightgrey" }
		}
		if !refs.all(sha) {
			return { message: "unpinned · pin full SHA", color: "orange" }
		}
		versions = Str.join_with(unique(refs).map(short), ", ")
		if refs.all(|ref| ref == latest) {
			{ message: "${versions} · matches main", color: "brightgreen" }
		} else {
			{ message: "${versions} · review upgrade", color: "orange" }
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

	report : List(Entry), Str, Str -> Str
	report = |snapshots, latest, checked| {
		rows = Str.join_with(
			snapshots.map(
				|entry| {
					snapshot = entry.automation
					links = Str.join_with(
						snapshot.references.map(
							|ref| {
								"<li><a href=\"https://github.com/${html(snapshot.repository)}/blob/${html(snapshot.head)}/${html(ref.file)}\">${html(ref.file)}</a>: <code>${html(ref.target)}@${html(ref.revision)}</code></li>"
							},
						),
						"",
					)
					"<tr><td>${html(snapshot.repository)}</td><td><a href=\"${html(entry.compatibility.url)}\">${html(entry.compatibility.message)}</a> ${html(entry.compatibility.date)}<p>${html(entry.compatibility.detail)}</p></td><td>${html(status(snapshot, latest).message)}<p>${html(snapshot.problem)}</p><ul>${links}</ul></td></tr>"
				},
			),
			"\n",
		)
		"<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width\"><title>Supported downstream status</title><h1>Supported downstream status</h1><p>Checked ${html(checked)} UTC against roc-automation main: <a href=\"https://github.com/${upstream}/commit/${html(latest)}\"><code>${html(latest)}</code></a>.</p><p>Compatibility means the latest scheduled run passed the central published-release gate on every target; passes expire after 48 hours. Legacy updater results are excluded. Open the run for the exact release/nightly pair, per-target raw diagnostics and summary.json. Warnings are visible and nonfatal. Missing evidence requires investigation, not an assumption of compatibility.</p><p>Automation revisions are direct references in default-branch workflows and composite actions. All SHAs must match the observed main commit, including documentation-only changes. Review upgrades manually; matching main is not a security-compliance claim. Nested dependencies inside pinned shared code are outside this inventory. A newer check timestamp requires a successful Documentation deployment.</p><table><thead><tr><th>Repository</th><th>Published-release compatibility / action</th><th>Automation revision / action</th></tr></thead><tbody>${rows}</tbody></table></html>"
	}
}

current = Str.repeat("a", 40)

older = Str.repeat("b", 40)

sample = { repository: "o/r", head: older, references: [{ file: ".github/workflows/ci.yml", target: "lukewilliamboswell/roc-automation/actions/setup-blueprint", revision: current }], problem: "" }

expect DownstreamStatus.status(sample, current).color == "brightgreen"
expect DownstreamStatus.status({ ..sample, references: sample.references.append({ file: "f", target: "t", revision: older }) }, current).message == "aaaaaaa, bbbbbbb · review upgrade"
expect DownstreamStatus.status({ ..sample, problem: "HTTP 403" }, current).message == "unknown · investigate"
expect DownstreamStatus.status(sample, "").message == "unknown · investigate"
expect DownstreamStatus.status({ ..sample, references: [] }, current).message == "not adopted · review setup"
expect DownstreamStatus.status({ ..sample, references: [{ file: "f", target: "t", revision: "main" }] }, current).message == "unpinned · pin full SHA"
expect !DownstreamStatus.sha("abc1234") and !DownstreamStatus.sha(Str.repeat("g", 40))
expect DownstreamStatus.references("ci.yml", "# uses: lukewilliamboswell/roc-automation/a@main\n- uses: 'lukewilliamboswell/roc-automation/actions/setup-blueprint@${current}' # reviewed\r\n") == Ok([{ file: "ci.yml", target: "lukewilliamboswell/roc-automation/actions/setup-blueprint", revision: current }])
expect DownstreamStatus.references("f", "uses: actions/checkout@abc # lukewilliamboswell/roc-automation/a@main") == Ok([])
expect DownstreamStatus.references("f", "- {uses: lukewilliamboswell/roc-automation/a@main}").is_err()
expect DownstreamStatus.references("f", "uses: lukewilliamboswell/roc-automation/a").is_err()
expect DownstreamStatus.references("f", Str.concat("uses: lukewilliamboswell/roc-automation/a@$", "{{ inputs.ref }}")).is_err()
expect !DownstreamStatus.safe_path("../secret") and !DownstreamStatus.safe_path("a?ref=main") and !DownstreamStatus.safe_path("a//b")
expect DownstreamStatus.source_file(".github/workflows/ci.yaml") and DownstreamStatus.source_file("actions/test/action.yml") and !DownstreamStatus.source_file("src/main.roc")
expect DownstreamStatus.html("<script>&\"'") == "&lt;script&gt;&amp;&quot;&#39;"
expect DownstreamStatus.badge(sample, current, "2026-10-21").contains("2026-10-21")
expect DownstreamStatus.source_paths("{\"truncated\":true,\"tree\":[]}").is_err()
expect DownstreamStatus.source_paths("{\"tree\":[]}").is_err()
expect DownstreamStatus.source_paths("not JSON").is_err()
expect DownstreamStatus.source_paths("{\"truncated\":false,\"tree\":[]}") == Ok([])
expect DownstreamStatus.source_paths("{\"truncated\":false,\"tree\":[{\"path\":\".github/workflows/ci.yml\",\"type\":\"blob\",\"mode\":\"120000\"}]}").is_err()
expect DownstreamStatus.references("f", "uses: LukeWilliamBoswell/roc-automation/actions/setup-blueprint@${current}").is_ok()
expect DownstreamStatus.get_args("repos/o/r", "application/json", "") == ["api", "--method", "GET", "-H", "Accept: application/json", "repos/o/r"]
expect DownstreamStatus.get_args("repos/o/r", "application/json", ".sha") == ["api", "--method", "GET", "-H", "Accept: application/json", "repos/o/r", "--jq", ".sha"]

nightly_ref = { file: ".github/workflows/nightly.yml", target: DownstreamStatus.compatibility_workflow, revision: current }

monitored = { ..sample, references: [nightly_ref] }

ran = { id: 1, head_sha: older, head_branch: "main", event: "schedule", status: "completed", conclusion: "success", updated_at: "2026-10-21T09:00:00Z" }

verdict = { name: "nightly / Compatibility verdict", status: "completed", conclusion: "success", steps: [{ name: "Classify target evidence", conclusion: "success" }, { name: "All published examples passed", conclusion: "success" }] }

jobs = { total_count: 1, jobs: [verdict] }

caller_source = "uses: ${DownstreamStatus.compatibility_workflow}@${current}"

cutoff = "2026-10-19T09:00:00Z"

expect DownstreamStatus.compatibility(monitored, ran, jobs, caller_source, cutoff).color == "brightgreen"
expect DownstreamStatus.compatibility(monitored, { ..ran, updated_at: "2026-10-18T09:00:00Z" }, jobs, caller_source, cutoff).message == "stale pass · rerun nightly"
expect DownstreamStatus.compatibility(monitored, ran, jobs, caller_source, "").color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, ran, jobs, caller_source.replace_each(current, older), cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, ran, { ..jobs, total_count: 2 }, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, ran, { total_count: 0, jobs: [] }, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, { ..ran, conclusion: "failure" }, jobs, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, { ..ran, status: "in_progress" }, jobs, caller_source, cutoff).color == "blue"
expect DownstreamStatus.compatibility(monitored, { ..ran, event: "workflow_dispatch" }, jobs, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, ran, { ..jobs, jobs: [{ ..verdict, steps: [] }] }, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, ran, { ..jobs, jobs: [{ ..verdict, steps: [{ name: "Published examples failed", conclusion: "failure" }] }] }, caller_source, cutoff).color == "red"
expect DownstreamStatus.compatibility(monitored, ran, { ..jobs, jobs: [{ ..verdict, steps: [{ name: "Compatibility evidence incomplete", conclusion: "failure" }] }] }, caller_source, cutoff).message == "not tested · fix setup / rerun"
expect DownstreamStatus.caller(sample).is_err()
