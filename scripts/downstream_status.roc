#!/usr/bin/env roc-stable
app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
	roc: "nightly-2026-10-09-258ab27",
}

import cli.Cmd
import cli.OsStr
import cli.Path
import cli.Stderr
import src/DownstreamStatus

## blueprint run downstream-status -- <site-directory>
## Writes inert Shields endpoints and a detailed report into the Pages artifact.
## Only GET requests; no consumer code is checked out or executed.
main! = |args| {
	output = match args.map(OsStr.display) {
		[directory] => Path.join(Path.utf8(directory), "downstream-status")
		_ => {
			Stderr.line!("usage: blueprint run downstream-status -- <site-directory>")?
			return Err(Exit(2))
		}
	}
	Path.create_all!(output)?
	upstream_head = get!("repos/${DownstreamStatus.upstream}/commits/main", "application/vnd.github+json", ".sha")?
	latest = match upstream_head {
		Ok(value) if DownstreamStatus.sha(value.trim()) => value.trim()
		_ => ""
	}
	date = Cmd.new("date").args([OsStr.from_str("-u"), OsStr.from_str("+%Y-%m-%dT%H:%MZ")]).stdout(Capture).run!()?
	if date.status != Exited(0) {
		return Err(Exit(1))
	}
	checked = Str.from_utf8_lossy(date.stdout_bytes).trim()
	# GNU date in Blueprint, with a macOS fallback for the standalone utility.
	cutoff_gnu = Cmd.new("date").args(["-u", "-d", "48 hours ago", "+%Y-%m-%dT%H:%M:%SZ"].map(OsStr.from_str)).stdout(Capture).stderr(Capture).run!()?
	cutoff_output = if cutoff_gnu.status == Exited(0) cutoff_gnu else Cmd.new("date").args(["-u", "-v-2d", "+%Y-%m-%dT%H:%M:%SZ"].map(OsStr.from_str)).stdout(Capture).stderr(Capture).run!()?
	cutoff = if cutoff_output.status == Exited(0) Str.from_utf8_lossy(cutoff_output.stdout_bytes).trim() else ""
	var $snapshots = []
	for name in DownstreamStatus.repositories {
		repository = "${DownstreamStatus.owner}/${name}"
		initial = { repository, head: "", references: [], problem: "" }
		snapshot = match snapshot!(repository)? {
			Ok(found) => found
			Err(problem) => { ..initial, problem }
		}
		compatibility = compatibility!(snapshot, cutoff)?
		$snapshots = $snapshots.append({ automation: snapshot, compatibility })
		Path.write_utf8!(Path.join(output, "${name}.json"), DownstreamStatus.badge(snapshot, latest, checked))?
		Path.write_utf8!(Path.join(output, "${name}-compatibility.json"), DownstreamStatus.compatibility_badge(compatibility, checked))?
	}
	Path.write_utf8!(Path.join(output, "index.html"), DownstreamStatus.report($snapshots, latest, checked))?
	Path.write_utf8!(Path.join(output, "snapshot.json"), Json.to_str({ checked, latest, repositories: $snapshots }))
}

compatibility! = |snapshot, cutoff| {
	fallback = DownstreamStatus.unknown(snapshot.repository, "Could not read complete shared-nightly evidence. Inspect the linked Actions page.")
	if snapshot.problem != "" {
		return Ok(fallback)
	}
	ref = match DownstreamStatus.caller(snapshot) {
		Ok(value) => value
		Err(reason) => return Ok({ ..fallback, message: reason, detail: "Adopt the pinned shared published-release nightly workflow. Pin updaters, development CI and weekly floating-input checks do not establish this compatibility result." })
	}
	runs_source = match get!("repos/${snapshot.repository}/actions/workflows/${ref.file.drop_prefix(".github/workflows/")}/runs?event=schedule&per_page=1", "application/vnd.github+json", "[.workflow_runs[] | {id, head_sha, head_branch, event, status, conclusion: (.conclusion // \"\"), updated_at}]")? {
		Ok(source) => source
		Err(_) => return Ok(fallback)
	}
	runs : Try(List(DownstreamStatus.Run), _)
	runs = Json.parse(runs_source)
	ran = match runs {
		Ok([found]) if DownstreamStatus.sha(found.head_sha) => found
		Ok([]) => return Ok({ ..fallback, message: "no scheduled result · run nightly" })
		_ => return Ok(fallback)
	}
	default_branch = match get!("repos/${snapshot.repository}", "application/vnd.github+json", ".default_branch")? {
		Ok(source) => source.trim()
		Err(_) => return Ok(fallback)
	}
	if ran.head_branch != default_branch {
		return Ok(fallback)
	}
	caller_source = match get!("repos/${snapshot.repository}/contents/${ref.file}?ref=${ran.head_sha}", "application/vnd.github.raw+json", "")? {
		Ok(source) => source
		Err(_) => return Ok(fallback)
	}
	jobs_source = match get!("repos/${snapshot.repository}/actions/runs/${ran.id.to_str()}/jobs?per_page=100", "application/vnd.github+json", "{total_count, jobs: [.jobs[] | {name, status, conclusion: (.conclusion // \"\"), steps: [(.steps // [])[] | {name, conclusion: (.conclusion // \"\")}]}]}")? {
		Ok(source) => source
		Err(_) => return Ok(fallback)
	}
	jobs : Try(DownstreamStatus.Jobs, _)
	jobs = Json.parse(jobs_source)
	match jobs {
		Ok(found) => Ok(DownstreamStatus.compatibility(snapshot, ran, found, caller_source, cutoff))
		Err(_) => Ok(fallback)
	}
}

head! = |repository| {
	answer = get!("repos/${repository}/commits?per_page=1", "application/vnd.github+json", ".[0].sha")?
	Ok(
		match answer {
			Ok(source) if DownstreamStatus.sha(source.trim()) => Ok(source.trim())
			_ => Err("default-branch head could not be read")
		},
	)
}

snapshot! = |repository| {
	head = match head!(repository)? {
		Ok(value) => value
		Err(problem) => return Ok(Err(problem))
	}
	answer = get!("repos/${repository}/git/trees/${head}?recursive=1", "application/vnd.github+json", "{truncated, tree: [.tree[] | {path, type, mode}]}")?
	paths = match answer {
		Ok(source) => match DownstreamStatus.source_paths(source) {
			Ok(found) => found
			Err(problem) => return Ok(Err(problem))
		}
		Err(problem) => return Ok(Err(problem))
	}
	var $references = []
	for path in paths {
		answer_source = get!("repos/${repository}/contents/${path}?ref=${head}", "application/vnd.github.raw+json", "")?
		source = match answer_source {
			Ok(value) => value
			Err(problem) => return Ok(Err(problem))
		}
		match DownstreamStatus.references(path, source) {
			Ok(found) => {
				$references = $references.concat(found)
			}
			Err(problem) => return Ok(Err(problem))
		}
	}
	Ok(Ok({ repository, head, references: $references, problem: "" }))
}

## No request can change a repository; credentials stay in gh's environment.
get! = |path, accept, jq| {
	output = Cmd.new("gh").args(DownstreamStatus.get_args(path, accept, jq).map(OsStr.from_str)).stdout(Capture).stderr(Capture).run!()?
	Ok(if output.status == Exited(0) Ok(Str.from_utf8_lossy(output.stdout_bytes)) else Err("GitHub metadata unavailable (request failed)"))
}
