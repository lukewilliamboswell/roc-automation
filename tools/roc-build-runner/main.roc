# Native sandbox build runner, protocol v1. The caller supplies immutable
# recipes and tools; no caller library or controller module is imported.
app [main!] {
	pf: platform "https://github.com/roc-lang/basic-cli/releases/download/0.26.0-rc2/Gj3zVDr3qrQa6aTh7Z1zgUfe9NeBE6R76T5ho3xrc8af.tar.zst",
}

import pf.Cmd
import pf.Env
import pf.OsStr
import pf.Path
import pf.Stderr

path : Str -> Path
path = |value| Path.from_os_str(OsStr.from_str(value))

native_command! : OsStr => Cmd
native_command! = |program| Cmd.new(program)

valid_name : Str -> Bool
valid_name = |value| !value.is_empty() and value.to_utf8().all(|byte| (byte >= 'a' and byte <= 'z') or (byte >= 'A' and byte <= 'Z') or (byte >= '0' and byte <= '9') or byte == '_' or byte == '-')

within : List(U8), List(U8) -> Bool
within = |child, ancestor| {
	base = if (ancestor == ['/']) [] else ancestor
	child == ancestor or child.take_first(base.len() + 1) == base.append('/')
}

## A namespace identity as the kernel prints it, e.g. `mnt:[4026531841]`.
valid_namespace : Str, Str -> Bool
valid_namespace = |name, identity| {
	prefix = "${name}:["
	digits = identity.to_utf8().drop_first(prefix.to_utf8().len()).drop_last(1)
	identity.starts_with(prefix)
		and identity.ends_with("]")
		and !digits.is_empty()
		and digits.all(|byte| byte >= '0' and byte <= '9')
}

expect valid_namespace("mnt", "mnt:[4026531841]")
expect valid_namespace("net", "net:[0]")
expect
	["mnt:[]", "mnt:]", "net:[1]", "mnt:[1]\n", "mnt:[1] ", " mnt:[1]", "mnt:[-1]", "mnt:[1a]", "mnt:[1]]", ""].all(
		|identity| !valid_namespace("mnt", identity),
	)

## A child's namespaces are its parent's, so this `readlink` program observes
## the calling process. basic-cli cannot read a link itself.
observe_namespace! : Str, Str => Try(Str, Str)
observe_namespace! = |readlink, name| {
	output = native_command!(OsStr.from_str(readlink)).args(["/proc/self/ns/${name}"].map(OsStr.from_str)).capture!()
		.map_err(|_| "could not run readlink")?
	Ok(output.drop_suffix("\n"))
}

## The caller supplies this versioned JSON data, never a command string.
## `readlink`, `chmod` and `ln` are absolute programs: a build has no implicit
## PATH. Only a build whose environment has Roc packages names them and `ln`.
BuildSpec : {
	schema_version : U64,
	env : List({ name : Str, value : Str }),
	project : Str,
	argv : List(Str),
	output : Str,
	path : Str,
	inputs : Str,
	artifacts : Str,
	tools : Str,
	readlink : Str,
	chmod : Str,
	roc_packages : Try(List(RocPackage), [Missing]),
	ln : Try(Str, [Missing]),
}

## Absolute metadata paths are UTF-8; tree entry names stay raw Unix bytes.
absolute_path : Str -> Bool
absolute_path = |text| text.starts_with("/") and text.to_utf8().len() > 1 and !text.to_utf8().any(|byte| byte < 32 or byte == 127)

lowercase : Str -> Str
lowercase = |text| Str.from_utf8_lossy(
	text.to_utf8().map(
		|byte| if byte >= 'A' and byte <= 'Z' {
			byte + 32
		} else {
			byte
		},
	),
)

env_name : Str -> Bool
env_name = |name| {
	letter = |byte| (byte >= 'A' and byte <= 'Z') or (byte >= 'a' and byte <= 'z') or byte == '_'
	match name.to_utf8() {
		[first, .. as rest] => letter(first) and rest.all(|byte| letter(byte) or (byte >= '0' and byte <= '9'))
		[] => False
	}
}

