#!/usr/bin/env roc-stable
app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
	docs: "../tools/roc-docs/release.roc",
	roc: "nightly-2026-10-09-258ab27",
}

import cli.Cmd
import cli.Env
import cli.IOErr
import cli.OsStr
import cli.Path
import cli.Sleep
import cli.Tcp
import cli.Stdout
import cli.Stderr
import src/ToolVersion
import src/Compatibility
import src/LifecyclePlan
import src/ReleaseArchive
import src/ReleaseMetadata
import src/ExamplesEngine
import docs.BuildFiles
import docs.Sha256

## Candidate-only release effects. This executable runs with read-only authority;
## its JSON records and files are inert inputs to independent admission.
main! = |arguments| {
	if arguments.map(OsStr.display) == ["--version"] {
		Stdout.line!(ToolVersion.json("release-operations"))?
		return Ok({})
	}
	args = arguments.map(OsStr.display)
	result = match args {
		["source"] => execute!("source")
		["validate"] => execute!("validate")
		["host"] => execute!("host")
		["seal"] => execute!("seal")
		["prepare"] => execute!("prepare")
		["candidate"] => execute!("candidate")
		["serve", directory, port_file] => serve!(Path.utf8(directory), Path.utf8(port_file))
		_ => Err("expected validate, host, source, prepare, seal, candidate or internal serve DIRECTORY PORT_FILE")
	}
	match result {
		Ok({}) => Ok({})
		Err(message) => {
			Stderr.line!("release operations: ${Compatibility.console(message)}")?
			Err(Exit(1))
		}
	}
}

env! = |name| Env.var_str!(OsStr.from_str(name)).map_err(|_| "missing environment ${name}")

optional! = |name, fallback| Env.var_str!(OsStr.from_str(name)) ?? fallback

native! = |path| Compatibility.native_path(
	Path.display(path),
	match Env.platform!().os {
		WINDOWS => Bool.True
		_ => Bool.False
	},
)

write! = |path, text| Path.write_utf8!(path, text).map_err(|e| Str.inspect(e))

mkdir! = |path| Path.create_all!(path).map_err(|e| Str.inspect(e))

read! = |path| Str.from_utf8(BuildFiles.read!(path, 16_777_216)?).map_err(|_| "input is not UTF-8")

plan! = || {
	parsed : Try(LifecyclePlan.Plan, _)
	parsed = Json.parse(read!(Path.utf8(env!("RO_PLAN")?))?)
	found = parsed.map_err(|_| "invalid release plan")?
	if found.schema != 1 {
		return Err("unsupported release plan schema")
	}
	_ = LifecyclePlan.parse_context(Json.to_str(found.context))?
	Ok(found)
}

BundleFile : { id : Str, path : Str, url : Str, sha256 : Str, size : U64 }

Previous : { first_release : Bool, bundles : List({ id : Str, url : Str }) }

FileRecord : { path : Str, role : Str, subject : Str, target : Str, size : U64, sha256 : Str }

ObligationRecord : { id : Str, outcome : Str }

