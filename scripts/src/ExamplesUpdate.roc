import cli.Env
import cli.Path
import cli.Stdout
import Compatibility
import ExamplesEngine
import LifecycleJson
import docs.BuildFiles
import docs.Sha256

## Explicit local editing only. Release/candidate execution never calls this API.
ExamplesUpdate := [].{
	run! = update!
}

update! = |relative, root_text| {
	ci = Env.var_str!("CI") ?? ""
	github = Env.var_str!("GITHUB_ACTIONS") ?? ""
	if truthy(ci) or truthy(github) {
		return Err("expectation update is local only; CI and GITHUB_ACTIONS must be unset or false")
	}
	root = Path.canonicalize!(Path.utf8(root_text)).map_err(|_| "cannot resolve local suite root")?
	if Path.type!(root).map_err(|_| "cannot inspect suite root")? != IsDir {
		return Err("suite root must be a directory")
	}
	spec = BuildFiles.inside!(root, relative)?
	if Path.type!(spec).map_err(|_| "cannot inspect local suite spec")? != IsFile {
		return Err("local suite spec must be a regular file")
	}
	original = BuildFiles.read!(spec, 1_048_576)?
	text = Str.from_utf8(original).map_err(|_| "suite spec must be UTF-8")?
	tree = LifecycleJson.decode_limit(text, 1_048_576).map_err(LifecycleJson.message)?
	_ = strict(tree)?
	parsed : Try(Compatibility.Suite, _)
	parsed = Json.parse(text)
	suite = Compatibility.validate_apps(parsed.map_err(|_| "invalid suite schema 1")?)?
	if suite.release != "working-tree" {
		return Err("local update requires release working-tree; published suites are immutable")
	}
	compiler_text = Env.var_str!("RO_COMPILER").map_err(|_| "local update requires explicit RO_COMPILER")?
	compiler_path = Path.canonicalize!(Path.utf8(compiler_text)).map_err(|_| "cannot resolve RO_COMPILER")?
	compiler = native!(compiler_path)
	directory = Env.create_temp_dir_with_prefix!("roc-examples-update-").map_err(|_| "cannot create update diagnostic directory")?
	Stdout.line!("Local update diagnostics: ${native!(directory)}").map_err(|_| "cannot display diagnostic directory")?
	cache = Path.join(directory, "cache")
	Path.create_all!(cache).map_err(|_| "cannot create isolated update cache")?
	identity = ExamplesEngine.command!([compiler, "version"], root, cache, [], 30_000)?
	if identity.status != "exited" or identity.code != 0 {
		return Err("selected local compiler could not report its version")
	}
	version = (Str.from_utf8(identity.stdout).map_err(|_| "compiler version is not UTF-8")?.split_on("\n").first() ?? "").trim()
	selected = version.split_on(" ").find_first(|word| word.starts_with("nightly-")).map_err(|_| "compiler version must identify the selected nightly")?
	initial : Compatibility.Result
	initial = {
		schema_version: 1,
		repository: "local/examples",
		nightly: selected,
		release: suite.release,
		asset_digest: "local",
		runner: match Env.platform!().os {
			WINDOWS => "windows-local"
			_ => "local"
		},
		run_id: "local",
		run_attempt: "1",
		outcome: "setup-failed",
		tested: 0,
		failed: 0,
		skipped: 0,
		diagnostic_commands: 0,
		detail: "",
	}
	observed = ExamplesEngine.observe!(suite, compiler, initial, root, directory, cache, 10_000)?
	if !["compatible", "not-tested"].contains(observed.result.outcome) or observed.result.failed != 0 or observed.result.tested == 0 {
		return Err("no expectations changed: every executed case must pass status, exit, crash and output checks; at least one must execute")
	}
	updated = rewrite(tree, observed.observed)?
	# Child processes may edit inputs. Re-resolve all components and compare type
	# and complete original bytes immediately before either a no-op or commit.
	_ = unchanged!(root, relative, original)?
	if updated == tree {
		Stdout.line!("Expectations unchanged.").map_err(|_| "cannot display update result")?
		return Ok({})
	}
	encoded = "${pretty(updated, 0)}\n"
	if encoded.to_utf8().len() > 1_048_576 {
		return Err("updated spec exceeds the 1 MiB input budget")
	}
	# A private sibling directory keeps the final rename on the same filesystem.
	# The original spec is untouched until all validation succeeds.
	staging = Env.create_temp_dir_in!(BuildFiles.parent_path(spec), ".roc-expectations-").map_err(|_| "cannot create atomic update staging directory")?
	temporary = Path.join(staging, "suite.json")
	Path.write_utf8!(temporary, encoded).map_err(|_| "cannot write staged expectations")?
	_ = unchanged!(root, relative, original)?
	Path.rename!(temporary, spec).map_err(|_| "cannot atomically replace local suite spec")?
	Path.delete_all!(staging).map_err(|_| "cannot remove expectation staging directory")?
	Stdout.line!("Updated local stdout/stderr expectations.").map_err(|_| "cannot display update result")?
	Ok({})
}