validate_spec : BuildSpec -> Try({}, _)
validate_spec = |spec| {
	if spec.schema_version != 1 {
		return Err(Invalid("unsupported build specification schema_version; expected 1"))
	}
	_ = output_parts(spec.output)?
	match spec.argv {
		[] => return Err(Invalid("empty build command"))
		[first, ..] if first.is_empty() => return Err(Invalid("empty build program"))
		_ => {}
	}
	if spec.argv.any(|arg| arg.to_utf8().contains(0)) or spec.path.to_utf8().contains(0) {
		return Err(Invalid("NUL in build command or PATH"))
	}
	if ![spec.project, spec.inputs, spec.artifacts, spec.tools, spec.readlink, spec.chmod].all(absolute_path) {
		return Err(Invalid("project, farms and helper programs must be absolute paths"))
	}
	var $names = []
	for variable in spec.env {
		name = lowercase(variable.name)
		if !env_name(variable.name) or variable.value.to_utf8().contains(0) {
			return Err(Invalid("invalid environment binding"))
		}
		if ["path", "home", "xdg_cache_home", "out", "runnerspecpath"].contains(name) or name.starts_with("roc_build_runner_") {
			return Err(Invalid("reserved environment binding: ${variable.name}"))
		}
		if $names.contains(name) {
			return Err(Invalid("duplicate environment binding: ${variable.name}"))
		}
		$names = $names.append(name)
	}
	var $bundles = []
	for bundle in spec.roc_packages ?? [] {
		if !valid_name(bundle.name) or !absolute_path(bundle.path) or $bundles.contains(bundle.name) {
			return Err(Invalid("invalid or duplicate Roc package"))
		}
		$bundles = $bundles.append(bundle.name)
	}
	if !$bundles.is_empty() and !(spec.ln.map_ok(absolute_path) ?? False) {
		return Err(Invalid("Roc packages require an absolute ln program"))
	}
	Ok({})
}

## A released Roc bundle: the content hash Roc looks for in its package cache
## and the directory holding the unpacked bundle.
RocPackage : { name : Str, path : Str }

## The builder of a sandboxed build: exact argv and one contained,
## symlink-free output. The specification is the given file, or the one the
## caller passes as `runnerSpecPath`. Only local Linux is admitted.
main! : List(OsStr) => Try({}, [Exit(I32)])
main! = |args| {
	result = match args {
		[] =>
			match Env.var!(OsStr.from_str("runnerSpecPath")) {
				Ok(file) => run_build!(Path.from_os_str(file))
				Err(_) => Err(Invalid("missing build specification"))
			}

		[file] => run_build!(Path.from_os_str(file))
		_ => Err(Invalid("expected one build specification"))
	}
	match result {
		Ok({}) => Ok({})
		Err(Exited(code)) => Err(Exit(code))
		Err(Invalid(message)) => {
			_ = Stderr.line!("roc-build-runner: ${message}")
			Err(Exit(1))
		}
		Err(other) => {
			_ = Stderr.line!("roc-build-runner: ${Str.inspect(other)}")
			Err(Exit(1))
		}
	}
}

