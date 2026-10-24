import ReleaseVersion
import GitHub
import Compatibility
import LifecycleJson

## The single inert topology and obligation contract used by planning and
## independent admission. Candidate-produced plans never establish authority.
LifecyclePlan := [].{
	Context : { event : Str, ref : Str, default_branch : Str, repository : Str, sha : Str, run_id : Str, run_attempt : Str, compiler : Str, version : Str, full_dry_run : Bool, topology_sha256 : Str }
	Change : { status : Str, old_path : Str, new_path : Str }
	Changes : { complete : Bool, files : List(Change) }
	Bundle : { id : Str, root : Str, prefix : Str, placeholder : Str, depends_on : List(Str) }
	Examples : { id : Str, directory : Str, spec : Str, bundles : List(Str), target_ids : List(Str) }
	Documentation : { id : Str, directory : Str, pdf : Bool }
	Target : { id : Str, runner : Str, runtime_task : Str }
	Output : { role : Str, target : Str, path : Str }
	Adapter : { id : Str, task : Str, operation : Str, builder : Str, target_ids : List(Str), outputs : List(Output) }
	Check : { id : Str, task : Str, kind : Str, target_ids : List(Str) }
	Sbom : { subject : Str, target : Str, composition : Str, supplier_imports : Str }
	Topology : { schema : U64, name : Str, profile : Str, sbom_author : Str, bundles : List(Bundle), examples : List(Examples), documentation : List(Documentation), target_matrix : List(Target), adapters : List(Adapter), checks : List(Check), sboms : List(Sbom), source_paths : List(Str) }
	Obligation : { id : Str, stage : Str, runner : Str, operation : Str, subject : Str, target : Str, task : Str }
	AssetRule : { subject : Str, role : Str, target : Str, path : Str }
	Plan : { schema : U64, context : Context, topology : Topology, full_rehearsal : Bool, obligations : List(Obligation), assets : List(AssetRule), intermediates : List(AssetRule), bundle_order : List(Str) }
	unique = Compatibility.unique
	lower : Str -> Str
	lower = |value| Str.from_utf8_lossy(value.to_utf8().map(|byte| if byte >= 'A' and byte <= 'Z' byte + 32 else byte))
	id = |value| {
		bytes = value.to_utf8()
		first = bytes.first() ?? 0
		!bytes.is_empty() and bytes.len() <= 48 and first >= 'a' and first <= 'z' and bytes.all(|byte| (byte >= 'a' and byte <= 'z') or (byte >= '0' and byte <= '9') or byte == '-')
	}
	valid_target_id = |value| id(value.replace_each("_", "-"))
	digest = |value| value.to_utf8().len() == 64 and value.to_utf8().all(|byte| (byte >= '0' and byte <= '9') or (byte >= 'a' and byte <= 'f'))
	valid_author = |value| {
		name = if value.starts_with("Organization: ") value.drop_prefix("Organization: ") else if value.starts_with("Person: ") value.drop_prefix("Person: ") else ""
		!name.is_empty() and name == name.trim() and value.to_utf8().len() <= 200 and value.to_utf8().all(|byte| byte >= 32 and byte != 127)
	}
	reserved_asset = |value| value == "bundle-map.json" or (!value.contains("/") and value.ends_with("-trace.json")) or value == "diagnostics" or value.starts_with("diagnostics/")
	paths_disjoint : List(Str) -> Bool
	paths_disjoint = |values| {
		paths = values.map(lower)
		unique(paths).len() == paths.len() and !paths.any(|left| paths.any(|right| left != right and right.starts_with("${left}/")))
	}
	sha = |value| value.to_utf8().len() == 40 and value.to_utf8().all(|byte| (byte >= '0' and byte <= '9') or (byte >= 'a' and byte <= 'f'))
	safe_path = |value| {
		parts = value.split_on("/")
		!value.is_empty() and value.to_utf8().len() <= 240 and parts.all(
			|part| {
				base = lower(part.split_on(".").first() ?? "")
				!part.is_empty() and part != "." and part != ".." and !part.ends_with(".") and !part.ends_with(" ") and GitHub.is_plain(part, "-._") and !["con", "prn", "aux", "nul", "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9", "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9"].contains(base)
			},
		)
	}
	version = ReleaseVersion.valid

	parse_topology : Str -> Try(Topology, Str)
	parse_topology = |source| {
		raw = LifecycleJson.decode(source).map_err(LifecycleJson.message)?
		_ = LifecycleJson.object(raw, ["schema", "name", "profile", "sbom_author", "bundles", "examples", "documentation", "target_matrix", "adapters", "checks", "sboms", "source_paths"])?
		if LifecycleJson.field(raw, "schema")? != LifecycleJson.Number("1") {
			return Err("topology schema must be integer 1")
		}
		for pair in [
			{ key: "bundles", fields: ["id", "root", "prefix", "placeholder", "depends_on"] },
			{ key: "examples", fields: ["id", "directory", "spec", "bundles", "target_ids"] },
			{ key: "documentation", fields: ["id", "directory", "pdf"] },
			{ key: "target_matrix", fields: ["id", "runner", "runtime_task"] },
			{ key: "adapters", fields: ["id", "task", "operation", "builder", "target_ids", "outputs"] },
			{ key: "checks", fields: ["id", "task", "kind", "target_ids"] },
			{ key: "sboms", fields: ["subject", "target", "composition", "supplier_imports"] },
		] {
			for item in LifecycleJson.array(LifecycleJson.field(raw, pair.key)?)? {
				_ = LifecycleJson.object(item, pair.fields)?
				if pair.key == "adapters" {
					for output in LifecycleJson.array(LifecycleJson.field(item, "outputs")?)? {
						_ = LifecycleJson.object(output, ["role", "target", "path"])?
					}
				}
			}
		}
		parsed : Try(Topology, _)
		parsed = Json.parse(source)
		found = parsed.map_err(|_| "invalid topology field types")?
		validate(found)
	}

	parse_context : Str -> Try(Context, Str)
	parse_context = |source| {
		raw = LifecycleJson.decode(source).map_err(LifecycleJson.message)?
		_ = LifecycleJson.object(raw, ["event", "ref", "default_branch", "repository", "sha", "run_id", "run_attempt", "compiler", "version", "full_dry_run", "topology_sha256"])?
		parsed : Try(Context, _)
		parsed = Json.parse(source)
		found = parsed.map_err(|_| "invalid context field types")?
		if !GitHub.valid_repository(found.repository) or !sha(found.sha) or !digest(found.topology_sha256) or !GitHub.is_digits(found.run_id) or found.run_id.starts_with("0") or !GitHub.is_digits(found.run_attempt) or found.run_attempt.starts_with("0") or !safe_path(found.default_branch) or !found.compiler.starts_with("nightly-") or !Compatibility.tag(found.compiler) {
			return Err("invalid lifecycle identities")
		}
		if found.event == "workflow_dispatch" {
			if found.ref != "refs/heads/${found.default_branch}" or !version(found.version) or found.version == "0.0.1-dry-run" {
				return Err("release dispatch must name a real version on the default branch")
			}
		} else if found.event == "pull_request" {
			pr = found.ref.drop_prefix("refs/pull/").drop_suffix("/merge")
			if !found.ref.starts_with("refs/pull/") or !found.ref.ends_with("/merge") or !GitHub.is_digits(pr) or found.version != "" {
				return Err("pull requests require the exact merge ref and no requested release version")
			}
		} else {
			return Err("only pull_request and default-branch workflow_dispatch are eligible")
		}
		Ok(found)
	}

	parse_changes : Str -> Try(Changes, Str)
	parse_changes = |source| {
		raw = LifecycleJson.decode(source).map_err(LifecycleJson.message)?
		_ = LifecycleJson.object(raw, ["complete", "files"])?
		for item in LifecycleJson.array(LifecycleJson.field(raw, "files")?)? {
			_ = LifecycleJson.object(item, ["status", "old_path", "new_path"])?
		}
		parsed : Try(Changes, _)
		parsed = Json.parse(source)
		parsed.map_err(|_| "invalid change inventory field types")
	}

	host_owner : Topology, Str -> Try(Bundle, Str)
	host_owner = |topology, path| {
		owners = topology.bundles.keep_if(
			|bundle| {
				parts = bundle.root.split_on("/")
				parent_directory = Str.join_with(parts.take_first(parts.len() - 1), "/")
				parent_directory == "" or path.starts_with("${parent_directory}/")
			},
		)
		match owners {
			[owner] => Ok(owner)
			_ => Err("native host output needs exactly one owning bundle directory")
		}
	}
	public_names_unique = |values| unique(values.map(|value| lower(value.split_on("/").last() ?? ""))).len() == values.len()
	validate : Topology -> Try(Topology, Str)
	validate = |topology| {
		if topology.schema != 1 or !valid_author(topology.sbom_author) or !id(topology.name) or !["package", "platform"].contains(topology.profile) or topology.bundles.is_empty() or topology.bundles.len() > 32 or topology.target_matrix.is_empty() or topology.target_matrix.len() > 16 or topology.adapters.len() > 32 or topology.checks.len() > 128 or topology.examples.is_empty() or topology.examples.len() > 64 or topology.documentation.len() > 32 or topology.sboms.len() > 128 or topology.source_paths.len() > 128 {
			return Err("invalid topology identity or collection bounds")
		}
		for ids in [topology.bundles.map(|x| x.id), topology.adapters.map(|x| x.id), topology.checks.map(|x| x.id), topology.examples.map(|x| x.id), topology.documentation.map(|x| x.id)] {
			if !ids.all(id) or unique(ids).len() != ids.len() {
				return Err("invalid or duplicate topology IDs")
			}
		}
		if !topology.target_matrix.all(|target| valid_target_id(target.id)) or unique(topology.target_matrix.map(|target| target.id)).len() != topology.target_matrix.len() {
			return Err("invalid or duplicate target IDs")
		}
		bundles = topology.bundles.map(|x| x.id)
		target_ids = topology.target_matrix.map(|x| x.id)
		if topology.profile == "package" and (topology.target_matrix.len() != 1 or !(topology.target_matrix.first().map_ok(|entry| entry.runner).map_err(|_| "missing target")?.starts_with("ubuntu-")) or !topology.adapters.is_empty()) {
			return Err("package profile requires one Linux runner and no native adapters")
		}
		for values in [topology.bundles.map(|x| lower(x.root)), topology.bundles.map(|x| x.prefix), topology.bundles.map(|x| x.placeholder), topology.examples.map(|x| lower(x.directory)), topology.documentation.map(|x| lower(x.directory))] {
			if unique(values).len() != values.len() {
				return Err("duplicate or case-colliding topology paths/names")
			}
		}
		for bundle in topology.bundles {
			if !safe_path(bundle.root) or reserved_asset(bundle.root) or !bundle.root.ends_with(".roc") or !id(bundle.prefix) or !id(bundle.placeholder) or bundle.depends_on.len() > 32 or unique(bundle.depends_on).len() != bundle.depends_on.len() or bundle.depends_on.any(|dependency| dependency == bundle.id or !bundles.contains(dependency)) {
				return Err("invalid bundle root, prefix, placeholder or dependency")
			}
		}
		_ = bundle_order(topology)?
		for target in topology.target_matrix {
			if !topology.examples.any(|example| example.target_ids.contains(target.id)) {
				return Err("every declared target requires consumer examples")
			}
			if !Compatibility.runners.contains(target.runner) {
				return Err("target runner is not supported")
			}
			if target.runtime_task != "" and !topology.adapters.any(|adapter| adapter.id == target.runtime_task and adapter.operation == "runtime" and adapter.target_ids.contains(target.id)) {
				return Err("runtime_task must name an applicable runtime adapter")
			}
		}
		for example in topology.examples {
			if !safe_path(example.directory) or !safe_path(example.spec) or !example.spec.starts_with("${example.directory}/") or example.bundles.is_empty() or example.target_ids.is_empty() or example.bundles.any(|value| !bundles.contains(value)) or example.target_ids.any(|value| !target_ids.contains(value)) or unique(example.bundles).len() != example.bundles.len() or unique(example.target_ids).len() != example.target_ids.len() {
				return Err("invalid example directory, spec, bundle or target reference")
			}
		}
		for doc in topology.documentation {
			if !safe_path(doc.directory) {
				return Err("invalid documentation directory")
			}
		}
		var $outputs = []
		for adapter in topology.adapters {
			if !id(adapter.task) or !["build-host", "test-host", "check-bindings", "runtime"].contains(adapter.operation) or !Compatibility.runners.contains(adapter.builder) or adapter.target_ids.is_empty() or adapter.target_ids.any(|value| !target_ids.contains(value)) or unique(adapter.target_ids).len() != adapter.target_ids.len() or adapter.outputs.len() > 64 {
				return Err("invalid native adapter")
			}
			if adapter.operation == "build-host" and (adapter.outputs.is_empty() or adapter.target_ids.any(|target_id| !adapter.outputs.any(|output| output.target == target_id))) {
				return Err("build-host requires explicit outputs")
			}
			if adapter.operation != "build-host" and !adapter.outputs.is_empty() {
				return Err("only build-host may declare outputs")
			}
			for output in adapter.outputs {
				if !["host", "binary"].contains(output.role) or !adapter.target_ids.contains(output.target) or !safe_path(output.path) or reserved_asset(output.path) {
					return Err("invalid native output role, path or target")
				}
				if output.role == "host" {
					_ = host_owner(topology, output.path)?
				}
				$outputs = $outputs.append(output)
			}
		}
		if !paths_disjoint($outputs.map(|x| x.path)) or !public_names_unique($outputs.keep_if(|x| x.role == "binary").map(|x| x.path)) {
			return Err("native outputs have ambiguous producers or case collisions")
		}
		for check in topology.checks {
			if !id(check.task) or !["unit", "source", "native"].contains(check.kind) or check.target_ids.any(|value| !target_ids.contains(value)) or unique(check.target_ids).len() != check.target_ids.len() or (check.kind == "native" and check.target_ids.is_empty()) {
				return Err("invalid independently owned source/native check")
			}
		}
		if unique(topology.checks.map(|x| x.task).concat(topology.adapters.map(|x| x.task))).len() != topology.checks.len() + topology.adapters.len() {
			return Err("each native/check task must have one obligation owner")
		}
		if !topology.source_paths.all(safe_path) or unique(topology.source_paths).len() != topology.source_paths.len() {
			return Err("invalid source path prefixes")
		}
		for mapping in topology.sboms {
			if !safe_path(mapping.composition) or (mapping.supplier_imports != "" and !safe_path(mapping.supplier_imports)) or !(bundles.contains(mapping.subject) and mapping.target == "source") and !$outputs.any(|output| output.role == "binary" and output.path == mapping.subject and output.target == mapping.target) {
				return Err("SBOM mapping must name a declared subject/target and regular-file paths")
			}
		}
		for bundle in topology.bundles {
			if topology.sboms.keep_if(|mapping| mapping.subject == bundle.id and mapping.target == "source").len() != 1 {
				return Err("every bundle needs exactly one source SBOM mapping")
			}
		}
		for output in $outputs.keep_if(|item| item.role == "binary") {
			if !topology.adapters.any(|adapter| adapter.operation == "test-host" and adapter.target_ids.contains(output.target)) {
				return Err("each published binary target requires a consumer test-host adapter")
			}
			if topology.sboms.keep_if(|mapping| mapping.subject == output.path and mapping.target == output.target).len() != 1 {
				return Err("every published binary needs exactly one applicable SBOM mapping")
			}
		}
		Ok(topology)
	}

	bundle_order : Topology -> Try(List(Str), Str)
	bundle_order = |topology| {
		var $ordered = []
		while $ordered.len() < topology.bundles.len() {
			ready = topology.bundles.keep_if(|bundle| !$ordered.contains(bundle.id) and bundle.depends_on.all(|dependency| $ordered.contains(dependency)))
			if ready.is_empty() {
				return Err("bundle dependency cycle")
			}
			$ordered = $ordered.concat(ready.map(|bundle| bundle.id))
		}
		Ok($ordered)
	}

	mandatory = |topology, path| {
		base = path.split_on("/").last() ?? ""
		path.starts_with(".github/") or path.starts_with("actions/") or path.starts_with("scripts/") or path.starts_with("sbom/") or path.starts_with("tools/") or path.starts_with("docs/releases/") or path.starts_with("examples/") or path == "Blueprint.roc" or path == "Blueprint.lock" or path == "rust-toolchain.toml" or path == "Cargo.lock" or path == "Cargo.toml" or path == "build.zig" or path == "build.zig.zon" or path == "release-topology.json" or path == "AGENTS.md" or base == "spec.json" or base == "composition.json" or base == "build.rs" or base == "CMakeLists.txt" or base == "Makefile" or base == "flake.nix" or base.ends_with(".cmake") or base.ends_with(".nix") or base.ends_with(".py") or base.ends_with(".sh") or base.ends_with(".ps1") or base.starts_with("rust-toolchain") or path.contains("/.cargo/") or base.contains("lock") or topology.examples.any(|example| path == example.spec or path == example.directory or path.starts_with("${example.directory}/")) or topology.documentation.any(|doc| path == doc.directory or path.starts_with("${doc.directory}/")) or topology.sboms.any(|mapping| path == mapping.composition or path == mapping.supplier_imports)
	}
	rehearse = |context, topology, changes| context.event == "workflow_dispatch" or context.full_dry_run or !changes.complete or changes.files.is_empty() or changes.files.any(
		|change| {
			if !["added", "modified", "removed", "renamed"].contains(change.status) {
				return Bool.True
			}
			paths = [change.old_path, change.new_path].keep_if(|path| path != "")
			if paths.is_empty() or (change.status == "renamed" and paths.len() != 2) {
				return Bool.True
			}
			paths.any(|path| !safe_path(path) or mandatory(topology, path) or !topology.source_paths.any(|prefix| path == prefix or path.starts_with("${prefix}/")))
		},
	)

	full : Context, Topology -> Try(Plan, Str)
	full = |context, topology| plan(context, topology, { complete: Bool.False, files: [] })
	plan : Context, Topology, Changes -> Try(Plan, Str)
	plan = |context, topology, changes| {
		_ = validate(topology)?
		_ = parse_context(Json.to_str(context))?
		selected_version = if context.event == "pull_request" "0.0.1-dry-run" else context.version
		full_rehearsal = rehearse(context, topology, changes)
		var $obligations = [
			{ id: "source-policy", stage: "source", runner: "ubuntu-24.04", operation: "policy", subject: "", target: "", task: "" },
			{ id: "source-format", stage: "source", runner: "ubuntu-24.04", operation: "format", subject: "", target: "", task: "" },
		]
		if context.event == "workflow_dispatch" {
			for bundle in topology.bundles {
				$obligations = $obligations.append({ id: "version-${bundle.id}", stage: "validate", runner: "ubuntu-24.04", operation: "version-bump", subject: bundle.id, target: "source", task: "" })
			}
		}
		for check in topology.checks {
			if check.target_ids.is_empty() {
				$obligations = $obligations.append({ id: "check-${check.id}", stage: "source", runner: "ubuntu-24.04", operation: check.kind, subject: check.id, target: "", task: check.task })
			} else {
				for target_id in check.target_ids {
					target = topology.target_matrix.find_first(|value| value.id == target_id).map_err(|_| "unknown check target")?
					$obligations = $obligations.append({ id: "check-${check.id}-${target.id}", stage: "source", runner: target.runner, operation: check.kind, subject: check.id, target: target.id, task: check.task })
				}
			}
		}
		for target in topology.target_matrix {
			if target.runtime_task != "" {
				adapter = topology.adapters.find_first(|item| item.id == target.runtime_task).map_err(|_| "unknown runtime adapter")?
				$obligations = $obligations.append({ id: "adapter-${adapter.id}-${target.id}", stage: "source", runner: target.runner, operation: "runtime", subject: adapter.id, target: target.id, task: adapter.task })
				if full_rehearsal {
					$obligations = $obligations.append({ id: "runtime-${adapter.id}-${target.id}", stage: "candidate", runner: target.runner, operation: "runtime", subject: adapter.id, target: target.id, task: adapter.task })
				}
			}
		}

		if full_rehearsal {
			for adapter in topology.adapters.keep_if(|item| item.operation == "test-host") {
				for target_id in adapter.target_ids {
					if topology.adapters.any(|builder| builder.outputs.any(|output| output.role == "binary" and output.target == target_id)) {
						target = topology.target_matrix.find_first(|item| item.id == target_id).map_err(|_| "unknown binary test target")?
						$obligations = $obligations.append({ id: "binary-${adapter.id}-${target.id}", stage: "candidate", runner: target.runner, operation: "test-binary", subject: adapter.id, target: target.id, task: adapter.task })
					}
				}
			}
		}

		for example in topology.examples {
			for target_id in example.target_ids {
				target = topology.target_matrix.find_first(|value| value.id == target_id).map_err(|_| "unknown example target")?
				$obligations = $obligations.append({ id: "working-${example.id}-${target.id}", stage: "source", runner: target.runner, operation: "working-examples", subject: example.id, target: target.id, task: "" })
				if full_rehearsal {
					$obligations = $obligations.append({ id: "archive-${example.id}-${target.id}", stage: "candidate", runner: target.runner, operation: "archive-examples", subject: example.id, target: target.id, task: "" })
				}
			}
		}
		for adapter in topology.adapters {
			if adapter.operation == "build-host" {
				$obligations = $obligations.append({ id: "adapter-${adapter.id}", stage: "host", runner: adapter.builder, operation: adapter.operation, subject: adapter.id, target: "", task: adapter.task })
			} else {
				for target_id in adapter.target_ids {
					target = topology.target_matrix.find_first(|value| value.id == target_id).map_err(|_| "unknown adapter target")?
					if adapter.operation != "runtime" {
						$obligations = $obligations.append({ id: "adapter-${adapter.id}-${target.id}", stage: "source", runner: target.runner, operation: adapter.operation, subject: adapter.id, target: target.id, task: adapter.task })
					}
				}
			}
		}
		var $intermediates = []
		for adapter in topology.adapters {
			for output in adapter.outputs.keep_if(|item| item.role == "host") {
				$intermediates = $intermediates.append({ subject: output.path, role: output.role, target: output.target, path: output.path })
			}
		}
		var $assets = []
		order = bundle_order(topology)?
		if full_rehearsal {
			for bundle_id in order {
				bundle = topology.bundles.find_first(|value| value.id == bundle_id).map_err(|_| "unknown ordered bundle")?
				$obligations = $obligations.append({ id: "bundle-${bundle.id}", stage: "prepare", runner: "ubuntu-24.04", operation: "bundle", subject: bundle.id, target: "source", task: "" })
				$assets = $assets.append({ subject: bundle.id, role: "bundle", target: "source", path: "${bundle.prefix}-{hash}.tar.zst" })
				$obligations = $obligations.append({ id: "api-${bundle.id}", stage: "prepare", runner: "ubuntu-24.04", operation: "api", subject: bundle.id, target: "source", task: "" })
				if topology.documentation.is_empty() {
					$assets = $assets.append({ subject: bundle.id, role: "documentation", target: "source", path: "${bundle.prefix}-docs-${selected_version}.zip" })
				}
			}
			for example in topology.examples {
				$obligations = $obligations.append({ id: "examples-${example.id}", stage: "prepare", runner: "ubuntu-24.04", operation: "package-examples", subject: example.id, target: "source", task: "" })
				name = if topology.examples.len() == 1 topology.name else "${topology.name}-${example.id}"
				$assets = $assets.append({ subject: example.id, role: "examples", target: "source", path: "${name}-examples-${selected_version}.zip" })
			}
			for doc in topology.documentation {
				name = if topology.documentation.len() == 1 topology.name else "${topology.name}-${doc.id}"
				$obligations = $obligations.append({ id: "docs-${doc.id}", stage: "prepare", runner: "ubuntu-24.04", operation: "docs", subject: doc.id, target: "source", task: "" })
				$assets = $assets.append({ subject: doc.id, role: "documentation", target: "source", path: "${name}-docs-${selected_version}.zip" })
				if doc.pdf {
					$assets = $assets.append({ subject: doc.id, role: "pdf", target: "source", path: "${name}-${selected_version}.pdf" })
				}
			}
			for adapter in topology.adapters {
				for output in adapter.outputs.keep_if(|item| item.role == "binary") {
					$assets = $assets.append({ subject: output.path, role: output.role, target: output.target, path: output.path })
				}
			}
			for asset in $assets.keep_if(|asset| ["bundle", "binary"].contains(asset.role)) {
				for suffix in ["spdx.json", "cdx.json", "sbom-manifest.json"] {
					$assets = $assets.append({ subject: asset.subject, role: suffix, target: asset.target, path: "${asset.path}.${suffix}" })
				}
			}
			var $mapping_index = 0.U64
			for mapping in topology.sboms {
				$obligations = $obligations.append({ id: "sbom-${$mapping_index.to_str()}", stage: "sbom", runner: "ubuntu-24.04", operation: "sbom", subject: mapping.subject, target: mapping.target, task: "" })
				$mapping_index = $mapping_index + 1
			}
			$obligations = $obligations.append({ id: "release-notes", stage: "seal", runner: "ubuntu-24.04", operation: "notes", subject: "", target: "source", task: "" })
			$assets = $assets.concat([{ subject: "", role: "notes", target: "source", path: "release-notes.md" }, { subject: "", role: "inventory", target: "source", path: "release-inventory.json" }])
		}
		if $obligations.len() > 256 or unique($obligations.map(|item| item.id)).len() != $obligations.len() {
			return Err("obligation limit or duplicate ownership")
		}
		if !public_names_unique($assets.map(|asset| asset.path)) or !paths_disjoint($assets.map(|asset| asset.path)) or $assets.any(|asset| reserved_asset(asset.path)) {
			return Err("release asset path collision")
		}
		Ok({ schema: 1, context, topology, full_rehearsal, obligations: $obligations, assets: $assets, intermediates: $intermediates, bundle_order: order })
	}
}