execute! = |stage| {
	plan = plan!()?
	_ = LifecyclePlan.validate(plan.topology)?
	workspace = Path.utf8(optional!("RO_WORKSPACE", env!("GITHUB_WORKSPACE")?))
	directory = Path.utf8(env!("RO_DIRECTORY")?)
	assets = Path.utf8(optional!("RO_CANDIDATE", native!(directory)))
	runner = env!("RO_RUNNER")?
	compiler = env!("RO_COMPILER")?
	mkdir!(directory)?
	mkdir!(assets)?
	diagnostics = Path.join(directory, "diagnostics")
	mkdir!(diagnostics)?
	emit_output!("diagnostics=${native!(diagnostics)}\n")?
	match Env.var_str!("RO_CANDIDATE") {
		Ok(_) => {
			scratch_path = native!(Path.canonicalize!(directory).map_err(|e| Str.inspect(e))?).replace_each("\\", "/")
			asset_path = native!(Path.canonicalize!(assets).map_err(|e| Str.inspect(e))?).replace_each("\\", "/")
			left = Compatibility.upper(scratch_path)
			right = Compatibility.upper(asset_path)
			if left == right or left.starts_with("${right}/") or right.starts_with("${left}/") {
				return Err("explicit evidence and candidate directories must not overlap")
			}
		}
		Err(_) => {}
	}
	version = capture!(directory, "compiler-version", [compiler, "version"], workspace, [], [], 30_000)?
	if !Compatibility.compiler_matches(version, plan.context.compiler) {
		return Err("installed compiler differs from the plan")
	}
	head = capture!(directory, "event-checkout", ["git", "rev-parse", "HEAD"], workspace, [], [], 30_000)?.trim()
	if head != plan.context.sha {
		return Err("checkout differs from the planned event source")
	}
	selected = plan.obligations.keep_if(|o| o.stage == stage and o.runner == runner)
	if selected.is_empty() {
		return Err("no selected obligations for this stage and runner")
	}
	previous = if stage == "validate" previous!(plan)? else { first_release: Bool.True, bundles: [] }
	if stage == "validate" {
		notes = read!(BuildFiles.inside!(workspace, "docs/releases/${release_version(plan)}.adoc")?)?
		_ = validate_notes(plan.topology, release_version(plan), notes)?
	}
	var $records = []
	if stage == "prepare" {
		prepare!(plan, workspace, directory, assets, compiler)?
	}
	if stage == "seal" {
		seal!(plan, workspace, directory, assets, version)?
	}
	for obligation in selected {
		match obligation.operation {
			"version-bump" => {
				if !previous.first_release {
					bundle = plan.topology.bundles.find_first(|entry| entry.id == obligation.subject).map_err(|_| "unknown version obligation")?
					prior = previous.bundles.find_first(|entry| entry.id == bundle.id).map_err(|_| "previous release evidence is incomplete")?
					base_version = release_version(plan).split_on("-").first() ?? release_version(plan)
					_ = capture!(directory, obligation.id, [compiler, "bump", "--old", prior.url, "--expect", base_version, bundle.root], workspace, [], [], 300_000)?
				}
			}
			"policy" => {
				_ = capture!(directory, obligation.id, [env!("RO_POLICY")?, "check", "--root", native!(workspace)], workspace, [], [], 300_000)?
			}
			"format" => {
				var $roots = plan.topology.bundles.map(|bundle| if parent(bundle.root) == "" "." else parent(bundle.root))
				$roots = Compatibility.unique($roots.concat(plan.topology.examples.map(|example| example.directory)))
				_ = capture!(directory, obligation.id, [compiler, "fmt", "--check"].concat($roots), workspace, [], [], 300_000)?
			}
			"unit" | "source" | "native" | "build-host" | "test-host" | "check-bindings" | "runtime" => {
				adapter = if ["build-host", "test-host", "check-bindings", "runtime"].contains(obligation.operation) plan.topology.adapters.find_first(|entry| entry.id == obligation.subject) else Err(NotFound)
				outputs = match adapter {
					Ok(value) => value.outputs
					Err(_) => []
				}
				target_ids = if obligation.target != "" [obligation.target] else match adapter {
					Ok(value) => value.target_ids
					Err(_) => []
				}
				output_directory = if obligation.operation == "build-host" workspace else Path.join(directory, "adapter-${obligation.id}")
				mkdir!(output_directory)?
				variables = [
					{ name: "RO_TARGET", value: obligation.target },
					{ name: "RO_TARGET_IDS", value: Json.to_str(target_ids) },
					{ name: "RO_OUTPUTS", value: Json.to_str(outputs) },
					{ name: "RO_COMPILER", value: compiler },
					{ name: "RO_OUTPUT_DIRECTORY", value: native!(output_directory) },
				]
				_ = capture_with!(directory, obligation.id, [optional!("RO_BLUEPRINT", "blueprint"), "run", obligation.task], workspace, [], [], 600_000, variables)?
				if obligation.operation == "build-host" {
					builder_adapter = plan.topology.adapters.find_first(|value| value.id == obligation.subject).map_err(|_| "unknown adapter")?
					for output in builder_adapter.outputs {
						copy_file!(workspace, output.path, assets, output.path)?
					}
				}
			}
			"working-examples" | "archive-examples" => {
				example = plan.topology.examples.find_first(|value| value.id == obligation.subject).map_err(|_| "unknown examples obligation")?
				test_examples!(plan, example, obligation, workspace, directory, assets, compiler, stage == "candidate")?
			}
			"bundle" | "api" | "docs" | "package-examples" if stage == "prepare" => {}
			"notes" if stage == "seal" => {}
			_ => return Err("unsupported operation ${obligation.operation}; no success recorded")
		}
		$records = $records.append({ id: obligation.id, outcome: "passed" })
	}
	files = produced_files!(plan, directory, assets, stage, selected)?
	trace = Path.join(directory, "${stage}-trace.json")
	write!(trace, Json.to_str({ schema_version: 1, profile: "roc-automation-lifecycle-trace-v1", repository: plan.context.repository, source_sha: plan.context.sha, run_id: plan.context.run_id, run_attempt: plan.context.run_attempt, stage, runner, obligations: $records, files }))?
	match Env.var_str!("GITHUB_OUTPUT") {
		Ok(output) => {
			old = Path.read_utf8!(Path.utf8(output)).map_err(|e| Str.inspect(e))?
			write!(Path.utf8(output), "${old}trace=${native!(trace)}\ndiagnostics=${native!(diagnostics)}\nfiles=${Json.to_str(files)}\n")
		}
		Err(_) => Ok({})
	}
}

release_version = |plan| if plan.context.event == "pull_request" "0.0.1-dry-run" else plan.context.version

examples_name = |plan, example| if plan.topology.examples.len() == 1 "${plan.topology.name}-examples-${release_version(plan)}" else "${plan.topology.name}-${example.id}-examples-${release_version(plan)}"