truthy = |text| !["", "0", "false", "False", "FALSE"].contains(text.trim())

native! = |path| Compatibility.native_path(
	Path.display(path),
	match Env.platform!().os {
		WINDOWS => Bool.True
		_ => Bool.False
	},
)

unchanged! = |root, relative, original| {
	path = BuildFiles.inside!(root, relative)?
	if Path.type!(path).map_err(|_| "suite spec disappeared during execution")? != IsFile or Sha256.hex(BuildFiles.read!(path, 1_048_576)?) != Sha256.hex(original) {
		return Err("suite spec changed during execution; refusing stale expectation update")
	}
	Ok({})
}

allowed = |tree, names| match tree {
	LifecycleJson.Object(fields) if fields.all(|field| names.contains(field.name)) => Ok({})
	_ => Err("local update refuses unknown fields or non-object suite metadata")
}

strict = |tree| {
	_ = allowed(tree, ["schema_version", "release", "dependencies", "apps"])?
	for example in LifecycleJson.array(LifecycleJson.field(tree, "apps")?)? {
		_ = allowed(example, ["path", "build_args", "target", "run", "cases"])?
		for case in LifecycleJson.array(LifecycleJson.field(example, "cases")?)? {
			_ = allowed(case, ["name", "args", "stdin", "stdout", "stdout_match", "stderr", "stderr_match", "exit_code", "skip"])?
			match LifecycleJson.field(case, "skip") {
				Ok(skip) => {
					_ = LifecycleJson.object(skip, ["reason", "issue"])?
				}
				Err(_) => {}
			}
		}
	}
	Ok({})
}

rewrite = |tree, observed| {
	apps = LifecycleJson.array(LifecycleJson.field(tree, "apps")?)?
	var $apps = []
	for example in apps {
		path = LifecycleJson.field(example, "path")?
		cases = LifecycleJson.array(LifecycleJson.field(example, "cases")?)?
		var $cases = []
		for case in cases {
			name = LifecycleJson.field(case, "name")?
			found = observed.find_first(|entry| path == LifecycleJson.String(entry.path) and name == LifecycleJson.String(entry.name))
			match found {
				Ok(entry) => {
					expected = LifecycleJson.field(case, "stdout")?
					matches = match expected {
						LifecycleJson.String(value) if LifecycleJson.field(case, "stdout_match") == Ok(LifecycleJson.String("strip-sgr-and-cr")) => ExamplesEngine.normalize(value.to_utf8()) == entry.stdout.to_utf8()
						_ => expected == LifecycleJson.String(entry.stdout)
					}
					changed = if matches case else replace(case, "stdout", LifecycleJson.String(entry.stdout))
					$cases = $cases.append(if LifecycleJson.field(case, "stderr_match") == Ok(LifecycleJson.String("ignore")) changed else replace(changed, "stderr", LifecycleJson.String(entry.stderr)))
				}
				Err(_) => {
					$cases = $cases.append(case)
				}
			}
		}
		$apps = $apps.append(replace(example, "cases", LifecycleJson.Array($cases)))
	}
	Ok(replace(tree, "apps", LifecycleJson.Array($apps)))
}

replace = |tree, name, value| match tree {
	LifecycleJson.Object(fields) => LifecycleJson.Object(fields.map(|field| if field.name == name { ..field, value } else field))
	_ => tree
}

pretty : LifecycleJson, U64 -> Str
pretty = |tree, depth| match tree {
	LifecycleJson.Object(fields) => if fields.is_empty() "{}" else "{\n${Str.join_with(fields.map(|field| "${Str.repeat("  ", depth + 1)}${Json.to_str(field.name)}: ${pretty(field.value, depth + 1)}"), ",\n")}\n${Str.repeat("  ", depth)}}"
	LifecycleJson.Array(items) => if items.is_empty() "[]" else "[\n${Str.join_with(items.map(|item| "${Str.repeat("  ", depth + 1)}${pretty(item, depth + 1)}"), ",\n")}\n${Str.repeat("  ", depth)}]"
	LifecycleJson.String(value) => Json.to_str(value)
	LifecycleJson.Number(value) => value
	LifecycleJson.Boolean(value) => if value "true" else "false"
	LifecycleJson.Null => "null"
}
