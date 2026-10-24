import cli.Cmd
import cli.Env
import cli.IOErr
import cli.OsStr
import cli.Path
import cli.Stdout
import Compatibility

## One bounded compiler/examples engine for working-tree, candidate and published
## suites. Callers own immutable source/archive admission and dependency routes.
ExamplesEngine := [].{
	run! = |suite, compiler, initial, root, directory, cache, replace_args, timeout, published| run_examples!(suite, compiler, initial, root, directory, cache, replace_args, timeout, published, directory)
	run_with_outputs! = run_examples!
	command! = run_command!
	logged! = log_command!
}

env! = |name| Env.var_str!(OsStr.from_str(name)).map_err(|_| "missing environment ${name}")

native! = |path| Compatibility.native_path(
	Path.display(path),
	match Env.platform!().os {
		WINDOWS => Bool.True
		_ => Bool.False
	},
)

read! = |path| Path.read_utf8!(path).map_err(|_| "cannot read example")

write! = |path, text| Path.write_utf8!(path, text).map_err(|_| "cannot write diagnostic")

run_command! = |argv, cwd, cache, input, timeout| {
	match argv {
		[program, .. as arguments] => {
			environment : Try(List(Compatibility.Environment), _)
			environment = Json.parse(Env.var_str!("RA_ENVIRONMENT") ?? "[]")
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

log_command! : Path, U64, List(Str), Path, Path, List(U8), U64 => Try({ status : Str, signal : I32, error : Str, code : I32, stdout : List(U8), stderr : List(U8), timed_out : Bool, limited : Bool }, Str)
log_command! = |directory, number, argv, cwd, cache, input, timeout| {
	ran = run_command!(argv, cwd, cache, input, timeout)?
	prefix = "${number.to_str()}"
	write!(Path.join(directory, "${prefix}-command.json"), Json.to_str({ argv, cwd: native!(cwd), cache: native!(cache), stdin: Str.from_utf8_lossy(input), environment: Env.var_str!("RA_ENVIRONMENT") ?? "[]", timeout_ms: timeout, status: ran.status, signal: ran.signal, error: ran.error, code: ran.code, timed_out: ran.timed_out, limited: ran.limited }))?
	Path.write_bytes!(Path.join(directory, "${prefix}-stdout.log"), ran.stdout).map_err(|_| "cannot preserve stdout")?
	Path.write_bytes!(Path.join(directory, "${prefix}-stderr.log"), ran.stderr).map_err(|_| "cannot preserve stderr")?
	Stdout.line!("\n${Compatibility.console(Json.to_str(argv))} (${ran.status}, code ${ran.code.to_str()}, signal ${ran.signal.to_str()}: ${Compatibility.console(ran.error)})\n${Compatibility.console(Str.from_utf8_lossy(ran.stdout))}${Compatibility.console(Str.from_utf8_lossy(ran.stderr))}").map_err(|_| "cannot display diagnostics")?
	if ran.status == "io-error" {
		return Err("command ${prefix} could not complete: ${ran.error}; inspect command logs")
	}
	Ok(ran)
}

run_examples! : Compatibility.Suite, Str, Compatibility.Result, Path, Path, Path, List(Str), U64, Bool, Path => Try(Compatibility.Result, Str)
run_examples! = |suite, compiler, initial, root, directory, cache, replace_args, case_timeout, published, executable_directory| {
	version = log_command!(directory, 1, [compiler, "version"], root, cache, [], 30_000)?
	if version.status != "exited" or version.code != 0 or !Compatibility.compiler_matches(Str.from_utf8_lossy(version.stdout), initial.nightly) {
		return Err("installed compiler identity does not match the selected nightly")
	}
	var $result = { ..initial, detail: "", outcome: "compatible" }
	var $number = 1
	for example in suite.apps {
		if published and !Compatibility.app_source(read!(Path.join(root, example.path))?, suite.dependencies) {
			return Err("example must directly reference the declared published bundle and must not pin its compiler")
		}
		target = example.target ?? "native"
		executable = Path.join(executable_directory, "example-${$number.to_str()}${if target == "wasm32" ".wasm" else if initial.runner.starts_with("windows-") ".exe" else ""}")
		native_executable = native!(executable)
		native_root = native!(root)
		build_target = if target == "native" [] else ["--target=${target}"]
		var $built = Bool.True
		for argv in [[compiler, "check", example.path, "--no-cache"].concat(replace_args), [compiler, "build", example.path, "--no-cache", "--opt=dev", "--output=${native_executable}"].concat(build_target).concat(example.build_args ?? []).concat(replace_args)] {
			$number = $number + 1
			ran = log_command!(directory, $number, argv, root, cache, [], 300_000)?
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
					ran = log_command!(directory, $number, runtime.concat(case.args), root, cache, Str.to_utf8(case.stdin), case_timeout)?
					if ran.timed_out or ran.limited {
						return Ok({ ..$result, outcome: if ran.timed_out "test-timeout" else "output-limit", detail: "example interrupted; inspect command ${$number.to_str()}" })
					}
					passed = ran.status == "exited" and ran.code == case.exit_code and ran.stdout == Str.to_utf8(case.stdout) and ((case.stderr_match ?? "exact") == "ignore" or ran.stderr == Str.to_utf8(case.stderr))
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
