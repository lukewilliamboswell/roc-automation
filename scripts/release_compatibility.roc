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
command! = |argv, cwd, cache, input, timeout| {
	match argv {
		[program, .. as arguments] => {
			environment : Try(List(Compatibility.Environment), _)
			environment = Json.parse(env!("RA_ENVIRONMENT")?)
			entries = environment.map_err(|_| "test environment must be a JSON array of name/value records")?
			if !Compatibility.valid_environment(entries) {
				return Err("test environment overrides a reserved variable or repeats a name")
			}
			base = Cmd.new(OsStr.from_str(program)).args(arguments.map(OsStr.from_str)).cwd(cwd).stdin(Bytes(input)).stdout(Capture).stderr(Capture).timeout_ms(timeout).output_limit(10_000_000).manage_tree(Bool.True)
				.env("GH_TOKEN", "").env("GITHUB_TOKEN", "").env("NO_COLOR", "1").env_str("XDG_CACHE_HOME", native!(cache)).env_str("ROC_CACHE_DIR", native!(cache))
			ran = entries.fold(base, |command, entry| command.env_str(entry.name, entry.value)).run!()
			Ok(
				match ran {
					Ok(value) => {
						status: match value.status {
							Exited(_) => "exited"
							Signaled(_) => "signaled"
						},
						signal: match value.status {
							Signaled(signal) => signal
							Exited(_) => 0
						},
						error: "",
						# Only the exited status carries a comparable exit code.
						code: match value.status {
							Exited(code) => code
							_ => 255
						},
						stdout: value.stdout_bytes,
						stderr: value.stderr_bytes,
						timed_out: Bool.False,
						limited: Bool.False,
					}
					Err(Timeout(partial)) => { status: "timeout", signal: 0, error: "", code: 124, stdout: partial.stdout_bytes, stderr: partial.stderr_bytes, timed_out: Bool.True, limited: Bool.False }
					Err(OutputLimit(partial)) => { status: "output-limit", signal: 0, error: "", code: 125, stdout: partial.stdout_bytes, stderr: partial.stderr_bytes, timed_out: Bool.False, limited: Bool.True }
					Err(IO(error)) => { status: "io-error", signal: 0, error: IOErr.to_str(error), code: 126, stdout: [], stderr: [], timed_out: Bool.False, limited: Bool.False }
				},
			)
		}
		[] => Err("empty command")
	}
}