docs_name = |plan, doc| if plan.topology.documentation.len() == 1 "${plan.topology.name}-docs-${release_version(plan)}" else "${plan.topology.name}-${doc.id}-docs-${release_version(plan)}"

pdf_name = |plan, doc| if plan.topology.documentation.len() == 1 "${plan.topology.name}-${release_version(plan)}.pdf" else "${plan.topology.name}-${doc.id}-${release_version(plan)}.pdf"

parent = |path| Str.join_with(path.split_on("/").drop_last(1), "/")

file_name = |path| path.split_on("/").last() ?? path

copy_file! = |source_root, source, destination_root, destination| {
	from = BuildFiles.inside!(source_root, source)?
	bytes = BuildFiles.read!(from, 50_000_000)?
	BuildFiles.write_bytes!(destination_root, destination, bytes)
}

read_bundles! = |plan, directory, assets| {
	parsed : Try(List(BundleFile), _)
	parsed = Json.parse(read!(Path.utf8(optional!("RO_BUNDLE_MAP", native!(Path.join(directory, "bundle-map.json")))))?)
	found = parsed.map_err(|_| "invalid bundle-map.json")?
	if found.len() != plan.topology.bundles.len() or Compatibility.unique(found.map(|file| file.id)).len() != found.len() {
		return Err("incomplete or duplicate bundle map")
	}
	for file in found {
		declared = plan.topology.bundles.find_first(|bundle| bundle.id == file.id).map_err(|_| "unknown mapped bundle")?
		url = "https://github.com/${plan.context.repository}/releases/download/${release_version(plan)}/${file.path}"
		if !file.path.starts_with("${declared.prefix}-") or !file.path.ends_with(".tar.zst") or file.path.contains("/") or file.url != url {
			return Err("bundle map has wrong filename or release URL")
		}
		bytes = BuildFiles.read!(BuildFiles.inside!(assets, file.path)?, 50_000_000)?
		if file.size != bytes.len() or file.sha256 != Sha256.hex(bytes) {
			return Err("bundle map no longer describes the candidate bytes")
		}
	}
	Ok(found)
}

## Replace only declared placeholder literals and working-tree bundle paths.
## Other dependencies and every expected-output byte remain unchanged.
relative = |source, target| {
	left = parent(source).split_on("/").keep_if(|part| part != "")
	right = target.split_on("/")
	var $common = 0
	for i in 0..<(if left.len() < right.len() left.len() else right.len()) {
		if left.get(i) == right.get(i) and $common == i {
			$common = $common + 1
		}
	}
	Str.join_with(List.repeat("..", left.len() - $common).concat(right.drop_first($common)), "/")
}

rewrite = |text, source, topology, bundles| bundles.fold(
	text,
	|current, file| {
		declared = topology.bundles.find_first(|bundle| bundle.id == file.id)
		match declared {
			Ok(bundle) => current.replace_each("\"{${bundle.placeholder}}\"", "\"${file.url}\"").replace_each("\"${relative(source, bundle.root)}\"", "\"${file.url}\"")
			Err(_) => current
		}
	},
)

rewrite_placeholders = |text, topology, bundles| bundles.fold(
	text,
	|current, file| {
		match topology.bundles.find_first(|bundle| bundle.id == file.id) {
			Ok(bundle) => current.replace_each("{${bundle.placeholder}}", file.url)
			Err(_) => current
		}
	},
)

