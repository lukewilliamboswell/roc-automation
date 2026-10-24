#!/usr/bin/env roc-stable
app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
	docs: "../tools/roc-docs/release.roc",
	roc: "nightly-2026-10-09-258ab27",
}

import cli.Cmd
import cli.Env
import cli.OsStr
import cli.Path
import cli.Stderr
import cli.Stdout
import docs.BuildFiles
import docs.Sha256
import src/ToolVersion
import src/Compatibility
import src/LifecyclePlan
import src/LifecycleAdmission

Artifact : { id : U64, name : Str, digest : Str }

Transport : { artifacts : List(Artifact) }

Trace : { schema_version : U64, profile : Str, repository : Str, source_sha : Str, run_id : Str, run_attempt : Str, stage : Str, runner : Str, obligations : List({ id : Str, outcome : Str }), files : List(Str) }

## Transport only inert bytes. Raw downloader output is verified and inspected
## before extraction; explicit traced public files are copied into a fresh tree.
main! = |args| {
	if args.map(OsStr.display) == ["--version"] {
		Stdout.line!(ToolVersion.json("lifecycle-transport"))?
		return Ok({})
	}
	result = match args.map(OsStr.display) {
		["extract", manifest, raw, destination, python, helper] => extract!(manifest, Path.utf8(raw), Path.utf8(destination), python, helper)
		["assemble", evidence, destination] => assemble!(Path.utf8(evidence), Path.utf8(destination))
		["restore", plan, candidate, workspace] => restore!(plan, Path.utf8(candidate), Path.utf8(workspace))
		["pack", trace, candidate, metadata, diagnostics, destination] => pack!(Path.utf8(trace), Path.utf8(candidate), metadata, diagnostics, Path.utf8(destination))
		["subject", plan, index, candidate] => subject_output!(plan, index, Path.utf8(candidate))
		["sbom-trace", plan, index, candidate, evidence, inventory, directory] => sbom_trace!(plan, index, Path.utf8(candidate), Path.utf8(evidence), Path.utf8(inventory), Path.utf8(directory))
		_ => Err("usage: lifecycle-transport extract MANIFEST RAW DEST PYTHON HELPER | assemble EVIDENCE DEST")
	}
	match result {
		Ok({}) => Ok({})
		Err(problem) => {
			Stderr.line!("lifecycle transport: ${Compatibility.console(problem)}")?
			Err(Exit(1))
		}
	}
}

restore! = |plan_path, candidate, workspace| {
	parsed : Try(LifecyclePlan.Plan, _)
	parsed = Json.parse(read_json!(Path.utf8(plan_path))?)
	plan = parsed.map_err(|_| "invalid native-output restoration plan")?
	_ = LifecyclePlan.validate(plan.topology)?
	for adapter in plan.topology.adapters {
		for output in adapter.outputs {
			from = BuildFiles.inside!(candidate, output.path)?
			to = BuildFiles.inside!(workspace, output.path)?
			if Path.exists!(to).map_err(|_| "cannot inspect native-output path")? {
				return Err("native output would replace checkout bytes")
			}
			BuildFiles.write_bytes!(workspace, output.path, BuildFiles.read!(from, 50_000_000)?)?
		}
	}
	Ok({})
}

subject! = |plan_path, index_text, candidate| {
	parsed : Try(LifecyclePlan.Plan, _)
	parsed = Json.parse(read_json!(Path.utf8(plan_path))?)
	plan = parsed.map_err(|_| "invalid subject plan")?
	index = U64.from_str(index_text).map_err(|_| "invalid SBOM mapping index")?
	mapping = plan.topology.sboms.get(index).map_err(|_| "unknown SBOM mapping")?
	rules = plan.assets.keep_if(|rule| rule.subject == mapping.subject and rule.target == mapping.target and ["bundle", "host", "binary"].contains(rule.role))
	rule = match rules {
		[one] => one
		_ => return Err("SBOM mapping has no unique declared subject")
	}
	actual = BuildFiles.list!(candidate)?
	file = match actual.keep_if(|entry| LifecycleAdmission.matches_path(rule.path, entry.path)) {
		[one] => one
		_ => return Err("SBOM mapping has no unique actual subject")
	}
	Ok({ plan, index, mapping, file: file.path })
}

append_output! = |value| {
	path = Path.utf8(Env.var_str!("GITHUB_OUTPUT").map_err(|_| "missing GitHub outputs file")?)
	before = Path.read_utf8!(path).map_err(|_| "cannot read GitHub outputs")?
	Path.write_utf8!(path, "${before}${value}").map_err(|_| "cannot write GitHub outputs")
}