run_build! : Path => Try({}, _)
run_build! = |file| {
	# No specification is interpreted, workspace created, or recipe started
	# until both independent Linux namespace checks have succeeded.
	require_isolation!()?
	text = file.read_utf8!()?
	parsed : Try(BuildSpec, _)
	parsed = Json.parse(text)
	spec = parsed.map_err(|_| Invalid("invalid build specification"))?
	validate_spec(spec)?
	out = Env.var_str!("out").map_err(|_| Invalid("the build has no $out"))?
	if !absolute_path(out) {
		return Err(Invalid("$out must be an absolute path"))
	}
	destination = path(out)
	source = path(spec.project)
	if !(source.is_dir!() ?? False) {
		return Err(Invalid("project must be a directory"))
	}
	safe_tree!(source)?
	check_inputs!(spec.readlink, path(spec.inputs))?
	check_inputs!(spec.readlink, path(spec.artifacts))?
	if path(spec.tools).type!() != Ok(IsDir) {
		return Err(Invalid("expected a regular tool package farm directory"))
	}
	for bundle in spec.roc_packages ?? [] {
		if path(bundle.path).join("main.roc").type!() != Ok(IsFile) {
			return Err(Invalid("Roc package ${bundle.name} has no regular main.roc"))
		}
	}
	top = Env.cwd!()?
	if within(top.canonicalize!()?.to_os_str().to_bytes(), source.canonicalize!()?.to_os_str().to_bytes()) {
		return Err(Invalid("working directory must be outside the project source"))
	}
	if (destination.is_sym_link!() ?? False) or (destination.exists!() ?? False) {
		return Err(Invalid("$out already exists before the build"))
	}
	work = top.join("runner-work")
	work.create_dir!()?
	copy_tree!(source, work)?
	# Store files are read-only; the project copy is the build's to change.
	native_command!(OsStr.from_str(spec.chmod)).args(["-R", "u+w", "--"].map(OsStr.from_str)).arg(work.to_os_str()).exec!()?
	home = top.join("runner-home")
	home.create_dir!()?
	cache = roc_build_cache!(spec.roc_packages, spec.ln, top)?
	match spec.argv {
		[] => return Err(Invalid("empty build command"))
		[program, .. as rest] => {
			# A bare program name resolves against the PATH given here.
			var $base = native_command!(OsStr.from_str(program)).args(rest.map(OsStr.from_str)).cwd(work).clear_envs()
				.env(OsStr.from_str("PATH"), OsStr.from_str(spec.path))
				.env(OsStr.from_str("HOME"), home.to_os_str())
				.env(OsStr.from_str("out"), destination.to_os_str())
			for variable in spec.env {
				$base = $base.env(OsStr.from_str(variable.name), OsStr.from_str(variable.value))
			}
			# Only the user command sees the cache, and only when there is one.
			command = match cache {
				Private(directory) => $base.env(OsStr.from_str("XDG_CACHE_HOME"), directory.to_os_str())
				NoCache => $base
			}
			ran = command.stdout(Inherit).stderr(Inherit).run!()
			match ran {
				Ok(Cmd.Output.{ status: Exited(0), .. }) => {}
				Ok(Cmd.Output.{ status: Exited(code), .. }) => return Err(Exited(code))
				Ok(Cmd.Output.{ status: Signaled(signal), .. }) => return Err(Exited(128 + signal))
				Err(CmdErr(StartFailed(NotFound, _))) => return Err(Invalid("build command not found: ${program}"))
				Err(CmdErr(StartFailed(err, _))) => return Err(Invalid("cannot run ${program}: ${Str.inspect(err)}"))
				Err(_) => return Err(Invalid("cannot run ${program}"))
			}
		}
	}
	parts = output_parts(spec.output)?
	if (work.is_sym_link!() ?? False) or !(work.is_dir!() ?? False) {
		return Err(Invalid("build replaced the project workspace"))
	}
	# basic-cli has no no-follow open, so a build still running in the
	# background could swap an entry between these checks and the copy.
	var $depth = parts.len()
	while $depth > 0 {
		ancestor = work.join(Str.join_with(parts.take_first($depth), "/"))
		if ancestor.is_sym_link!() ?? False {
			return Err(Invalid("symlink in declared output path: ${ancestor.display()}"))
		}
		$depth = $depth - 1
	}
	output = work.join(spec.output)
	if !(output.exists!() ?? False) {
		return Err(Invalid("declared output is missing: ${spec.output}"))
	}
	if !within(
		output.canonicalize!()?.to_os_str().to_bytes(),
		work.canonicalize!()?.to_os_str().to_bytes(),
	) {
		return Err(Invalid("declared output escapes the project workspace"))
	}
	safe_tree!(output)?

	if (destination.is_sym_link!() ?? False) or (destination.exists!() ?? False) {
		return Err(Invalid("build wrote directly to $out instead of declared Output"))
	}
	if output.is_dir!()? {
		destination.create_dir!()?
		copy_tree!(output, destination)
	} else {
		output.copy!(destination)?
		Ok({})
	}
}

