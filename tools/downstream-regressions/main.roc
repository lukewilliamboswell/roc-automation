app [main!] { cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.28.1/7brgwR8YUqfeGrkdfF1V4GPPN7DnP7ftqub9KcrTEZtr.tar.zst" }

import cli.Cmd
import cli.OsStr
import cli.Path
import cli.Stdout
import cli.Stderr
import cli.Monotonic
import cli.Duration

Case : { name : Str, args : List(Str), stdin : Str, stdout : Str, stderr : Str, exit_code : I32, skip : Str }

App : { id : Str, repository : Str, release : Str, date : Str, root : Str, path : Str, cwd : Str, cases : List(Case), note : Str, skip : Str }

Manifest : { cache : Str, output : Str, observations : Str, corpus_hash : Str, host : Str, nightlies : List({ path : Str, sha256 : Str }), apps : List(App) }

Record : { key : Str, compiler_path : Str, compiler_hash : Str, version : Str, nightly_date : Str, app_id : Str, repository : Str, release : Str, stage : Str, case_name : Str, status : Str, code : I32, signal : I32, duration_ms : U64, argv : List(Str), cwd : Str, stdin : Str, stdout : Str, stderr : Str, expected_stdout : Str, expected_stderr : Str, expected_code : I32, note : Str }

helper = "tools/downstream-regressions/support.py"

main! : List(OsStr) => Try({}, [Exit(I32), Interrupted])
main! = |arguments| {
	result = match arguments.map(OsStr.display) {
		[config] => sweep!(config)
		_ => Err("usage: roc tools/downstream-regressions/main.roc CONFIG.json (from the repository root)")
	}
	match result {
		Ok({}) => Ok({})
		Err(reason) => {
			Stderr.line!(reason) ?? {}
			Err(Exit(1))
		}
	}
}

helper! = |args, input| {
	ran = Cmd.new("python3").args([helper].concat(args).map(OsStr.from_str)).stdin(Bytes(Str.to_utf8(input))).stdout(Capture).stderr(Inherit).capture!().map_err(|_| "support helper failed; see diagnostics above")?
	Ok(ran)
}

sweep! = |config| {
	Stdout.line!("Resolving published examples…").map_err(|_| "cannot write progress")?
	parsed : Try(Manifest, _)
	parsed = Json.parse(helper!(["prepare", config], "")?)
	manifest = parsed.map_err(|_| "invalid prepared manifest")?
	observations_path = Path.from_os_str(OsStr.from_str(manifest.observations))
	saved = if Path.exists!(observations_path).map_err(|_| "cannot inspect saved observations")? {
		Path.read_utf8!(observations_path).map_err(|_| "cannot read saved observations")?
	} else {
		"[]"
	}
	prior : Try(List(Record), _)
	prior = Json.parse(saved)
	var $records = prior.map_err(|_| "invalid saved observations; move the file aside to start a new history")?
	for compiler in manifest.nightlies {
		base : Record
		base = { key: "", compiler_path: compiler.path, compiler_hash: compiler.sha256, version: "", nightly_date: "", app_id: "", repository: "", release: "", stage: "version", case_name: "", status: "", code: 0, signal: 0, duration_ms: 0, argv: [], cwd: manifest.cache, stdin: "", stdout: "", stderr: "", expected_stdout: "", expected_stderr: "", expected_code: 0, note: "" }
		identified = observe!(base, [compiler.path, "version"], manifest.cache, "", 30_000, manifest.cache)?
		version = identified.stdout.trim()
		date = nightly_date(version)
		for application in manifest.apps {
			identity = "${manifest.corpus_hash}|${manifest.host}|${compiler.path}|${compiler.sha256}|${application.id}|v1"
			seed = { ..base, key: identity, app_id: application.id, repository: application.repository, release: application.release, version, nightly_date: date, note: application.note }
			if application.skip != "" or date == "" or identified.status != "exited" or identified.code != 0 or before(date, application.date) {
				reason = if (application.skip != "") application.skip else if (date == "" or identified.status != "exited" or identified.code != 0) "compiler identity unavailable: ${identified.stderr}" else "nightly predates release"
				$records = $records.keep_if(|r| r.key != "${identity}|skip").append({ ..seed, key: "${identity}|skip", stage: "suite", status: if (reason == "nightly predates release") "not-applicable" else "skipped", note: reason })
				_ = helper!(["save", manifest.observations], Json.to_str($records))?
			} else {
				complete = ["check", "test", "build"].all(|stage| $records.any(|r| r.key == "${identity}|${stage}")) and application.cases.all(|case| ["run", "binary"].all(|stage| $records.any(|r| r.key == "${identity}|${stage}|${case.name}")))
				if !complete {
					Stdout.line!("${date} ${application.id}").map_err(|_| "cannot write progress")?
					work = "${manifest.cache}/work/${compiler.sha256}/${application.id}"
					# The helper replaces only managed working copies, never cached archives.
					_ = helper!(["copy", application.root, work, manifest.cache], "")?
					source = "${work}/${application.path}"
					cwd = "${work}/${application.cwd}"
					binary = "${work}/sweep-app"
					output_path = Path.from_os_str(OsStr.from_str(binary))
					if Path.exists!(output_path).map_err(|_| "cannot inspect build output")? {
						Path.delete!(output_path).map_err(|_| "cannot remove preexisting build output")?
					}
					var $built = Bool.False
					for stage in ["check", "test", "build"] {
						key = "${identity}|${stage}"
						found = $records.keep_if(|r| r.key == key)
						completed = match found.first() {
							Ok(record) => record
							Err(_) => {
								argv = [compiler.path, stage, "--no-cache"].concat(if (stage == "build") ["--opt=dev", "--output=${binary}"] else []).append(source)
								observed = observe!({ ..seed, key, stage }, argv, cwd, "", 300_000, "${manifest.cache}/compiler-cache/${compiler.sha256}")?
								$records = $records.append(observed)
								_ = helper!(["save", manifest.observations], Json.to_str($records))?
								observed
							}
						}
						if stage == "build" {
							# A resumed build's scratch binary was discarded; rebuild before execution.
							$built = completed.status == "exited" and completed.code == 0
							if $built and !found.is_empty() {
								rebuilt = observe!({ ..seed, key, stage }, completed.argv, cwd, "", 300_000, "${manifest.cache}/compiler-cache/${compiler.sha256}")?
								$built = rebuilt.status == "exited" and rebuilt.code == 0
								$records = $records.keep_if(|r| r.key != key).append(rebuilt)
							}
						}
					}
					for case in application.cases {
						for stage in ["run", "binary"] {
							key = "${identity}|${stage}|${case.name}"
							if !$records.any(|r| r.key == key) {
								case_seed = { ..seed, key, stage, case_name: case.name, expected_stdout: case.stdout, expected_stderr: case.stderr, expected_code: case.exit_code }
								observed = if case.skip != "" or (stage == "binary" and !$built) {
									{ ..case_seed, status: if (case.skip != "") "skipped" else "blocked", note: if (case.skip != "") case.skip else "build did not produce a successful executable" }
								} else {
									runtime = "${work}-runtime"
									_ = helper!(["copy", application.root, runtime, manifest.cache], "")?
									argv = if (stage == "run") [compiler.path, "run", "--no-cache", "--opt=dev", "${runtime}/${application.path}"].concat(case.args) else [binary].concat(case.args)
									observe!(case_seed, argv, "${runtime}/${application.cwd}", case.stdin, if (stage == "run") 300_000 else 10_000, "${manifest.cache}/compiler-cache/${compiler.sha256}")?
								}
								$records = $records.append(observed)
								_ = helper!(["save", manifest.observations], Json.to_str($records))?
							}
						}
					}
				}
			}
		}
		# Leave a usable partial report after each compiler.
		_ = helper!(["render", config, manifest.observations], "")?
	}
	_ = helper!(["render", config, manifest.observations], "")?
	Stdout.line!("Report: ${manifest.output}").map_err(|_| "cannot write progress")
}

nightly_date = |version| {
	parts = version.split_on("nightly-")
	match parts.get(1) {
		Ok(suffix) if suffix.to_utf8().len() >= 10 => Str.from_utf8_lossy(suffix.to_utf8().take_first(10))
		_ => ""
	}
}

observe! : Record, List(Str), Str, Str, U64, Str => Try(Record, Str)
observe! = |seed, argv, cwd, input, timeout, cache| {
	Path.create_all!(Path.from_os_str(OsStr.from_str(cache))).map_err(|_| "cannot create compiler cache")?
	started = Monotonic.now!()
	command = Cmd.new(OsStr.from_str(argv.first() ?? "")).args(argv.drop_first(1).map(OsStr.from_str)).cwd(Path.from_os_str(OsStr.from_str(cwd))).stdin(Bytes(input.to_utf8())).stdout(Capture).stderr(Capture).timeout(Duration.millis(timeout)).output_limit(MaxBytes(10_000_000)).manage_tree(Bool.True).env("GH_TOKEN", "").env("GITHUB_TOKEN", "").env("NO_COLOR", "1").env("ROC_CACHE_DIR", OsStr.from_str(cache)).env("XDG_CACHE_HOME", OsStr.from_str(cache))
	result = command.run!()
	duration_ms = Monotonic.now!().since(started).to_millis()
	base = { ..seed, argv, cwd, stdin: input, duration_ms }
	Ok(
		match result {
			Ok(value) => {
				..base,
				status: match value.status {
					Exited(_) => "exited"
					Signaled(_) => "signaled"
				},
				code: match value.status {
					Exited(code) => code
					_ => 0
				},
				signal: match value.status {
					Signaled(signal) => signal
					_ => 0
				},
				stdout: Str.from_utf8_lossy(value.stdout_bytes),
				stderr: Str.from_utf8_lossy(value.stderr_bytes),
			}
			Err(CmdErr(TimedOut(partial))) => { ..base, status: "timeout", stdout: Str.from_utf8_lossy(partial.stdout_bytes), stderr: Str.from_utf8_lossy(partial.stderr_bytes) }
			Err(CmdErr(OutputLimitExceeded(partial))) => { ..base, status: "output-limit", stdout: Str.from_utf8_lossy(partial.stdout_bytes), stderr: Str.from_utf8_lossy(partial.stderr_bytes) }
			Err(CmdErr(error)) => { ..base, status: "io-error", stderr: Str.inspect(error) }
			Err(Interrupted) => return Err("interrupted; completed observations are preserved")
		},
	)
}

before = |a, b| {
	aa = U64.from_str(a.replace_each("-", "")) ?? 0
	bb = U64.from_str(b.replace_each("-", "")) ?? 0
	aa < bb
}