prepare! = |plan, workspace, directory, assets, compiler| {
	stage = Path.join(directory, "work")
	if Path.exists!(stage).map_err(|e| Str.inspect(e))? {
		return Err("preparation workspace already exists; candidate assets build once")
	}
	mkdir!(stage)?
	paths = stage_tracked!(workspace, stage, directory, "tracked-files")?
	for adapter in plan.topology.adapters {
		for output in adapter.outputs {
			# Host artifacts have been transported back into the exact checkout paths.
			copy_file!(workspace, output.path, stage, output.path)?
			copy_file!(workspace, output.path, assets, output.path)?
		}
	}
	cache = Path.join(directory, "bundle-cache")
	mkdir!(cache)?
	var $bundles = []
	for id in plan.bundle_order {
		bundle = plan.topology.bundles.find_first(|entry| entry.id == id).map_err(|_| "unknown bundle order")?
		for path in paths.keep_if(|path| path.ends_with(".roc")) {
			original = read!(BuildFiles.inside!(workspace, path)?)?
			BuildFiles.write_text!(stage, path, rewrite(original, path, plan.topology, $bundles))?
		}
		root_directory = if parent(bundle.root) == "" stage else Path.join(stage, parent(bundle.root))

		out = Path.join(directory, "bundle-${id}")
		mkdir!(out)?
		modules = Path.list!(root_directory).map_err(|e| Str.inspect(e))?.map(|path| Path.filename(path).map_ok(Path.display) ?? "").keep_if(|name| name.ends_with(".roc") and name != file_name(bundle.root)).sort_with(ReleaseArchive.compare)
		prefix = if parent(bundle.root) == "" "" else "${parent(bundle.root)}/"
		native_inputs = plan.topology.adapters.fold([], |all, adapter| all.concat(adapter.outputs.map(|output| output.path))).keep_if(|path| prefix == "" or path.starts_with(prefix)).map(|path| path.drop_prefix(prefix))
		inputs = Compatibility.unique(modules.concat(native_inputs)).sort_with(ReleaseArchive.compare)
		_ = capture!(directory, "bundle-${id}", [compiler, "bundle", file_name(bundle.root)].concat(inputs).concat(["--output-dir", native!(out)]), root_directory, [native!(cache)], [], 300_000)?
		produced = Path.list!(out).map_err(|e| Str.inspect(e))?.keep_if(|path| Path.display(path).ends_with(".tar.zst"))
		file = match produced {
			[one] => one
			_ => return Err("bundle must produce exactly one archive")
		}
		bare = Path.filename(file).map_ok(Path.display).map_err(|_| "bundle filename is unavailable")?
		if !Compatibility.path(bare) or bare.contains("/") {
			return Err("unsafe bundle filename")
		}
		bytes = BuildFiles.read!(file, 50_000_000)?
		name = "${bundle.prefix}-${bare}"
		BuildFiles.write_bytes!(assets, name, bytes)?
		$bundles = $bundles.append({ id, path: name, url: "https://github.com/${plan.context.repository}/releases/download/${release_version(plan)}/${name}", size: bytes.len(), sha256: Sha256.hex(bytes) })
		# Roc's content-addressed cache admits the bare hash archive; it never
		# needs to fetch an unpublished sibling URL when bundling its dependent.
		package_cache = Path.join(cache, "roc/packages")
		mkdir!(package_cache)?
		_ = capture!(directory, "unbundle-${id}", [compiler, "unbundle", native!(file)], package_cache, [native!(cache)], [], 120_000)?
	}
	write!(Path.utf8(optional!("RO_BUNDLE_MAP", native!(Path.join(directory, "bundle-map.json")))), Json.to_str($bundles))?
	for bundle in plan.topology.bundles {
		api = Path.join(Path.join(directory, "api"), bundle.id)
		mkdir!(Path.join(directory, "api"))?
		_ = capture!(directory, "api-${bundle.id}", [compiler, "docs", bundle.root, "--output=${native!(api)}"], stage, [native!(cache)], [], 300_000)?
		_ = BuildFiles.read!(BuildFiles.inside!(api, "index.html")?, 16_777_216)?
		if plan.topology.documentation.is_empty() {
			root = "${bundle.prefix}-docs-${release_version(plan)}"
			zip_tree!(api, assets, root, "${root}.zip")?
		}
	}
	for example in plan.topology.examples {
		package_examples!(plan, example, workspace, directory, assets, $bundles)?
	}
	for path in paths.keep_if(|path| path.ends_with(".adoc") or path.ends_with(".md")) {
		original = read!(BuildFiles.inside!(workspace, path)?)?
		BuildFiles.write_text!(stage, path, rewrite_placeholders(original, plan.topology, $bundles))?
	}
	for doc in plan.topology.documentation {
		output = Path.join(stage, ".release-docs-${doc.id}")
		_ = capture!(directory, "docs-${doc.id}", [env!("RO_DOCS")?, "build", "--workspace", native!(stage), "--docs-directory", doc.directory, "--entrypoint", "index.adoc", "--output-directory", ".release-docs-${doc.id}", "--docs-version", release_version(plan), "--pdf-filename", "manual.pdf"], stage, [], [], 300_000)?
		site = Path.join(output, "site")
		for bundle in plan.topology.bundles {
			api = Path.join(Path.join(directory, "api"), bundle.id)
			for file in BuildFiles.list!(api)? {
				path = "api/${bundle.id}/${file.path}"
				if Path.exists!(BuildFiles.inside!(site, path)?).map_err(|e| Str.inspect(e))? {
					return Err("manual conflicts with generated API path ${path}")
				}
				copy_file!(api, file.path, site, path)?
			}
		}
		root = docs_name(plan, doc)
		zip_tree!(Path.join(output, "site"), assets, root, "${root}.zip")?
		if doc.pdf {
			copy_file!(output, "manual.pdf", assets, pdf_name(plan, doc))?
		}
	}
	Ok({})
}

stage_tracked! = |workspace, stage, directory, id| {
	listed = capture!(directory, id, ["git", "ls-files", "--cached", "-z"], workspace, [], [], 30_000)?
	paths = listed.split_on("\u(0000)").keep_if(|path| path != "")
	if paths.len() > 10000 {
		return Err("release source inventory exceeds file limit")
	}
	var $total = 0
	for path in paths {
		input = BuildFiles.inside!(workspace, path)?
		bytes = BuildFiles.read!(input, 50_000_000)?
		$total = $total + bytes.len()
		if $total > 200_000_000 {
			return Err("release workspace exceeds byte limit")
		}
		BuildFiles.write_bytes!(stage, path, bytes)?
	}
	Ok(paths)
}