## A build has no network, so Roc cannot download a package there. Give the
## build a cache of its own holding its environment's locked bundles: Roc reads
## `$XDG_CACHE_HOME/roc/packages/<hash>/main.roc` and does not download what it
## finds. Links suffice, because the cache is discarded with the build
## directory and the store cannot be collected while the build runs. basic-cli
## cannot create a link itself.
roc_build_cache! : Try(List(RocPackage), [Missing]), Try(Str, [Missing]), Path => Try([Private(Path), NoCache], _)
roc_build_cache! = |declared, ln, top| {
	bundles = declared ?? []
	if bundles.is_empty() {
		return Ok(NoCache)
	}
	program = ln.map_err(|_| Invalid("the build has Roc packages but no ln program"))?
	cache = top.join("runner-cache")
	directory = cache.join("roc").join("packages")
	directory.create_all!()?
	for bundle in bundles {
		if !valid_name(bundle.name) {
			return Err(Invalid("invalid Roc package name: ${bundle.name}"))
		}
		if !absolute_path(bundle.path) or path(bundle.path).join("main.roc").type!() != Ok(IsFile) {
			return Err(Invalid("Roc package ${bundle.name} has no main.roc"))
		}
		native_command!(OsStr.from_str(program)).args(["-s", "--", bundle.path].map(OsStr.from_str))
			.arg(directory.join(bundle.name).to_os_str())
			.exec!()
			.map_err(|_| Invalid("cannot link Roc package ${bundle.name}"))?
	}
	Ok(Private(cache))
}

## Reject Run even when the daemon ignores client sandbox flags: the build's
## namespaces must both differ from those the caller observed for itself.
require_isolation! : () => Try({}, _)
require_isolation! = || {
	if Env.platform!().os != LINUX {
		return Err(Invalid("${isolation_remedy} This runner requires local Linux namespaces."))
	}
	location = Env.var_str!("ROC_BUILD_RUNNER_WITNESS").map_err(|_| Invalid("${isolation_remedy} Missing sandbox witness."))?
	if !absolute_path(location) {
		return Err(Invalid("${isolation_remedy} Invalid sandbox witness path."))
	}
	witness = path(location)
	if witness.type!() != Ok(IsFile) {
		return Err(Invalid("${isolation_remedy} Invalid sandbox witness."))
	}
	lines = witness.read_utf8!().map_err(|_| Invalid("${isolation_remedy} Missing sandbox witness."))?.split_on("\n")
	caller = match lines {
		[mnt, net, ""] => { mnt, net }
		_ => return Err(Invalid("${isolation_remedy} Invalid sandbox witness."))
	}
	readlink = Env.var_str!("ROC_BUILD_RUNNER_READLINK")
	require_namespace!(readlink, "mnt", caller.mnt)?
	require_namespace!(readlink, "net", caller.net)
}

isolation_remedy : Str
isolation_remedy =
	"cannot verify build isolation; user Run was not executed. "
		.concat("Enable sandbox = true and sandbox-fallback = false in the Nix ")
		.concat("daemon configuration and use a local Linux sandbox with /proc.")

require_namespace! : Try(Str, _), Str, Str => Try({}, _)
require_namespace! = |readlink, name, observed| {
	if !valid_namespace(name, observed) {
		return Err(Invalid("${isolation_remedy} Invalid caller ${name} namespace."))
	}
	current = match readlink {
		Ok(program) => if absolute_path(program) {
			observe_namespace!(program, name)
		} else {
			Err("readlink must be absolute")
		}
		Err(_) => Err("no readlink program")
	}
		.map_err(|reason| Invalid("${isolation_remedy} Cannot read build ${name} namespace: ${reason}"))?
	if !valid_namespace(name, current) {
		return Err(Invalid("${isolation_remedy} Invalid build ${name} namespace."))
	}
	if current == observed {
		return Err(Invalid("${isolation_remedy} Build shares caller ${name} namespace."))
	}
	Ok({})
}

## A declared output is a relative path of plain names.
output_parts : Str -> Try(List(Str), _)
output_parts = |output| {
	parts = output.split_on("/")
	if output.to_utf8().any(|byte| byte < 32 or byte == 127 or byte == '\\' or byte == ':') or parts.any(|part| part == "" or part == "." or part == "..") {
		Err(Invalid("invalid declared relative output"))
	} else {
		Ok(parts)
	}
}

