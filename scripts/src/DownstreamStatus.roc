import GitHub

## Read-only downstream inventory. A SHA is a shared-code version; tool release
## tags are a separate version scheme. Unknown evidence never becomes current.
DownstreamStatus := [].{
	Reference : { file : Str, target : Str, revision : Str }
	Snapshot : { repository : Str, head : Str, references : List(Reference), problem : Str }
	CompatibilityResult : { message : Str, color : Str, url : Str, date : Str, detail : Str }
	Run : { id : U64, run_attempt : U64, path : Str, referenced_workflows : List({ path : Str, sha : Str }), head_sha : Str, head_branch : Str, event : Str, status : Str, conclusion : Str, updated_at : Str }
	Jobs : { total_count : U64, jobs : List({ id : U64, run_id : U64, run_attempt : U64, head_sha : Str, name : Str, status : Str, conclusion : Str, steps : List({ name : Str, conclusion : Str }) }) }
	Entry : { automation : Snapshot, compatibility : CompatibilityResult }
	compatibility_workflow = "${upstream}/.github/workflows/nightly-compatibility.yml"

	caller : Snapshot -> Try(Reference, Str)
	caller = |snapshot| {
		if snapshot.problem != "" {
			return Err("incomplete source inventory")
		}
		match snapshot.references.keep_if(|ref| lower(ref.target) == compatibility_workflow) {
			[ref] if sha(ref.revision) => Ok(ref)
			[] => Err("adopt shared nightly")
			_ => Err("review nightly configuration")
		}
	}

	unknown = |repository, detail| { message: "not verified · investigate", color: "lightgrey", url: "https://github.com/${repository}/actions", date: "", detail }

	## Check every scalar, including metadata outside jobs, before interpreting
	## indentation. A multiline quoted/flow scalar can contain root-looking text.
	## Quoted values must close on this line; quoted escapes retain YAML meaning.
	## Flow mappings/lists are deliberately restricted to empty {} and [].
	single_line_scalar : Str -> Bool
	single_line_scalar = |value| {
		trimmed = value.trim()
		if ["|", ">", "&", "*", "!"].any(|marker| trimmed.starts_with(marker)) {
			return Bool.False
		}
		if trimmed.starts_with("[") or trimmed.starts_with("{") {
			return ["[]", "{}"].contains(trimmed)
		}
		match Str.to_utf8(trimmed) {
			[39, .. as rest] => quoted_end(rest, 39)
			[34, .. as rest] => quoted_end(rest, 34)
			_ => Bool.True
		}
	}

	quoted_end : List(U8), U8 -> Bool
	quoted_end = |bytes, quote| match bytes {
		[] => Bool.False
		[39, 39, .. as rest] if quote == 39 => quoted_end(rest, quote)
		[92, _, .. as rest] if quote == 34 => quoted_end(rest, quote)
		[first, .. as rest] if first == quote => {
			tail = Str.from_utf8_lossy(rest).trim()
			tail == "" or tail.starts_with("#")
		}
		[_, .. as rest] => quoted_end(rest, quote)
	}

	## A deliberately restricted caller has exactly one reusable-workflow job.
	## Without origin metadata on individual REST jobs, allowing arbitrary local
	## jobs would let their display names impersonate the shared verdict. Other
	## valid YAML shapes are unknown, never evidence of a compatibility pass.
	caller_job : Str, Reference -> Try(Str, Str)
	caller_job = |source, ref| {
		var $in_jobs = Bool.False
		var $seen_jobs = Bool.False
		var $job = ""
		var $keys = []
		var $seen_uses = Bool.False
		var $section = ""
		for raw in Str.split_on(source, "\n") {
			line = raw.trim()
			if line != "" and !line.starts_with("#") {
				if raw.contains("\t") {
					return Err("unsupported caller indentation")
				}
				candidate = line.drop_prefix("- ")
				scalar = if ["'", "\"", "[", "{", "&", "*", "!"].any(|marker| candidate.starts_with(marker)) candidate else match Str.split_first(line, ":") {
					Ok({ after, .. }) => after
					Err(_) => candidate
				}
				if !single_line_scalar(scalar) {
					return Err("caller scalar must finish on one line")
				}
				indent = Str.to_utf8(raw).len() - Str.to_utf8(raw.trim_start()).len()
				if indent == 0 {
					if line == "jobs:" {
						if $seen_jobs {
							return Err("duplicate jobs mapping")
						}
						$in_jobs = Bool.True
						$seen_jobs = Bool.True
					} else {
						$in_jobs = Bool.False
						root = match Str.split_first(line, ":") {
							Ok(value) => value
							Err(_) => return Err("unsupported caller root")
						}
						key = root.before
						if !["name", "on", "permissions", "concurrency", "run-name"].contains(key) or $keys.contains(key) {
							return Err("unsupported or duplicate caller root")
						}
						$keys = $keys.append(key)
					}
				} else if $in_jobs {
					if indent == 2 {
						id = line.drop_suffix(":")
						if $job != "" or !line.ends_with(":") or !identifier(id) {
							return Err("caller requires one literal reusable-workflow job")
						}
						$job = id
					} else {
						if $job == "" {
							return Err("missing caller job")
						}
						if indent == 4 {
							pair = match Str.split_first(line, ":") {
								Ok(value) => value
								Err(_) => return Err("unsupported caller job field")
							}
							if !["uses", "with", "permissions"].contains(pair.before) or $keys.contains("job:${pair.before}") {
								return Err("unsupported or duplicate caller job field")
							}
							$keys = $keys.append("job:${pair.before}")
							$section = pair.before
							if pair.before == "uses" {
								if !literal_reference(pair.after.trim(), "${ref.target}@${ref.revision}") {
									return Err("caller workflow revision changed")
								}
								$seen_uses = Bool.True
							} else if pair.after.trim() != "" and !(pair.before == "permissions" and pair.after.trim() == "{}") {
								return Err("unsupported caller mapping")
							}
						} else {
							if indent != 6 or !["with", "permissions"].contains($section) {
								return Err("unsupported caller mapping depth")
							}
							pair = match Str.split_first(line, ":") {
								Ok(value) => value
								Err(_) => return Err("unsupported caller value")
							}
							if !identifier(pair.before) or pair.after.trim() == "" or !single_line_scalar(pair.after) {
								return Err("caller values must be literal single lines")
							}
							field = "${$section}:${pair.before}"
							if $keys.contains(field) {
								return Err("duplicate caller value")
							}
							$keys = $keys.append(field)
						}
					}
				}
			}
		}
		if $seen_jobs and $seen_uses and $job != "" {
			Ok($job)
		} else {
			Err("missing reusable-workflow caller")
		}
	}

	identifier = |value| value != "" and Str.to_utf8(value).all(|byte| (byte >= 'a' and byte <= 'z') or (byte >= 'A' and byte <= 'Z') or (byte >= '0' and byte <= '9') or byte == '_' or byte == '-')
	literal = |value| value.drop_prefix("\"").drop_suffix("\"").drop_prefix("'").drop_suffix("'")
	literal_reference = |value, expected| [expected, "\"${expected}\"", "'${expected}'"].any(
		|candidate| {
			if !value.starts_with(candidate) {
				Bool.False
			} else {
				tail = value.drop_prefix(candidate)
				tail == "" or (tail.starts_with(" ") and tail.trim_start().starts_with("#"))
			}
		},
	)

	## Both immutable caller structure and GitHub's resolved reusable-workflow
	## identity bind display-name verdicts to the only executable job origin.
	coherent_jobs = |ran, jobs| jobs.total_count == jobs.jobs.len() and jobs.jobs.all(|job| job.run_id == ran.id and job.run_attempt == ran.run_attempt and job.head_sha == ran.head_sha) and unique(jobs.jobs.map(|job| job.id.to_str())).len() == jobs.jobs.len()

	## The pinned report job runs only with both selectors empty. Its success
	## distinguishes a latest-pair dispatch from an arbitrary historical probe.
	default_dispatch : Snapshot, Run, Jobs, Str -> Bool
	default_dispatch = |snapshot, ran, jobs, caller_source| {
		if ran.event != "workflow_dispatch" or !coherent_jobs(ran, jobs) {
			return Bool.False
		}
		ref = match caller(snapshot) {
			Ok(value) => value
			Err(_) => return Bool.False
		}
		job_id = match caller_job(caller_source, ref) {
			Ok(value) => value
			Err(_) => return Bool.False
		}
		if ran.path != ref.file or ran.referenced_workflows != [{ path: "${ref.target}@${ref.revision}", sha: ref.revision }] or ran.run_attempt == 0 or !sha(ran.head_sha) {
			return Bool.False
		}
		match jobs.jobs.keep_if(|job| job.name == "${job_id} / Track release compatibility") {
			[job] => job.status == "completed" and job.conclusion == "success"
			_ => Bool.False
		}
	}

	compatibility : Snapshot, Run, Jobs, Str, Str -> CompatibilityResult
	compatibility = |snapshot, ran, jobs, caller_source, cutoff| {
		url = "https://github.com/${snapshot.repository}/actions/runs/${ran.id.to_str()}"
		base = { message: "not verified · inspect run", color: "orange", url, date: short_date(ran.updated_at), detail: "The shared verdict could not establish compatibility." }
		ref = match caller(snapshot) {
			Ok(value) => value
			Err(reason) => return { ..base, detail: reason }
		}
		job_id = match caller_job(caller_source, ref) {
			Ok(value) => value
			Err(reason) => return { ..base, detail: reason }
		}
		resolved = [{ path: "${ref.target}@${ref.revision}", sha: ref.revision }]
		if ran.path != ref.file or ran.referenced_workflows != resolved or ran.run_attempt == 0 or (ran.event != "schedule" and !default_dispatch(snapshot, ran, jobs, caller_source)) or !sha(ran.head_sha) {
			return { ..base, message: "new configuration · rerun nightly" }
		}
		if ran.status != "completed" {
			return { ..base, message: "running · wait for result", color: "blue" }
		}
		if !coherent_jobs(ran, jobs) {
			return { ..base, detail: "Incomplete job inventory; cannot establish a pass." }
		}
		verdict = match jobs.jobs.keep_if(|job| job.name == "${job_id} / Compatibility verdict") {
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

	repositories = ["aoc", "basic-ssg", "roc-ansi", "roc-blueprint", "roc-fuzz", "roc-graph-layout", "roc-gui", "roc-pandoc", "roc-parser", "roc-pdf", "roc-platform-template-go", "roc-platform-template-rust", "roc-platform-template-zig", "roc-ray", "roc-signals", "roc-time", "roc-wasm4", "weaver"]
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
		var $block_indent = 0
		var $in_block = Bool.False
		var $structural = []
		for raw in Str.split_on(source, "\n") {
			line = raw.trim()
			indent = Str.to_utf8(raw).len() - Str.to_utf8(raw.trim_start()).len()
			if !($in_block and (line == "" or indent > $block_indent)) and !line.starts_with("#") {
				$in_block = Bool.False
				match Str.split_first(line, ":") {
					Ok({ after, .. }) if after.trim().starts_with("|") or after.trim().starts_with(">") or after.trim().starts_with("&") and (after.contains(" |") or after.contains(" >")) => {
						$block_indent = indent
						$in_block = Bool.True
					}
					_ => {}
				}
				$structural = $structural.append(line)
			}
		}
		lines = $structural.keep_if(|line| !line.starts_with("#")).map(
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
			unquoted = literal(value)
			match Str.split_last(unquoted, "@") {
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
		"<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width\"><title>Supported downstream status</title><h1>Supported downstream status</h1><p>Checked ${html(checked)} UTC against roc-automation main: <a href=\"https://github.com/${upstream}/commit/${html(latest)}\"><code>${html(latest)}</code></a>.</p><p>Compatibility means the latest eligible scheduled or default-selector manual run passed the central published-release gate on every target. Historical manual probes are excluded. This snapshot marks passes over 48 hours old stale. If the check date is over 48 hours old, investigate Documentation publication before relying on green: static badges retain their last colour when publication stops. Legacy updater results are excluded. Open the run for the exact release/nightly pair, per-target raw diagnostics and summary.json. Warnings are visible and nonfatal. Missing evidence requires investigation, not an assumption of compatibility.</p><p>Automation revisions are direct references in default-branch workflows and composite actions. All SHAs must match the observed main commit, including documentation-only changes. Review upgrades manually; matching main is not a security-compliance claim. Nested dependencies inside pinned shared code are outside this inventory. A newer check timestamp requires a successful Documentation deployment.</p><table><thead><tr><th>Repository</th><th>Published-release compatibility / action</th><th>Automation revision / action</th></tr></thead><tbody>${rows}</tbody></table></html>"
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

ran = { id: 1, run_attempt: 1, path: nightly_ref.file, referenced_workflows: [{ path: "${nightly_ref.target}@${current}", sha: current }], head_sha: older, head_branch: "main", event: "schedule", status: "completed", conclusion: "success", updated_at: "2026-10-21T09:00:00Z" }

verdict = { id: 10, run_id: 1, run_attempt: 1, head_sha: older, name: "nightly / Compatibility verdict", status: "completed", conclusion: "success", steps: [{ name: "Classify target evidence", conclusion: "success" }, { name: "All published examples passed", conclusion: "success" }] }

jobs = { total_count: 1, jobs: [verdict] }

caller_source = "name: Nightly compatibility\non:\n  schedule:\n    - cron: '0 0 * * *'\npermissions: {}\njobs:\n  nightly:\n    permissions:\n      contents: read\n      issues: write\n    uses: ${DownstreamStatus.compatibility_workflow}@${current}\n    with:\n      runners: '[\"ubuntu-24.04\"]'"

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

manual = { ..ran, event: "workflow_dispatch" }

tracking = { ..verdict, id: 11, name: "nightly / Track release compatibility", steps: [] }

manual_jobs = { total_count: 2, jobs: [verdict, tracking] }

expect DownstreamStatus.compatibility(monitored, manual, manual_jobs, caller_source, cutoff).color == "brightgreen"
expect DownstreamStatus.compatibility(monitored, { ..manual, updated_at: "2026-10-18T09:00:00Z" }, manual_jobs, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, manual, { ..manual_jobs, total_count: 3 }, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, manual, { total_count: 3, jobs: [verdict, tracking, { ..tracking, id: 12 }] }, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, manual, { ..manual_jobs, jobs: [verdict, { ..tracking, conclusion: "skipped" }] }, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, manual, { ..manual_jobs, jobs: [verdict, { ..tracking, conclusion: "failure" }] }, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, manual, { ..manual_jobs, jobs: [verdict, { ..tracking, status: "in_progress" }] }, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, manual, { ..manual_jobs, jobs: [verdict, { ..tracking, run_attempt: 2 }] }, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, manual, { ..manual_jobs, jobs: [verdict, { ..tracking, run_id: 2 }] }, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, manual, { ..manual_jobs, jobs: [verdict, { ..tracking, head_sha: current }] }, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, manual, { ..manual_jobs, jobs: [verdict, { ..tracking, name: "local / Track release compatibility" }] }, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, { ..manual, referenced_workflows: [] }, manual_jobs, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, manual, manual_jobs, heredoc, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, { ..manual, event: "push" }, manual_jobs, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, { ..manual, conclusion: "failure" }, { ..manual_jobs, jobs: [{ ..verdict, conclusion: "failure", steps: [{ name: "Published examples failed", conclusion: "failure" }] }, tracking] }, caller_source, cutoff).color == "red"

expect DownstreamStatus.compatibility(monitored, ran, { ..jobs, jobs: [{ ..verdict, steps: [] }] }, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, ran, { ..jobs, jobs: [{ ..verdict, steps: [{ name: "Published examples failed", conclusion: "failure" }] }] }, caller_source, cutoff).color == "red"
expect DownstreamStatus.compatibility(monitored, ran, { ..jobs, jobs: [{ ..verdict, steps: [{ name: "Compatibility evidence incomplete", conclusion: "failure" }] }] }, caller_source, cutoff).message == "not tested · fix setup / rerun"
expect DownstreamStatus.caller(sample).is_err()

## Origin regressions: local display names and source text are not execution.
expect DownstreamStatus.caller_job(caller_source, nightly_ref) == Ok("nightly")
expect DownstreamStatus.caller_job(caller_source.replace_each("@${current}\n", "@${current} # reviewed pin\n"), nightly_ref) == Ok("nightly")
expect DownstreamStatus.caller_job(caller_source.replace_each("@${current}\n", "@${current}   # reviewed pin\n"), nightly_ref) == Ok("nightly")
expect DownstreamStatus.caller_job(caller_source.replace_each("${nightly_ref.target}@${current}", "'${nightly_ref.target}@${current}' # reviewed pin"), nightly_ref) == Ok("nightly")
expect DownstreamStatus.caller_job(caller_source.replace_each("${nightly_ref.target}@${current}", "\"${nightly_ref.target}@${current}\" # reviewed pin"), nightly_ref) == Ok("nightly")
expect DownstreamStatus.caller_job(caller_source.replace_each("@${current}\n", "@${current}#suffix\n"), nightly_ref).is_err()
expect DownstreamStatus.caller_job(caller_source.replace_each("${nightly_ref.target}@${current}", "\"${nightly_ref.target}@${current} # inside literal\""), nightly_ref).is_err()
expect DownstreamStatus.caller_job(caller_source.replace_each("    uses:", "    if: false\n    uses:"), nightly_ref).is_err()
expect DownstreamStatus.caller_job(caller_source.replace_each("    uses:", "    name: forged\n    uses:"), nightly_ref).is_err()
expect DownstreamStatus.caller_job(caller_source.replace_each("    uses:", "    runs-on: ubuntu-24.04\n    uses:"), nightly_ref).is_err()
expect DownstreamStatus.caller_job("uses: ${nightly_ref.target}@${current}", nightly_ref).is_err()
expect DownstreamStatus.caller_job("# uses: ${nightly_ref.target}@${current}", nightly_ref).is_err()
expect DownstreamStatus.caller_job("examples:\n  uses: ${nightly_ref.target}@${current}", nightly_ref).is_err()
expect DownstreamStatus.caller_job(caller_source.replace_each("jobs:", "jobs: &jobs"), nightly_ref).is_err()
expect DownstreamStatus.caller_job(caller_source.replace_each("  nightly:", "  nightly: &nightly"), nightly_ref).is_err()
expect DownstreamStatus.caller_job(caller_source.replace_each("  nightly:", "  nightly:\n    <<: *spoof"), nightly_ref).is_err()
expect DownstreamStatus.caller_job(caller_source.concat("\n  local:\n    name: nightly / Compatibility verdict\n    runs-on: ubuntu-24.04"), nightly_ref).is_err()
expect DownstreamStatus.caller_job(caller_source.concat("\njobs:\n  local:\n    uses: ${nightly_ref.target}@${current}"), nightly_ref).is_err()
expect DownstreamStatus.caller_job(caller_source.replace_each("    uses:", "    uses: ${nightly_ref.target}@${current}\n    uses:"), nightly_ref).is_err()

heredoc = "name: Local workflow\non:\n  schedule:\n    - cron: '0 0 * * *'\njobs:\n  local:\n    name: Compatibility verdict\n    runs-on: ubuntu-24.04\n    steps:\n      - name: Classify target evidence\n        run: |\n          cat <<'NOTE'\n          uses: ${nightly_ref.target}@${current}\n          NOTE\n      - name: All published examples passed\n        run: echo ok"

expect DownstreamStatus.references(nightly_ref.file, heredoc) == Ok([])
expect DownstreamStatus.caller_job(heredoc, nightly_ref).is_err()
expect DownstreamStatus.compatibility(monitored, ran, jobs, heredoc, cutoff).color != "brightgreen"
expect DownstreamStatus.references("action.yml", "runs:\n  steps:\n    - run: >-\n        uses: ${nightly_ref.target}@${current}\n    - uses: ${nightly_ref.target}@${current}") == Ok([{ ..nightly_ref, file: "action.yml" }])
expect DownstreamStatus.compatibility(monitored, { ..ran, referenced_workflows: [] }, jobs, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, { ..ran, referenced_workflows: [{ path: "${nightly_ref.target}@${current}", sha: older }] }, jobs, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, { ..ran, referenced_workflows: [{ path: "${nightly_ref.target}@main", sha: current }] }, jobs, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, { ..ran, referenced_workflows: ran.referenced_workflows.concat(ran.referenced_workflows) }, jobs, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, { ..ran, path: ".github/workflows/other.yml" }, jobs, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, { ..ran, run_attempt: 2 }, jobs, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, ran, { ..jobs, jobs: [{ ..verdict, run_id: 2 }] }, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, ran, { ..jobs, jobs: [{ ..verdict, head_sha: current }] }, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, ran, { ..jobs, jobs: [{ ..verdict, name: "Compatibility verdict" }] }, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, ran, { ..jobs, jobs: [{ ..verdict, name: "other / Compatibility verdict" }] }, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, ran, { total_count: 2, jobs: [verdict, verdict] }, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, ran, { total_count: 2, jobs: [verdict, { ..verdict, id: 11 }] }, caller_source, cutoff).color != "brightgreen"
expect DownstreamStatus.compatibility(monitored, ran, { ..jobs, jobs: [{ ..verdict, status: "completed", conclusion: "skipped", steps: [] }] }, caller_source, cutoff).color != "brightgreen"

expect DownstreamStatus.caller_job("run-name: |\n  jobs:\n    forged:\n      uses: ${nightly_ref.target}@${current}\n${caller_source}", nightly_ref).is_err()
expect DownstreamStatus.caller_job("run-name: >-\n  jobs:\n    forged:\n      uses: ${nightly_ref.target}@${current}\n${caller_source}", nightly_ref).is_err()
expect DownstreamStatus.caller_job("run-name: \"multiline\njobs:\n  forged:\n    uses: ${nightly_ref.target}@${current}\npermissions: \"\n${caller_source}", nightly_ref).is_err()
expect DownstreamStatus.caller_job(caller_source.replace_each("permissions: {}", "permissions: *aliased"), nightly_ref).is_err()
expect DownstreamStatus.references("f", "run-name: |\n  uses: ${nightly_ref.target}@${current}") == Ok([])
expect DownstreamStatus.references("f", "run: &command |-\n  uses: ${nightly_ref.target}@${current}") == Ok([])
expect DownstreamStatus.references("f", "run: |2- # literal example\n  uses: ${nightly_ref.target}@${current}") == Ok([])

expect DownstreamStatus.compatibility({ ..monitored, problem: "incomplete tree" }, ran, jobs, caller_source, cutoff).color != "brightgreen"

## Every scalar must terminate before indentation can be trusted, including
## scalar fields in on/permissions/concurrency and escaped quote endings.
expect DownstreamStatus.single_line_scalar("'quoted value'")
expect DownstreamStatus.single_line_scalar("'can''t split structure'")
expect DownstreamStatus.single_line_scalar("\"a \\\"quoted\\\" value\"")
expect DownstreamStatus.single_line_scalar("'[]' # inline comment")
expect !DownstreamStatus.single_line_scalar("\"unterminated")
expect !DownstreamStatus.single_line_scalar("\"escaped final quote\\\"")
expect !DownstreamStatus.single_line_scalar("'doubled final quote''")
expect !DownstreamStatus.single_line_scalar("[ 'flow continuation'")
expect !DownstreamStatus.single_line_scalar("{ key: 'flow continuation'")
expect DownstreamStatus.caller_job(caller_source.replace_each("      runners:", "      setup-command: \"[]\nrun-name: hidden\n      end: value\"\n    if: false\n      runners:"), nightly_ref).is_err()
expect DownstreamStatus.caller_job(caller_source.replace_each("      runners:", "      setup-command: \"[]\\\"\nrun-name: hidden\n      end: value\"\n    if: false\n      runners:"), nightly_ref).is_err()
expect DownstreamStatus.caller_job(caller_source.replace_each("      runners:", "      setup-command: '[]''\nrun-name: hidden\n      end: value'\n    if: false\n      runners:"), nightly_ref).is_err()
expect DownstreamStatus.caller_job(caller_source.replace_each("      runners:", "      setup-command: [\nrun-name: hidden\n      end: value]\n    if: false\n      runners:"), nightly_ref).is_err()
expect DownstreamStatus.caller_job("on:\n  workflow_dispatch:\n    inputs:\n      ref:\n        default: \"continued\n${caller_source}", nightly_ref).is_err()
expect DownstreamStatus.caller_job("concurrency:\n  group: \"continued\n${caller_source}", nightly_ref).is_err()
expect DownstreamStatus.caller_job("permissions:\n  contents: \"continued\n${caller_source}", nightly_ref).is_err()
expect DownstreamStatus.caller_job("on:\n  workflow_dispatch:\n    inputs:\n      ref:\n        options:\n          - 'https://continued\n${caller_source}", nightly_ref).is_err()
expect DownstreamStatus.caller_job("on:\n  workflow_dispatch:\n    inputs:\n      ref:\n        \"quoted:key\": \"continued\n${caller_source}", nightly_ref).is_err()