zip_tree! = |source, directory, root, filename| {
	var $entries = []
	for entry in BuildFiles.list!(source)? {
		$entries = $entries.append({ path: "${root}/${entry.path}", bytes: BuildFiles.read!(entry.absolute, 50_000_000)? })
	}
	bytes = ReleaseArchive.pack($entries)?
	if Path.exists!(Path.join(directory, filename)).map_err(|e| Str.inspect(e))? {
		return Err("release asset already exists: ${filename}")
	}
	BuildFiles.write_bytes!(directory, filename, bytes)
}

suite! = |workspace, example| {
	parsed : Try(Compatibility.Suite, _)
	parsed = Json.parse(read!(BuildFiles.inside!(workspace, example.spec)?)?)
	Compatibility.validate_apps(parsed.map_err(|_| "invalid examples specification")?)
}

package_examples! = |plan, example, workspace, directory, assets, bundles| {
	suite = suite!(workspace, example)?
	root_name = examples_name(plan, example)
	stage = Path.join(directory, root_name)
	mkdir!(stage)?
	for entry in BuildFiles.list!(BuildFiles.inside!(workspace, example.directory)?)? {
		source = "${example.directory}/${entry.path}"
		bytes = BuildFiles.read!(entry.absolute, 16_777_216)?
		out = if entry.path.ends_with(".roc") Str.to_utf8(rewrite(Str.from_utf8(bytes).map_err(|_| "Roc example must be UTF-8")?, source, plan.topology, bundles)) else bytes
		BuildFiles.write_bytes!(stage, "examples/${entry.path}", out)?
	}
	urls = bundles.keep_if(|file| example.bundles.contains(file.id)).map(|file| file.url)
	published = { ..suite, release: release_version(plan), dependencies: urls }
	write!(Path.join(stage, "spec.json"), Json.to_str(published))?
	write!(Path.join(stage, "README.md"), "# ${plan.topology.name} ${release_version(plan)} examples\n\nDependencies:\n\n${Str.join_with(urls, "\n")}\n")?
	zip_tree!(stage, assets, root_name, "${root_name}.zip")
}

## Bounded, descendant-managed execution retains diagnostics on every exit and
## clears ambient child tokens. Cache routing is explicit and never global.
capture! = |directory, id, argv, cwd, cache, input, timeout| capture_with!(directory, id, argv, cwd, cache, input, timeout, [])

capture_with! : Path, Str, List(Str), Path, List(Str), List(U8), U64, List(Compatibility.Environment) => Try(Str, Str)
capture_with! = |directory, id, argv, cwd, cache, input, timeout, variables| {
	match argv {
		[program, .. as args] => {
			base = Cmd.new(OsStr.from_str(program)).args(args.map(OsStr.from_str)).cwd(cwd).stdin(Bytes(input)).stdout(Capture).stderr(Capture).timeout_ms(timeout).output_limit(10_000_000).manage_tree(Bool.True).env("GH_TOKEN", "").env("GITHUB_TOKEN", "").env("NO_COLOR", "1")
			command = match cache {
				[] => base
				[path] => base.env_str("ROC_CACHE_DIR", path).env_str("XDG_CACHE_HOME", path)
				_ => return Err("invalid cache route")
			}
			ran = variables.fold(command, |value, entry| value.env_str(entry.name, entry.value)).run!()
			(stdout, stderr, status) = match ran {
				Ok(value) => (value.stdout_bytes, value.stderr_bytes, if value.status == Exited(0) "passed" else "failed")
				Err(Timeout(partial)) => (partial.stdout_bytes, partial.stderr_bytes, "timeout")
				Err(OutputLimit(partial)) => (partial.stdout_bytes, partial.stderr_bytes, "output-limit")
				Err(IO(error)) => ([], Str.to_utf8(IOErr.to_str(error)), "io-error")
			}
			Path.write_bytes!(Path.join(Path.join(directory, "diagnostics"), "${id}-stdout.log"), stdout).map_err(|e| Str.inspect(e))?
			Path.write_bytes!(Path.join(Path.join(directory, "diagnostics"), "${id}-stderr.log"), stderr).map_err(|e| Str.inspect(e))?
			write!(Path.join(Path.join(directory, "diagnostics"), "${id}-command.json"), Json.to_str({ argv, cwd: native!(cwd), status, context: variables }))?
			Stdout.line!("${Compatibility.console(Json.to_str(argv))}\n${Compatibility.console(Str.from_utf8_lossy(stdout))}${Compatibility.console(Str.from_utf8_lossy(stderr))}").map_err(|e| Str.inspect(e))?
			if status != "passed" {
				return Err("${id}: ${status}; see retained diagnostics")
			}
			Ok(Str.from_utf8_lossy(stdout))
		}
		[] => Err("empty command")
	}
}