logged! : Path, U64, List(Str), Path, Path, List(U8), U64 => Try({ status : Str, signal : I32, error : Str, code : I32, stdout : List(U8), stderr : List(U8), timed_out : Bool, limited : Bool }, Str)
logged! = |directory, number, argv, cwd, cache, input, timeout| {
	ran = command!(argv, cwd, cache, input, timeout)?
	prefix = "${number.to_str()}"
	write!(Path.join(directory, "${prefix}-command.json"), Json.to_str({ argv, cwd: native!(cwd), cache: native!(cache), stdin: Str.from_utf8_lossy(input), environment: env!("RA_ENVIRONMENT")?, timeout_ms: timeout, status: ran.status, signal: ran.signal, error: ran.error, code: ran.code, timed_out: ran.timed_out, limited: ran.limited }))?
	Path.write_bytes!(Path.join(directory, "${prefix}-stdout.log"), ran.stdout).map_err(|_| "cannot preserve stdout")?
	Path.write_bytes!(Path.join(directory, "${prefix}-stderr.log"), ran.stderr).map_err(|_| "cannot preserve stderr")?
	Stdout.line!("\n${Compatibility.console(Json.to_str(argv))} (${ran.status}, code ${ran.code.to_str()}, signal ${ran.signal.to_str()}: ${Compatibility.console(ran.error)})\n${Compatibility.console(Str.from_utf8_lossy(ran.stdout))}${Compatibility.console(Str.from_utf8_lossy(ran.stderr))}").map_err(|_| "cannot display diagnostics")?
	if ran.status == "io-error" {
		return Err("command ${prefix} could not complete: ${ran.error}; inspect command logs")
	}
	Ok(ran)
}

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
	version = logged!(directory, 1, [compiler, "version"], root, cache, [], 30_000)?
	if version.status != "exited" or version.code != 0 or !Str.from_utf8_lossy(version.stdout).contains(selected.nightly) {
		return Err("installed compiler identity does not match the selected nightly")
	}
	var $result = { ..initial, detail: "", outcome: "compatible" }
	var $number = 1
	for example in suite.apps {
		if !Compatibility.app_source(read!(Path.join(root, example.path))?, suite.dependencies) {
			return Err("example must directly reference the declared published bundle and must not pin its compiler")
		}
		target = example.target ?? "native"
		executable = Path.join(directory, "example-${$number.to_str()}${if target == "wasm32" ".wasm" else if initial.runner.starts_with("windows-") ".exe" else ""}")
		native_executable = native!(executable)
		native_root = native!(root)
		build_target = if target == "native" [] else ["--target=${target}"]
		var $built = Bool.True
		for argv in [[compiler, "check", example.path, "--no-cache"], [compiler, "build", example.path, "--no-cache", "--opt=dev", "--output=${native_executable}"].concat(build_target)] {
			$number = $number + 1
			ran = logged!(directory, $number, argv, root, cache, [], 300_000)?
			if ran.timed_out or ran.limited {
				return Ok({ ..$result, outcome: if ran.timed_out "test-timeout" else "output-limit", detail: "compiler interrupted; inspect command ${$number.to_str()}" })
			}
			$result = { ..$result, diagnostic_commands: $result.diagnostic_commands + 1 }
			if ran.status != "exited" or ran.code != 0 {
				$built = Bool.False
			}
		}
		if !$built {
			$result = { ..$result, failed: $result.failed + 1 }
		}
		for case in example.cases {
			match case.skip {
				Ok(skip) => {
					$result = { ..$result, skipped: $result.skipped + 1 }
					Stdout.line!("SKIP ${Compatibility.console(case.name)}: ${Compatibility.console(skip.reason)} (${Compatibility.console(skip.issue)})").map_err(|_| "cannot report skip")?
				}
				Err(Missing) if $built => {
					$number = $number + 1
					runtime = match example.run {
						Ok(argv) if !argv.is_empty() => argv.map(|arg| arg.replace_each("{executable}", native_executable).replace_each("{suite}", native_root))
						_ => [native_executable]
					}
					ran = logged!(directory, $number, runtime.concat(case.args), root, cache, Str.to_utf8(case.stdin), case_timeout)?
					if ran.timed_out or ran.limited {
						return Ok({ ..$result, outcome: if ran.timed_out "test-timeout" else "output-limit", detail: "example interrupted; inspect command ${$number.to_str()}" })
					}
					passed = ran.status == "exited" and ran.code == case.exit_code and ran.stdout == Str.to_utf8(case.stdout) and ran.stderr == Str.to_utf8(case.stderr)
					$result = { ..$result, tested: $result.tested + 1, failed: $result.failed + if passed 0 else 1 }
					if !passed {
						write!(Path.join(directory, "${$number.to_str()}-expected.json"), Json.to_str(case))?
					}
				}
				_ => {}
			}
		}
	}
	Ok({ ..$result, outcome: Compatibility.classify($result.tested, $result.failed, $result.skipped) })
}

aggregate! = || {
	selected = selection!()?
	directory = Path.utf8(env!("RA_DIRECTORY")?)
	mkdir!(directory)?
	artifacts = Path.utf8(env!("RA_ARTIFACTS")?)
	var $results = []
	for runner in selected.runners {
		parsed : Try(Compatibility.Result, _)
		parsed = match read!(Path.join(artifacts, "compatibility-${runner}/result.json")) {
			Ok(source) => Json.parse(source)
			# download-artifact flattens a pattern matching exactly one artifact.
			# Only a one-target selection can use that layout; identity and
			# current-attempt checks below remain identical.
			Err(_) if selected.runners.len() == 1 => match read!(Path.join(artifacts, "result.json")) {
				Ok(source) => Json.parse(source)
				Err(_) => Err(Missing)
			}
			Err(_) => Err(Missing)
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