expect output_parts("dist/my artifact") == Ok(["dist", "my artifact"])
expect
	["", "/absolute", ".", "..", "./artifact", "dist/../escape", "dist//file", "dist/"].all(
		|output| output_parts(output) == Err(Invalid("invalid declared relative output")),
	)

## Reject links and special files anywhere in a tree, by raw entry names.
safe_tree! : Path => Try({}, _)
safe_tree! = |entry|
	match entry.type!()? {
		IsSymLink => Err(Invalid("symlink is not allowed in build output/source: ${entry.display()}"))
		IsDir => {
			for child in entry.list!()? {
				safe_tree!(child)?
			}
			Ok({})
		}
		IsFile => Ok({})
		IsOther => Err(Invalid("special file is not allowed in build output/source: ${entry.display()}"))
	}

## Allow generated farm links, not symlinks within fetched source trees.
check_inputs! : Str, Path => Try({}, _)
check_inputs! = |readlink, farm| {
	if farm.type!() != Ok(IsDir) {
		return Err(Invalid("expected a regular input farm directory"))
	}
	for entry in farm.list!()? {
		if !(entry.is_sym_link!()?) {
			return Err(Invalid("expected generated source link: ${entry.display()}"))
		}
		target = native_command!(OsStr.from_str(readlink)).args(["-n", "--"].map(OsStr.from_str)).arg(entry.to_os_str())
			.run!().map_err(|err| Invalid(Str.inspect(err)))?.require_success()
			.map_err(|_| Invalid("cannot read generated source link: ${entry.display()}"))?
			.stdout_bytes
		# Follow exactly the planner's farm link. safe_tree! rejects a symlink
		# at the fetched root or anywhere beneath it, including remote inputs.
		resolved = if target.first() == Ok('/') {
			target
		} else {
			farm.to_os_str().to_bytes().append('/').concat(target)
		}
		safe_tree!(Path.unix_bytes(resolved))?
	}
	Ok({})
}

basename : List(U8) -> List(U8)
basename = |bytes| bytes.fold([], |acc, byte| if (byte == '/') [] else acc.append(byte))

expect basename("/project/src/main.roc".to_utf8()) == "main.roc".to_utf8()

## Copy the directories and regular files of a tree safe_tree! accepted into
## an existing directory, by raw entry names. Files keep their permissions.
copy_tree! : Path, Path => Try({}, _)
copy_tree! = |source, target| {
	for entry in source.list!()? {
		name = basename(entry.to_os_str().to_bytes())
		copy = Path.unix_bytes(target.to_os_str().to_bytes().append('/').concat(name))
		if entry.is_dir!()? {
			copy.create_dir!()?
			copy_tree!(entry, copy)?
		} else {
			entry.copy!(copy)?
		}
	}
	Ok({})
}

sample : BuildSpec
sample = {
	schema_version: 1,
	project: "/immutable/project",
	argv: ["python3", "recipe.py", "", "two words", "Unicode λ 日本語", "literal $HOME", "line\nbreak"],
	output: "dist/artifact",
	path: "/immutable/tools/bin",
	inputs: "/immutable/inputs",
	artifacts: "/immutable/artifacts",
	tools: "/immutable/tools",
	readlink: "/immutable/helpers/readlink",
	chmod: "/immutable/helpers/chmod",
	env: [{ name: "INPUTS", value: "/immutable/inputs" }],
	roc_packages: Err(Missing),
	ln: Err(Missing),
}