serve! = |directory, port_file| {
	listener = Tcp.listen!("127.0.0.1", 0, 5_000).map_err(|e| Str.inspect(e))?
	port = listener.local_port!().map_err(|e| Str.inspect(e))?
	write!(port_file, port.to_str())?
	for _ in 0..<10000 {
		stream = listener.accept!(60_000).map_err(|e| Str.inspect(e))?
		request = stream.read_line!(8192, 5_000).map_err(|e| Str.inspect(e))?
		match request.trim().split_on(" ") {
			["GET", name, _] if name.starts_with("/") and !name.drop_prefix("/").contains("/") and Compatibility.path(name.drop_prefix("/")) => {
				file = BuildFiles.inside!(directory, name.drop_prefix("/"))?
				bytes = BuildFiles.read!(file, 50_000_000)?
				stream.write_utf8!("HTTP/1.0 200 OK\r\nContent-Length: ${bytes.len().to_str()}\r\nContent-Type: application/octet-stream\r\n\r\n", 5_000).map_err(|e| Str.inspect(e))?
				stream.write!(bytes, 30_000).map_err(|e| Str.inspect(e))?
			}
			_ => {
				stream.write_utf8!("HTTP/1.0 404 Not Found\r\nContent-Length: 0\r\n\r\n", 5_000).map_err(|e| Str.inspect(e))?
			}
		}
	}
	listener.close!().map_err(|e| Str.inspect(e))
}

produced_files! = |plan, directory, assets, stage, selected| {
	if stage == "validate" or stage == "source" or stage == "candidate" {
		return Ok([])
	}
	if stage == "host" {
		return Ok(plan.topology.adapters.keep_if(|adapter| selected.any(|o| o.subject == adapter.id)).fold([], |all, adapter| all.concat(adapter.outputs.map(|output| output.path))))
	}
	bundles = read_bundles!(plan, directory, assets)?
	var $files = []
	for rule in plan.assets {
		if stage == "seal" or ["bundle", "examples", "documentation", "pdf", "host", "binary"].contains(rule.role) {
			path = resolved_path(rule, bundles)?
			_ = BuildFiles.read!(BuildFiles.inside!(assets, path)?, 50_000_000)?
			$files = $files.append(path)
		}
	}
	Ok($files)
}

resolved_path = |rule, bundles| {
	if !rule.path.contains("{hash}") {
		return Ok(rule.path)
	}
	bundle = bundles.find_first(|file| file.id == rule.subject).map_err(|_| "asset has no resolved bundle")?
	base = rule.path.split_on(".tar.zst").first() ?? ""
	suffix = rule.path.drop_prefix("${base}.tar.zst")
	Ok("${bundle.path}${suffix}")
}

seal! : LifecyclePlan.Plan, Path, Path, Path, Str => Try({}, Str)
seal! = |plan, workspace, directory, assets, package_compiler_output| {
	bundles = read_bundles!(plan, directory, assets)?
	if Path.exists!(Path.join(assets, "release-notes.md")).map_err(|e| Str.inspect(e))? {
		return Err("release notes already sealed")
	}
	var $files = []
	for rule in plan.assets.keep_if(|rule| rule.role != "notes" and rule.role != "inventory") {
		path = resolved_path(rule, bundles)?
		bytes = BuildFiles.read!(BuildFiles.inside!(assets, path)?, 50_000_000)?
		$files = $files.append({ path, role: rule.role, subject: rule.subject, target: rule.target, size: bytes.len(), sha256: Sha256.hex(bytes) })
	}
	notes_version = if plan.context.event == "pull_request" latest_notes!(workspace)? else release_version(plan)
	notes_path = "docs/releases/${notes_version}.adoc"
	original_notes = read!(BuildFiles.inside!(workspace, notes_path)?)?
	_ = validate_notes(plan.topology, notes_version, original_notes)?
	resolved_notes = rewrite_placeholders(original_notes, plan.topology, bundles)
	if !unresolved_urls(resolved_notes).is_empty() {
		return Err("release notes retain an unresolved bundle URL placeholder")
	}
	tooling_compiler_output = env!("RO_TOOLING_COMPILER_VERSION")?.trim()
	if tooling_compiler_output == "" {
		return Err("the observed shared utility compiler version is missing")
	}
	stage = Path.join(directory, "notes-work")
	if Path.exists!(stage).map_err(|e| Str.inspect(e))? {
		return Err("release notes staging already exists")
	}
	mkdir!(stage)?
	_ = stage_tracked!(workspace, stage, directory, "notes-files")?
	BuildFiles.write_text!(stage, notes_path, resolved_notes)?
	output_name = ".release-notes-${plan.context.run_id}-${plan.context.run_attempt}"
	output = Path.join(stage, output_name)
	_ = capture!(directory, "release-notes", [env!("RO_DOCS")?, "build", "--workspace", native!(stage), "--docs-directory", "docs/releases", "--entrypoint", "${notes_version}.adoc", "--output-directory", output_name, "--docs-version", release_version(plan), "--markdown", "true"], stage, [], [], 300_000)?
	authored = read!(BuildFiles.inside!(output, "site/index.md")?)?
	if !unresolved_urls(authored).is_empty() {
		return Err("rendered notes retain an unresolved bundle URL placeholder")
	}
	urls = bundles.map(|file| "- ${file.id}: ${file.url}")
	digests = $files.map(|file| "${file.sha256}  ${file.path}")
	notes = "${authored}\n\nSource: `${plan.context.sha}`.\n\n## Compilers\n\nPackage compiler:\n\n```text\n${package_compiler_output.trim()}\n```\n\nShared utility build compiler:\n\n```text\n${tooling_compiler_output}\n```\n\n${Str.join_with(urls, "\n")}\n\nSHA-256 of the tested release files:\n\n```\n${Str.join_with(digests, "\n")}\n```\n"
	if Path.exists!(Path.join(assets, "release-notes.md")).map_err(|e| Str.inspect(e))? {
		return Err("release notes already sealed")
	}
	write!(Path.join(assets, "release-notes.md"), notes)?
	$files = $files.append({ path: "release-notes.md", role: "notes", subject: "", target: "source", size: notes.to_utf8().len(), sha256: Sha256.hex(notes.to_utf8()) })
	write!(Path.join(assets, "release-inventory.json"), Json.to_str({ schema: 1, context: plan.context, version: release_version(plan), bundles, files: $files }))
}