sample_topology : LifecyclePlan.Topology
sample_topology = {
	schema: 1,
	name: "sample",
	profile: "package",
	sbom_author: "Organization: roc-automation contributors",
	bundles: [{ id: "core", root: "package/main.roc", prefix: "sample", placeholder: "package-url", depends_on: [] }],
	examples: [{ id: "examples", directory: "examples", spec: "examples/spec.json", bundles: ["core"], target_ids: ["linux"] }],
	documentation: [],
	target_matrix: [{ id: "linux", runner: "ubuntu-24.04", runtime_task: "" }],
	adapters: [],
	checks: [{ id: "unit", task: "check-unit", kind: "unit", target_ids: [] }],
	sboms: [{ subject: "core", target: "source", composition: "sbom/composition.json", supplier_imports: "" }],
	source_paths: ["package"],
}

sample_context : LifecyclePlan.Context
sample_context = { event: "pull_request", ref: "refs/pull/2/merge", default_branch: "main", repository: "owner/sample", sha: Str.repeat("a", 40), run_id: "10", run_attempt: "1", compiler: "nightly-2026-10-23-18478ff", version: "", full_dry_run: Bool.False, topology_sha256: Str.repeat("b", 64) }

sample_changes : LifecyclePlan.Changes
sample_changes = { complete: Bool.True, files: [{ status: "modified", old_path: "package/main.roc", new_path: "package/main.roc" }] }

