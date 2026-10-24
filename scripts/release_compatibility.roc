#!/usr/bin/env roc-stable
app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
	roc: "nightly-2026-10-09-258ab27",
}

import cli.Cmd
import cli.Env
import cli.IOErr
import cli.OsStr
import cli.Path
import cli.Stdout
import cli.Stderr
import src/Compatibility
import src/ExamplesEngine

## Native/source utility shared by the action. Each invocation uses an empty,
## private RA_DIRECTORY; metadata and diagnostics remain inert artifacts.
main! = |args| {
	phase = match args.map(OsStr.display) {
		[value] => value
		_ => return Err(Exit(2))
	}
	result = match phase {
		"select" => select!()
		"test" => test!()
		"aggregate" => aggregate!()
		_ => Err("expected select, test or aggregate")
	}
	match result {
		Ok({}) => Ok({})
		Err(reason) => {
			Stderr.line!("compatibility: ${Compatibility.console(reason)}")?
			Err(Exit(1))
		}
	}
}

env! = |name| Env.var_str!(OsStr.from_str(name)).map_err(|_| "missing environment ${name}")

## Path.join constructs UTF-8 paths using '/'. Convert external arguments and
## environment values to native Windows spelling, matching the child cwd and
## compiler's cache-root identity rather than mixing both separator forms.
native! = |path| Compatibility.native_path(
	Path.display(path),
	match Env.platform!().os {
		WINDOWS => Bool.True
		_ => Bool.False
	},
)

read! = |path| Path.read_utf8!(path).map_err(|_| "cannot read ${Path.display(path)}")

write! = |path, text| Path.write_utf8!(path, text).map_err(|_| "cannot write ${Path.display(path)}")

mkdir! = |path| Path.create_all!(path).map_err(|_| "cannot create ${Path.display(path)}")

output! = |text| {
	file = Path.utf8(env!("GITHUB_OUTPUT")?)
	before = read!(file)?
	write!(file, "${before}${text}")
}

## Requests are always GET. Only selection and asset download inherit GH_TOKEN.
get! = |path, jq| {
	ran = Cmd.new("gh").args(["api", "--method", "GET", path, "--jq", jq].map(OsStr.from_str)).stdout(Capture).stderr(Capture).timeout_ms(60_000).output_limit(20_000_000).manage_tree(Bool.True).run!().map_err(|_| "GitHub request timed out or could not run")?
	if ran.status == Exited(0) Ok(Str.from_utf8_lossy(ran.stdout_bytes)) else Err("GitHub could not supply release metadata")
}

select! = || {
	directory = Path.utf8(env!("RA_DIRECTORY")?)
	mkdir!(directory)?
	repository = env!("GITHUB_REPOSITORY")?
	requested = env!("RA_NIGHTLY")?
	requested_release = env!("RA_RELEASE")?
	package_name = env!("RA_PACKAGE")?
	runner_names : Try(List(Str), _)
	runner_names = Json.parse(env!("RA_RUNNERS")?)
	chosen_runners = runner_names.map_err(|_| "invalid runner JSON")?
	if !Compatibility.valid_repository(repository) or !Compatibility.path(package_name) or package_name.contains("/") or !Compatibility.valid_runners(chosen_runners) or (requested != "" and (!Compatibility.tag(requested) or !requested.starts_with("nightly-"))) or (requested_release != "" and !Compatibility.version(requested_release)) {
		return Err("invalid selection inputs")
	}
	nightly = if requested == "" get!("repos/roc-lang/nightlies/releases/latest", ".tag_name")?.trim() else requested
	var $releases = []
	var $complete = Bool.False
	pages : List(U64)
	pages = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
	for page in pages {
		if !$complete {
			source = get!("repos/${repository}/releases?per_page=100&page=${page.to_str()}", "[.[] | {id, tag_name, draft, prerelease, published_at: (.published_at // \"\"), assets: [.assets[] | {id, name, size, digest: (.digest // \"\")}]}]")?
			parsed : Try(List(Compatibility.Release), _)
			parsed = Json.parse(source)
			found = match parsed {
				Ok(value) => value
				Err(_) => return Err("invalid release metadata")
			}
			$releases = $releases.concat(found)
			$complete = found.len() < 100
		}
	}
	if !$complete {
		return Err("release listing exceeds 2000 entries; selection is incomplete")
	}
	selected = Compatibility.select(repository, nightly, requested_release, package_name, chosen_runners, $releases)?
	write!(Path.join(directory, "selection.json"), Json.to_str(selected))?
	output!("ready=true\nnightly=${selected.nightly}\nrelease=${selected.release}\nmatrix=${Json.to_str({ runner: selected.runners })}\n")
}

selection! = || {
	parsed : Try(Compatibility.Selection, _)
	parsed = Json.parse(read!(Path.utf8(env!("RA_SELECTION")?))?)
	match parsed {
		Ok(value) if value.schema_version == 1 and Compatibility.valid_repository(value.repository) and Compatibility.valid_runners(value.runners) and Compatibility.tag(value.nightly) and value.nightly.starts_with("nightly-") and Compatibility.version(value.release) and Compatibility.digest(value.asset.digest) and Compatibility.path(value.asset.name) and !value.asset.name.contains("/") and value.asset.name.ends_with(".zip") and value.asset.size > 0 and value.asset.size <= 50_000_000 => Ok(value)
		_ => Err("invalid selection artifact")
	}
}

## Child stdout/stderr are captured separately, saved byte-for-byte on every
## exit, then displayed even after success. Compiler warnings are never used
## as a substitute for its exit status.
command! = ExamplesEngine.command!
logged! = ExamplesEngine.logged!