latest_notes! = |workspace| {
	path = BuildFiles.inside!(workspace, "docs/releases")?
	versions = Path.list!(path).map_err(|e| Str.inspect(e))?.map(|entry| Path.filename(entry).map_ok(Path.display) ?? "").keep_if(|name| name.ends_with(".adoc") and LifecyclePlan.version(name.drop_suffix(".adoc"))).map(|name| name.drop_suffix(".adoc"))
	if versions.is_empty() {
		return Err("dry run requires authored release notes")
	}
	ordered = versions.sort_with(|a, b| ReleaseMetadata.compare(b, a))
	ordered.first().map_err(|_| "no authored release notes")
}

test_examples! = |plan, example, obligation, workspace, directory, assets, compiler, archived| {
	work = Path.join(directory, obligation.id)
	if Path.exists!(work).map_err(|e| Str.inspect(e))? {
		return Err("examples obligation already executed")
	}
	mkdir!(work)?
	cache = Path.join(work, "empty-cache")
	mkdir!(cache)?
	original = suite!(workspace, example)?
	if !archived {
		# The declared directory is admitted as a complete bounded regular tree.
		_ = BuildFiles.list!(BuildFiles.inside!(workspace, example.directory)?)?
		working = { ..original, apps: original.apps.map(|application| { ..application, path: "${example.directory}/${application.path.drop_prefix("examples/")}" }) }
		return run_suite!(plan, obligation, working, compiler, workspace, work, cache, [], Bool.False)
	}
	bundles = read_bundles!(plan, directory, assets)?
	archive_name = "${examples_name(plan, example)}.zip"
	archive = BuildFiles.inside!(assets, archive_name)?
	bytes = BuildFiles.read!(archive, 50_000_000)?
	unpacked = Path.join(work, "unpacked")
	root_name = examples_name(plan, example)
	_ = capture!(directory, "${obligation.id}-extract", [env!("RA_PYTHON")?, env!("RA_ARCHIVE_HELPER")?, native!(archive), "sha256:${Sha256.hex(bytes)}", root_name, native!(unpacked)], workspace, [], [], 120_000)?
	root = Path.join(unpacked, root_name)
	parsed : Try(Compatibility.Suite, _)
	parsed = Json.parse(read!(Path.join(root, "spec.json"))?)
	suite = Compatibility.validate_apps(parsed.map_err(|_| "invalid archived suite")?)?
	urls = bundles.keep_if(|file| example.bundles.contains(file.id)).map(|file| file.url)
	if suite != { ..original, release: release_version(plan), dependencies: urls } {
		return Err("archive specification differs from working-tree expectations")
	}
	source_files = BuildFiles.list!(BuildFiles.inside!(workspace, example.directory)?)?
	archived_files = BuildFiles.list!(BuildFiles.inside!(root, "examples")?)?
	if source_files.map(|file| file.path) != archived_files.map(|file| file.path) {
		return Err("archive source file inventory differs")
	}
	for file in source_files {
		source = BuildFiles.read!(file.absolute, 16_777_216)?
		expected = if file.path.ends_with(".roc") Str.to_utf8(rewrite(Str.from_utf8(source).map_err(|_| "invalid Roc UTF-8")?, "${example.directory}/${file.path}", plan.topology, bundles)) else source
		actual = BuildFiles.read!(BuildFiles.inside!(root, "examples/${file.path}")?, 16_777_216)?
		if actual != expected {
			return Err("archive changed bytes outside declared bundle URL substitution: ${file.path}")
		}
	}
	port_file = Path.join(work, "server-port")
	server = Cmd.new(OsStr.from_str(env!("RO_SELF")?)).args(["serve", native!(assets), native!(port_file)].map(OsStr.from_str)).stdout(Capture).stderr(Capture).timeout_ms(900_000).manage_tree(Bool.True).env("GH_TOKEN", "").env("GITHUB_TOKEN", "").spawn!().map_err(|e| Str.inspect(e))?
	result = served_examples!(plan, obligation, example, suite, compiler, root, work, cache, bundles, port_file)
	server.close!().map_err(|e| Str.inspect(e))?
	result
}