expect LifecyclePlan.parse_topology(Json.to_str(sample_topology)) == Ok(sample_topology)
expect LifecyclePlan.parse_context(Json.to_str(sample_context)) == Ok(sample_context)
expect LifecyclePlan.parse_changes(Json.to_str(sample_changes)) == Ok(sample_changes)
expect LifecyclePlan.parse_topology(Json.to_str(sample_topology).replace_each("\"schema\":1", "\"schema\":1,\"schema\":1")).is_err()
expect LifecyclePlan.parse_topology(Json.to_str(sample_topology).replace_each("\"schema\":1", "\"schema\":1,\"unknown\":true")).is_err()
expect LifecyclePlan.parse_topology(Json.to_str(sample_topology).replace_each("\"prefix\":\"sample\"", "\"prefix\":\"sample\",\"run\":\"evil\"")).is_err()
expect LifecyclePlan.parse_topology(Json.to_str(sample_topology).replace_each("\"schema\":1", "\"schema\":1.0")).is_err()
expect LifecyclePlan.parse_topology(Json.to_str(sample_topology).replace_each("\"checks\":[", "\"checks\":null,\"discard\":[")).is_err()
expect LifecyclePlan.parse_topology(Json.to_str({ ..sample_topology, schema: 2 })).is_err()
expect LifecyclePlan.parse_topology(Json.to_str({ ..sample_topology, profile: "product" })).is_err()
expect LifecyclePlan.parse_topology(Json.to_str({ ..sample_topology, bundles: [] })).is_err()
expect LifecyclePlan.parse_topology(Json.to_str({ ..sample_topology, target_matrix: [{ id: "linux", runner: "self-hosted", runtime_task: "" }] })).is_err()
expect LifecyclePlan.validate({ ..sample_topology, target_matrix: [{ id: "linux", runner: "windows-2025", runtime_task: "" }] }).is_err()
expect LifecyclePlan.validate({ ..sample_topology, source_paths: ["../package"] }).is_err()
expect LifecyclePlan.validate({ ..sample_topology, sboms: [] }).is_err()
expect LifecyclePlan.validate({ ..sample_topology, sboms: sample_topology.sboms.concat(sample_topology.sboms) }).is_err()
expect LifecyclePlan.validate({ ..sample_topology, examples: [{ id: "examples", directory: "examples", spec: "elsewhere/spec.json", bundles: ["core"], target_ids: ["linux"] }] }).is_err()
expect LifecyclePlan.validate({ ..sample_topology, bundles: [{ id: "core", root: "package/main.roc", prefix: "sample", placeholder: "package-url", depends_on: ["absent"] }] }).is_err()
expect LifecyclePlan.validate({ ..sample_topology, bundles: [{ id: "core", root: "package/main.roc", prefix: "sample", placeholder: "package-url", depends_on: ["core"] }] }).is_err()
expect LifecyclePlan.bundle_order({ ..sample_topology, bundles: [{ id: "one", root: "one.roc", prefix: "one", placeholder: "one-url", depends_on: ["two"] }, { id: "two", root: "two.roc", prefix: "two", placeholder: "two-url", depends_on: ["one"] }] }).is_err()
expect LifecyclePlan.bundle_order({ ..sample_topology, bundles: [{ id: "one", root: "one.roc", prefix: "one", placeholder: "one-url", depends_on: ["two"] }, { id: "two", root: "two.roc", prefix: "two", placeholder: "two-url", depends_on: [] }] }) == Ok(["two", "one"])
expect ["", "../file", "a//b", "/etc/passwd", "a/./b", "a/../b", "C:/x", "a\\b", "COM1.txt", "a/nul", "a/LPT9.exe", "name.", "file ", "a\nfile"].all(|path| !LifecyclePlan.safe_path(path))
expect ["package/main.roc", ".github/workflows/release.yml", "host/x86_64-windows.exe"].all(LifecyclePlan.safe_path)
expect ["1.2.3", "0.1.0", "1.2.3-rc.1"].all(LifecyclePlan.version)
expect ["", "v1.2.3", "1.2", "01.2.3", "1.2.3+build", "1.2.3-", "1.2.3-01", "1.2.3-rc..1"].all(|value| !LifecyclePlan.version(value))
expect LifecyclePlan.parse_context(Json.to_str({ ..sample_context, event: "push" })).is_err()
expect LifecyclePlan.parse_context(Json.to_str({ ..sample_context, version: "1.0.0" })).is_err()
expect LifecyclePlan.parse_context(Json.to_str({ ..sample_context, ref: "refs/heads/main" })).is_err()
expect LifecyclePlan.parse_context(Json.to_str({ ..sample_context, event: "workflow_dispatch", ref: "refs/heads/topic", version: "1.0.0" })).is_err()
expect LifecyclePlan.parse_context(Json.to_str({ ..sample_context, event: "workflow_dispatch", ref: "refs/heads/main", version: "1.0.0" })).is_ok()
expect !LifecyclePlan.rehearse(sample_context, sample_topology, sample_changes)
expect LifecyclePlan.rehearse({ ..sample_context, full_dry_run: Bool.True }, sample_topology, sample_changes)
expect LifecyclePlan.rehearse(sample_context, sample_topology, { ..sample_changes, complete: Bool.False })
expect LifecyclePlan.rehearse(sample_context, sample_topology, { complete: Bool.True, files: [] })
expect [".github/workflows/release.yml", "Blueprint.roc", "Blueprint.lock", "scripts/host.py", "release-topology.json", "examples/main.roc", "docs/releases/1.0.0.adoc", "sbom/composition.json", "unknown.txt", "package/../escape"].all(|path| LifecyclePlan.rehearse(sample_context, sample_topology, { complete: Bool.True, files: [{ status: "modified", old_path: path, new_path: path }] }))
expect LifecyclePlan.rehearse(sample_context, sample_topology, { complete: Bool.True, files: [{ status: "renamed", old_path: "examples/a.roc", new_path: "package/a.roc" }] })
expect LifecyclePlan.rehearse(sample_context, sample_topology, { complete: Bool.True, files: [{ status: "removed", old_path: "scripts/build.py", new_path: "" }] })
expect LifecyclePlan.rehearse(sample_context, sample_topology, { complete: Bool.True, files: [{ status: "unsupported", old_path: "package/a.roc", new_path: "package/a.roc" }] })
expect LifecyclePlan.rehearse(sample_context, sample_topology, { complete: Bool.True, files: [{ status: "modified", old_path: "package/a.roc", new_path: "package/a.roc" }, { status: "added", old_path: "", new_path: ".github/action.yml" }] })
expect match LifecyclePlan.plan(sample_context, sample_topology, sample_changes) {
	Ok(plan) => !plan.full_rehearsal and plan.assets.is_empty() and plan.obligations.map(|x| x.id) == ["source-policy", "source-format", "check-unit", "working-examples-linux"]
	Err(_) => Bool.False
}
expect match LifecyclePlan.full(sample_context, sample_topology) {
	Ok(plan) => plan.full_rehearsal and plan.context == sample_context and plan.bundle_order == ["core"] and plan.assets.any(|x| x.path == "sample-{hash}.tar.zst") and plan.assets.any(|x| x.path == "sample-examples-0.0.1-dry-run.zip") and plan.assets.len() == 8 and plan.obligations.map(|x| x.id).contains("archive-examples-linux")
	Err(_) => Bool.False
}