expect validate_spec(sample) == Ok({})
expect validate_spec({ ..sample, schema_version: 2 }).is_err()
expect validate_spec({ ..sample, argv: [] }).is_err()
expect validate_spec({ ..sample, argv: [""] }).is_err()
expect validate_spec({ ..sample, argv: ["tool", Str.from_utf8_lossy([0])] }).is_err()
expect validate_spec({ ..sample, path: Str.from_utf8_lossy([0]) }).is_err()
expect ["", "relative/path", "/", "/contains\nnewline", "/nul\u(0000)byte"].all(|value| !absolute_path(value))
expect absolute_path("/immutable/path with spaces")
expect ["NAME", "_NAME", "name_2", "x"].all(env_name)
expect ["", "2NAME", "A=B", "A-B", "A B", "λ", "PATH\u(0000)"].all(|name| !env_name(name))
expect ["PATH", "Path", "HOME", "home", "XDG_CACHE_HOME", "out", "OUT", "runnerSpecPath", "ROC_BUILD_RUNNER_READLINK", "roc_build_runner_witness", "ROC_BUILD_RUNNER_FUTURE"].all(|name| validate_spec({ ..sample, env: [{ name, value: "unexpected" }] }).is_err())
expect validate_spec({ ..sample, env: [{ name: "A", value: "one" }, { name: "A", value: "two" }] }).is_err()
expect validate_spec({ ..sample, env: [{ name: "A", value: "one" }, { name: "a", value: "two" }] }).is_err()
expect validate_spec({ ..sample, env: [{ name: "A", value: "\u(0000)" }] }).is_err()
expect validate_spec({ ..sample, env: [{ name: "A", value: "Unicode λ; literal $HOME\nnewline" }] }) == Ok({})
expect ["project", "inputs", "artifacts", "tools", "readlink", "chmod"].all(
	|name| {
		changed = match name {
			"project" => { ..sample, project: "relative" }
			"inputs" => { ..sample, inputs: "relative" }
			"artifacts" => { ..sample, artifacts: "relative" }
			"tools" => { ..sample, tools: "relative" }
			"readlink" => { ..sample, readlink: "relative" }
			_ => { ..sample, chmod: "relative" }
		}
		validate_spec(changed).is_err()
	},
)
expect ["C:/escape", "dir\\escape", "out\nfile", "nul\u(0000)file"].all(|output| output_parts(output).is_err())
expect output_parts("dist/λ 日本語") == Ok(["dist", "λ 日本語"])
expect within("/project/artifact".to_utf8(), "/project".to_utf8())
expect within("/project".to_utf8(), "/project".to_utf8())
expect within("/project".to_utf8(), "/".to_utf8())
expect !within("/project-other/artifact".to_utf8(), "/project".to_utf8())
expect basename(['/', 'x', '/', 0xff, 0xfe]) == [0xff, 0xfe]
expect {
	bundle = { name: "Hash123", path: "/immutable/package" }
	validate_spec({ ..sample, roc_packages: Ok([bundle]), ln: Ok("/immutable/ln") }) == Ok({})
		and validate_spec({ ..sample, roc_packages: Ok([bundle]), ln: Err(Missing) }).is_err()
		and validate_spec({ ..sample, roc_packages: Ok([bundle]), ln: Ok("relative") }).is_err()
		and validate_spec({ ..sample, roc_packages: Ok([bundle, bundle]), ln: Ok("/immutable/ln") }).is_err()
		and validate_spec({ ..sample, roc_packages: Ok([{ ..bundle, name: "../escape" }]), ln: Ok("/immutable/ln") }).is_err()
		and validate_spec({ ..sample, roc_packages: Ok([{ ..bundle, path: "relative" }]), ln: Ok("/immutable/ln") }).is_err()
}

# JSON is a data protocol: argument Unicode, empty values and line breaks are
# passed to exec unchanged, without shell expansion or whitespace splitting.
expect {
	encoded = "{\"schema_version\":1,\"project\":\"/immutable/project\",\"argv\":[\"tool\",\"\",\"two words\",\"λ 日本語\",\"literal $HOME\",\"line\\nbreak\"],\"output\":\"dist/artifact\",\"path\":\"/tools/bin\",\"inputs\":\"/inputs\",\"artifacts\":\"/artifacts\",\"tools\":\"/tools\",\"readlink\":\"/bin/readlink\",\"chmod\":\"/bin/chmod\",\"env\":[]}"
	parsed : Try(BuildSpec, _)
	parsed = Json.parse(encoded)
	match parsed {
		Ok(spec) => spec.argv == ["tool", "", "two words", "λ 日本語", "literal $HOME", "line\nbreak"] and validate_spec(spec) == Ok({}) and spec.roc_packages == Err(Missing)
		Err(_) => False
	}
}