served_examples! = |plan, obligation, example, suite, compiler, root, work, cache, bundles, port_file| {
	var $ready = Bool.False
	for _ in 0..<100 {
		if Path.exists!(port_file).map_err(|e| Str.inspect(e))? {
			$ready = Bool.True
		}
		if !$ready {
			Sleep.millis!(10)
		}
	}
	if !$ready {
		return Err("loopback server did not start")
	}
	port = U16.from_str(read!(port_file)?).map_err(|_| "invalid loopback server port")?
	routes = bundles.keep_if(|file| example.bundles.contains(file.id)).fold([], |all, file| all.concat(["--replace-dep", file.url, "http://127.0.0.1:${port.to_str()}/${file.path}"]))
	run_suite!(plan, obligation, suite, compiler, root, work, cache, routes, Bool.True)
}

run_suite! = |plan, obligation, suite, compiler, root, work, cache, routes, published| {
	initial : Compatibility.Result
	initial = { schema_version: 1, repository: plan.context.repository, nightly: plan.context.compiler, release: release_version(plan), asset_digest: "sha256:${Str.repeat("0", 64)}", runner: obligation.runner, run_id: plan.context.run_id, run_attempt: plan.context.run_attempt, outcome: "setup-failed", tested: 0, failed: 0, skipped: 0, diagnostic_commands: 0, detail: "" }
	logs = Path.join(Path.join(Path.utf8(env!("RO_DIRECTORY")?), "diagnostics"), obligation.id)
	mkdir!(logs)?
	executables = Path.join(work, "executables")
	mkdir!(executables)?
	result = ExamplesEngine.run_with_outputs!(suite, compiler, initial, root, logs, cache, routes, 10_000, published, executables)?
	write!(Path.join(logs, "result.json"), Json.to_str(result))?
	if result.outcome != "compatible" {
		return Err("${obligation.id}: ${result.outcome}; inspect diagnostics")
	}
	Ok({})
}

previous! : LifecyclePlan.Plan => Try(Previous, Str)
previous! = |plan| {
	if plan.context.event != "workflow_dispatch" {
		return Ok({ first_release: Bool.True, bundles: [] })
	}
	parsed : Try(Previous, _)
	parsed = Json.parse(read!(Path.utf8(env!("RO_PREVIOUS")?))?)
	found = parsed.map_err(|_| "invalid previous-release evidence")?
	if found.first_release {
		if !found.bundles.is_empty() {
			return Err("first release cannot claim previous bundles")
		}
	} else {
		prefix = "https://github.com/${plan.context.repository}/releases/download/"
		if found.bundles.len() != plan.topology.bundles.len() or Compatibility.unique(found.bundles.map(|entry| entry.id)).len() != found.bundles.len() or !found.bundles.all(|entry| plan.topology.bundles.any(|bundle| bundle.id == entry.id) and entry.url.starts_with(prefix) and entry.url.ends_with(".tar.zst") and Compatibility.path(entry.url.drop_prefix(prefix))) {
			return Err("previous-release evidence must name each declared bundle at a canonical repository release URL")
		}
	}
	Ok(found)
}

emit_output! = |text| {
	match Env.var_str!("GITHUB_OUTPUT") {
		Ok(output) => {
			old = Path.read_utf8!(Path.utf8(output)).map_err(|e| Str.inspect(e))?
			write!(Path.utf8(output), "${old}${text}")
		}
		Err(_) => Ok({})
	}
}

validate_notes = |topology, version, source| {
	if (source.split_on("\n").first() ?? "").trim() != "= ${topology.name} ${version}" {
		return Err("release notes title must be '= ${topology.name} ${version}'")
	}
	for bundle in topology.bundles {
		if !source.contains("{${bundle.placeholder}}") {
			return Err("release notes must use {${bundle.placeholder}}")
		}
	}
	declared = topology.bundles.map(|bundle| bundle.placeholder)
	if unresolved_urls(source).any(|placeholder| !declared.contains(placeholder)) {
		return Err("release notes use an undeclared bundle URL placeholder")
	}
	Ok({})
}

unresolved_urls = |source| source.split_on("{").drop_first(1).map(|tail| tail.split_on("}").first() ?? "").keep_if(|name| name.ends_with("-url"))