expect LifecyclePlan.validate({ ..sample_topology, examples: [] }).is_err()
expect LifecyclePlan.validate({ ..sample_topology, profile: "platform", target_matrix: sample_topology.target_matrix.append({ id: "windows", runner: "windows-2025", runtime_task: "" }) }).is_err()
expect ["package/build.rs", "package/native.cmake", "package/build.py", "package/.cargo/config.toml"].all(|path| LifecyclePlan.rehearse(sample_context, sample_topology, { complete: Bool.True, files: [{ status: "modified", old_path: path, new_path: path }] }))

expect !LifecyclePlan.paths_disjoint(["hosts", "hosts/windows.a"])
expect !LifecyclePlan.paths_disjoint(["HOSTS", "hosts/windows.a"])
expect !LifecyclePlan.paths_disjoint(["file", "FILE"])
expect LifecyclePlan.paths_disjoint(["hosts/linux.a", "hosts/windows.a"])

expect match LifecyclePlan.full({ ..sample_context, event: "workflow_dispatch", ref: "refs/heads/main", version: "1.0.0" }, sample_topology) {
	Ok(plan) => plan.obligations.keep_if(|item| item.stage == "validate").map(|item| item.id) == ["version-core"]
	Err(_) => Bool.False
}
expect match LifecyclePlan.full(sample_context, sample_topology) {
	Ok(plan) => !plan.obligations.any(|item| item.stage == "validate")
	Err(_) => Bool.False
}