subject_output! = |plan_path, index, candidate| {
	selected = subject!(plan_path, index, candidate)?
	workspace = Env.var_str!("GITHUB_WORKSPACE").map_err(|_| "missing workspace")?.drop_suffix("/")
	absolute = Path.display(candidate)
	if !absolute.starts_with("${workspace}/") {
		return Err("SBOM candidate must be inside the workspace")
	}
	relative = absolute.drop_prefix("${workspace}/")
	if !LifecyclePlan.safe_path(relative) {
		return Err("unsafe SBOM candidate directory")
	}
	append_output!("files=${relative}/${selected.file}\n")
}

sbom_trace! = |plan_path, index, candidate, evidence, inventory, directory| {
	selected = subject!(plan_path, index, candidate)?
	files = [selected.file].concat(["spdx.json", "cdx.json", "sbom-manifest.json"].map(|suffix| "${selected.file}.${suffix}"))
	for file in files {
		_ = BuildFiles.read!(BuildFiles.inside!(candidate, file)?, 50_000_000)?
	}
	Path.create_all!(Path.join(directory, "diagnostics")).map_err(|_| "cannot create SBOM evidence directory")?
	BuildFiles.write_bytes!(directory, "diagnostics/sbom-evidence.json", BuildFiles.read!(evidence, 1_000_000)?)?
	BuildFiles.write_bytes!(directory, "diagnostics/sbom-inventory.json", BuildFiles.read!(inventory, 1_000_000)?)?
	context = selected.plan.context
	trace : Trace
	trace = { schema_version: 1, profile: "roc-automation-lifecycle-trace-v1", repository: context.repository, source_sha: context.sha, run_id: context.run_id, run_attempt: context.run_attempt, stage: "sbom", runner: "ubuntu-24.04", obligations: [{ id: "sbom-${selected.index.to_str()}", outcome: "passed" }], files }
	BuildFiles.write_text!(directory, "sbom-trace.json", Json.to_str(trace))?
	append_output!("trace=${Path.display(Path.join(directory, "sbom-trace.json"))}\ndiagnostics=${Path.display(Path.join(directory, "diagnostics"))}\n")
}

pack! = |trace_path, candidate, metadata, diagnostics, destination| {
	parsed : Try(Trace, _)
	parsed = Json.parse(read_json!(trace_path)?)
	trace = parsed.map_err(|_| "missing completed stage trace")?
	if trace.schema_version != 1 or trace.profile != "roc-automation-lifecycle-trace-v1" or !["validate", "host", "source", "prepare", "sbom", "seal", "candidate"].contains(trace.stage) or trace.files.len() > 256 or Compatibility.unique(trace.files).len() != trace.files.len() {
		return Err("invalid completed stage trace")
	}
	if Path.exists!(destination).map_err(|_| "cannot inspect artifact staging output")? {
		return Err("artifact staging output must be fresh")
	}
	Path.create_all!(destination).map_err(|_| "cannot create artifact staging output")?
	var $total = 0.U64
	for relative in trace.files {
		if !LifecyclePlan.safe_path(relative) or relative == "bundle-map.json" or relative.ends_with("-trace.json") or relative == "diagnostics" or relative.starts_with("diagnostics/") {
			return Err("unsafe or reserved public artifact path")
		}
		bytes = BuildFiles.read!(BuildFiles.inside!(candidate, relative)?, 100_000_000)?
		$total = $total + bytes.len()
		if $total > 100_000_000 {
			return Err("workflow artifact public files exceed expansion budget")
		}
		BuildFiles.write_bytes!(destination, relative, bytes)?
	}
	BuildFiles.write_bytes!(destination, "${trace.stage}-trace.json", BuildFiles.read!(trace_path, 2_000_000)?)?
	if metadata != "-" {
		BuildFiles.write_bytes!(destination, "bundle-map.json", BuildFiles.read!(Path.utf8(metadata), 1_000_000)?)?
	}
	if diagnostics != "-" {
		for entry in BuildFiles.list!(Path.utf8(diagnostics))? {
			if !LifecycleAdmission.metadata_path(trace.stage, "diagnostics/${entry.path}") {
				return Err("unexpected executable, cache or unsupported diagnostic metadata")
			}
			bytes = BuildFiles.read!(entry.absolute, 10_000_000)?
			$total = $total + bytes.len()
			if $total > 100_000_000 {
				return Err("workflow artifact diagnostics exceed expansion budget")
			}
			BuildFiles.write_bytes!(destination, "diagnostics/${entry.path}", bytes)?
		}
	}
	Ok({})
}

read_json! = |path| {
	bytes = BuildFiles.read!(path, 2_000_000)?
	Str.from_utf8(bytes).map_err(|_| "transport inputs must be UTF-8")
}