test! = || {
	selected = selection!()?
	directory = Path.utf8(env!("RA_DIRECTORY")?)
	mkdir!(directory)?
	initial : Compatibility.Result
	initial = { schema_version: 1, repository: selected.repository, nightly: selected.nightly, release: selected.release, asset_digest: selected.asset.digest, runner: env!("RA_RUNNER")?, run_id: env!("GITHUB_RUN_ID")?, run_attempt: env!("GITHUB_RUN_ATTEMPT")?, outcome: "setup-failed", tested: 0, failed: 0, skipped: 0, diagnostic_commands: 0, detail: "setup did not complete" }
	write!(Path.join(directory, "result.json"), Json.to_str(initial))?
	finished = match execute!(selected, initial, directory) {
		Ok(value) => value
		Err(reason) => { ..initial, detail: reason }
	}
	write!(Path.join(directory, "result.json"), Json.to_str(finished))?
	Stdout.line!(Compatibility.console(Json.to_str(finished))).map_err(|_| "cannot display result")?
	if finished.outcome == "compatible" Ok({}) else Err("${finished.outcome}: ${finished.detail}")
}

execute! = |selected, initial, directory| {
	cache = Path.join(directory, "empty-cache")
	mkdir!(cache)?
	setup : Try(List(Str), _)
	setup = Json.parse(env!("RA_SETUP")?)
	setup_args = setup.map_err(|_| "setup command must be a JSON argv array")?
	if !setup_args.is_empty() {
		ran = logged!(directory, 0, setup_args, Path.utf8(env!("GITHUB_WORKSPACE")?), cache, [], 600_000)?
		if ran.status != "exited" or ran.code != 0 {
			return Err("native prerequisite setup failed; see command 0 logs")
		}
	}
	# Download by the selected asset ID, never a second moving release lookup.
	asset = Path.join(directory, selected.asset.name)
	download = Cmd.new("gh").args(["api", "--method", "GET", "-H", "Accept: application/octet-stream", "repos/${selected.repository}/releases/assets/${selected.asset.id.to_str()}"].map(OsStr.from_str)).stdout(Capture).stderr(Capture).timeout_ms(120_000).output_limit(50_000_001).manage_tree(Bool.True).run!().map_err(|_| "examples archive download failed")?
	if download.status != Exited(0) or download.stdout_bytes.len() != selected.asset.size {
		return Err("examples asset download failed or length changed")
	}
	Path.write_bytes!(asset, download.stdout_bytes).map_err(|_| "cannot save examples archive")?
	root_name = selected.asset.name.drop_suffix(".zip")
	unpacked = Path.join(directory, "unpacked")
	prepared = command!([env!("RA_PYTHON")?, env!("RA_ARCHIVE_HELPER")?, native!(asset), selected.asset.digest, root_name, native!(unpacked)], directory, cache, [], 30_000)?
	Path.write_bytes!(Path.join(directory, "archive-listing.log"), prepared.stdout.concat(prepared.stderr).concat(Str.to_utf8(prepared.error))).map_err(|_| "cannot preserve ZIP inventory")?
	if prepared.status != "exited" or prepared.code != 0 {
		return Err("examples ZIP verification or extraction failed; see archive-listing.log")
	}
	root = Path.join(unpacked, root_name)
	suite = Compatibility.suite(read!(Path.join(root, "spec.json"))?, selected)?
	compiler = env!("RA_COMPILER")?
	case_timeout = match U64.from_str(env!("RA_CASE_TIMEOUT_MS")?) {
		Ok(value) if value > 0 and value <= 120_000 => value
		_ => return Err("case timeout must be 1–120000 milliseconds")
	}
	ExamplesEngine.run!(suite, compiler, initial, root, directory, cache, [], case_timeout, Bool.True)
}

aggregate! = || {
	selected = selection!()?
	directory = Path.utf8(env!("RA_DIRECTORY")?)
	mkdir!(directory)?
	artifacts = Path.utf8(env!("RA_ARTIFACTS")?)
	var $results = []
	for runner in selected.runners {
		nested = read!(Path.join(artifacts, "compatibility-${runner}/result.json"))
		flat = read!(Path.join(artifacts, "result.json"))
		parsed : Try(Compatibility.Result, _)
		parsed = match (nested, flat) {
			# Two layouts are ambiguous, even if their bytes happen to agree.
			(Ok(_), Ok(_)) => Err(Missing)
			(Ok(source), Err(_)) => Json.parse(source)
			# download-artifact flattens a pattern matching exactly one artifact.
			# Only a one-target selection can use that layout; identity and
			# current-attempt checks below remain identical.
			(Err(_), Ok(source)) if selected.runners.len() == 1 => Json.parse(source)
			_ => Err(Missing)
		}
		match parsed {
			Ok(result) => {
				$results = $results.append(result)
			}
			Err(_) => {}
		}
	}
	classified = Compatibility.aggregate(selected, env!("GITHUB_RUN_ID")?, env!("GITHUB_RUN_ATTEMPT")?, $results)
	decision = if classified.report == "success" and env!("RA_TEST_RESULT")? != "success" { outcome: "setup-failed", report: "none" } else classified
	write!(Path.join(directory, "summary.json"), Json.to_str({ selection: selected, results: $results, outcome: decision.outcome, report: decision.report }))?
	output!("outcome=${decision.outcome}\nreport=${decision.report}\n")?
	# No example output is copied into the job summary or privileged issue job.
	summary = Path.utf8(env!("GITHUB_STEP_SUMMARY")?)
	write!(summary, "## Published-release compatibility\n\nRelease `${selected.release}` with `${selected.nightly}`: **${decision.outcome}**.\n\nSee the per-target diagnostic artifacts for commands, exit codes, stdout, stderr and expected output.\n")
}