expect LifecyclePlan.valid_target_id("x86_64-linux") and LifecyclePlan.valid_target_id("aarch64-darwin") and !LifecyclePlan.id("x86_64-linux")
expect !LifecyclePlan.valid_target_id("_linux") and !LifecyclePlan.valid_target_id("x86/64")
expect ["bundle-map.json", "source-trace.json", "diagnostics", "diagnostics/test.log"].all(LifecyclePlan.reserved_asset)
expect !LifecyclePlan.reserved_asset("platform/targets/libhost.a")
expect {
	target = { id: "x86_64-linux", runner: "ubuntu-24.04", runtime_task: "native-runtime" }
	adapter = { id: "native-runtime", task: "check-runtime", operation: "runtime", builder: "ubuntu-24.04", target_ids: [target.id], outputs: [] }
	topology = { ..sample_topology, profile: "platform", target_matrix: [target], examples: [{ id: "examples", directory: "examples", spec: "examples/spec.json", bundles: ["core"], target_ids: [target.id] }], adapters: [adapter] }
	match LifecyclePlan.full(sample_context, topology) {
		Ok(plan) => plan.obligations.keep_if(|item| item.stage == "candidate").map(|item| item.id) == ["runtime-native-runtime-x86_64-linux", "archive-examples-x86_64-linux"]
		Err(_) => Bool.False
	}
}
expect {
	adapter = { id: "native-runtime", task: "check-runtime", operation: "runtime", builder: "ubuntu-24.04", target_ids: ["linux"], outputs: [] }
	match LifecyclePlan.full(sample_context, { ..sample_topology, profile: "platform", adapters: [adapter] }) {
		Ok(plan) => !plan.obligations.any(|item| item.operation == "runtime")
		Err(_) => Bool.False
	}
}