extract! = |manifest, raw, destination, python, helper| {
	parsed : Try(Transport, _)
	parsed = Json.parse(read_json!(Path.utf8(manifest))?)
	transport = parsed.map_err(|_| "invalid trusted artifact transport manifest")?
	if transport.artifacts.is_empty() or transport.artifacts.len() > 256 or Compatibility.unique(transport.artifacts.map(|artifact| artifact.id)).len() != transport.artifacts.len() or Compatibility.unique(transport.artifacts.map(|artifact| artifact.name)).len() != transport.artifacts.len() {
		return Err("missing, duplicate or excessive selected artifacts")
	}
	if Path.exists!(destination).map_err(|_| "cannot inspect extraction output")? {
		return Err("extraction output must be fresh")
	}
	if BuildFiles.list!(raw)?.len() != transport.artifacts.len() {
		return Err("raw download inventory differs from the selected artifact set")
	}
	Path.create_all!(destination).map_err(|_| "cannot create extraction root")?
	for artifact in transport.artifacts {
		if artifact.id == 0 or !LifecyclePlan.safe_path(artifact.name) or artifact.name.contains("/") or !Compatibility.digest(artifact.digest) {
			return Err("invalid artifact identity")
		}
		container = if transport.artifacts.len() == 1 raw else BuildFiles.inside!(raw, artifact.name)?
		entries = BuildFiles.list!(container)?
		if entries.len() != 1 {
			return Err("raw download must contain exactly one archive per selected artifact")
		}
		archive = (entries.first().map_err(|_| "missing raw artifact")?).absolute
		# The helper rehashes the complete bounded raw ZIP and rejects all paths,
		# types, collisions and expansion-limit violations before creating output.
		path = Env.var_str!("PATH").map_err(|_| "missing trusted archive helper tool path")?
		# The inert parser needs no job credentials, OIDC endpoint or Python hooks.
		# SystemRoot is retained only for the Windows Python runtime.
		ran = Cmd.new(OsStr.from_str(python)).clear_envs().env_str("PATH", path).env_str("SystemRoot", Env.var_str!("SystemRoot") ?? "").args([helper, "--artifact", Path.display(archive), artifact.digest, Path.display(Path.join(destination, artifact.name))].map(OsStr.from_str)).stdout(Capture).stderr(Capture).timeout_ms(120_000).output_limit(2_000_000).manage_tree(Bool.True).run!().map_err(|_| "artifact verification did not complete")?
		if ran.status != Exited(0) {
			return Err("artifact verification refused selected bytes: ${Compatibility.console(Str.from_utf8_lossy(ran.stderr_bytes))}")
		}
	}
	Ok({})
}

assemble! = |evidence, destination| {
	if Path.exists!(destination).map_err(|_| "cannot inspect candidate output")? {
		return Err("candidate output must be fresh")
	}
	Path.create_all!(destination).map_err(|_| "cannot create candidate output")?
	entries = BuildFiles.list!(evidence)?
	traces = entries.keep_if(|entry| entry.path.ends_with("-trace.json"))
	if traces.is_empty() or traces.len() > 256 {
		return Err("missing or excessive stage traces")
	}
	var $total = 0.U64
	var $paths = []
	for trace_file in traces {
		if trace_file.path.split_on("/").len() != 2 {
			return Err("trace must be at the root of its selected artifact")
		}
		parsed : Try(Trace, _)
		parsed = Json.parse(read_json!(trace_file.absolute)?)
		trace = parsed.map_err(|_| "invalid stage trace")?
		if trace.schema_version != 1 or trace.profile != "roc-automation-lifecycle-trace-v1" or trace.files.len() > 256 or Compatibility.unique(trace.files).len() != trace.files.len() {
			return Err("invalid trace profile or file inventory")
		}
		artifact_root = BuildFiles.parent_path(trace_file.absolute)
		for relative in trace.files {
			if !LifecyclePlan.safe_path(relative) or relative.ends_with("-trace.json") {
				return Err("unsafe public candidate path")
			}
			source = BuildFiles.inside!(artifact_root, relative)?
			bytes = BuildFiles.read!(source, 100_000_000)?
			$total = $total + bytes.len()
			if $total > 1_000_000_000 {
				return Err("candidate assembly exceeds aggregate byte limit")
			}
			if !$paths.contains(relative) {
				$paths = $paths.append(relative)
				if $paths.len() > 256 {
					return Err("candidate assembly exceeds file limit")
				}
			}
			target = BuildFiles.inside!(destination, relative)?
			if Path.exists!(target).map_err(|_| "cannot inspect candidate path")? {
				before = BuildFiles.read!(target, 100_000_000)?
				if Sha256.hex(before) != Sha256.hex(bytes) {
					return Err("conflicting candidate bytes across selected artifacts")
				}
			} else {
				BuildFiles.write_bytes!(destination, relative, bytes)?
			}
		}
	}
	Ok({})
}