expect {
	target = { id: "linux", runner: "ubuntu-24.04", runtime_task: "runtime" }
	adapter = { id: "runtime", task: "check-runtime", operation: "runtime", builder: "ubuntu-24.04", target_ids: ["linux"], outputs: [] }
	match LifecyclePlan.plan(sample_context, { ..sample_topology, profile: "platform", target_matrix: [target], adapters: [adapter] }, sample_changes) {
		Ok(plan) => plan.obligations.keep_if(|item| item.stage == "source").map(|item| item.id) == ["source-policy", "source-format", "check-unit", "adapter-runtime-linux", "working-examples-linux"]
		Err(_) => Bool.False
	}
}

expect LifecyclePlan.valid_author("Person: Luke Boswell") and LifecyclePlan.valid_author("Organization: Roc contributors")
expect ["", "owner/repo", "Organization: ", "Person:  ", "Person: A\nforged", "Person: A\u(007f)"].all(|value| !LifecyclePlan.valid_author(value))

expect !LifecyclePlan.public_names_unique(["linux/tool", "windows/TOOL"])
expect LifecyclePlan.public_names_unique(["linux/tool-linux", "windows/tool-windows.exe"])
expect LifecyclePlan.host_owner(sample_topology, "elsewhere/libhost.a").is_err()
expect LifecyclePlan.host_owner(sample_topology, "package/targets/libhost.a").map_ok(|bundle| bundle.id) == Ok("core")
expect LifecyclePlan.host_owner({ ..sample_topology, bundles: sample_topology.bundles.append({ id: "nested", root: "package/nested/main.roc", prefix: "nested", placeholder: "nested-url", depends_on: [] }) }, "package/nested/libhost.a").is_err()

expect match LifecyclePlan.full(sample_context, sample_topology) {
	Ok(plan) => plan.obligations.keep_if(|item| item.operation == "api").map(|item| item.id) == ["api-core"] and plan.assets.any(|item| item.path == "sample-docs-0.0.1-dry-run.zip")
	Err(_) => Bool.False
}
expect match LifecyclePlan.full(sample_context, { ..sample_topology, documentation: [{ id: "manual", directory: "docs", pdf: Bool.True }] }) {
	Ok(plan) => plan.obligations.keep_if(|item| item.operation == "api").len() == 1 and plan.assets.keep_if(|item| item.role == "documentation").len() == 1 and plan.assets.any(|item| item.subject == "manual" and item.path == "sample-docs-0.0.1-dry-run.zip")
	Err(_) => Bool.False
}

expect match LifecyclePlan.full(sample_context, sample_topology) {
	Ok(plan) => plan.obligations.keep_if(|item| item.operation == "package-examples").map(|item| item.id) == ["examples-examples"]
	Err(_) => Bool.False
}
expect match LifecyclePlan.plan(sample_context, sample_topology, sample_changes) {
	Ok(plan) => !plan.obligations.any(|item| item.operation == "package-examples")
	Err(_) => Bool.False
}

binary_topology : LifecyclePlan.Topology
binary_topology = { ..sample_topology, profile: "platform", adapters: [{ id: "binary", task: "build-tool", operation: "build-host", builder: "ubuntu-24.04", target_ids: ["linux"], outputs: [{ role: "binary", target: "linux", path: "bin/tool-linux" }] }], sboms: sample_topology.sboms.append({ subject: "bin/tool-linux", target: "linux", composition: "sbom/binary.json", supplier_imports: "" }) }
expect LifecyclePlan.validate(binary_topology).is_err()
expect {
	topology = { ..binary_topology, adapters: binary_topology.adapters.append({ id: "consumer", task: "test-tool", operation: "test-host", builder: "ubuntu-24.04", target_ids: ["linux"], outputs: [] }) }
	match LifecyclePlan.full(sample_context, topology) {
		Ok(plan) => plan.obligations.keep_if(|item| item.stage == "candidate").map(|item| item.id) == ["binary-consumer-linux", "archive-examples-linux"] and plan.assets.any(|item| item.role == "binary" and item.path == "bin/tool-linux")
		Err(_) => Bool.False
	}
}
expect {
	topology = { ..binary_topology, adapters: binary_topology.adapters.append({ id: "consumer", task: "test-tool", operation: "test-host", builder: "ubuntu-24.04", target_ids: ["linux"], outputs: [] }) }
	match LifecyclePlan.plan(sample_context, topology, sample_changes) {
		Ok(plan) => !plan.obligations.any(|item| item.operation == "test-binary")
		Err(_) => Bool.False
	}
}
