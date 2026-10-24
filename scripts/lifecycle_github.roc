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
import src/ToolVersion
import src/Compatibility
import src/LifecyclePlan
import src/LifecycleAdmission
import docs.Sha256

PriorAsset : { name : Str, url : Str }

PriorRelease : { tag : Str, assets : List(PriorAsset) }

RunArtifact : { id : U64, name : Str, digest : Str, expired : Bool, run_id : U64 }

## Read-only GitHub inputs for the shared planner. No candidate code runs here.
## GitHub owns event identities; the caller supplies topology as inert JSON.
main! = |args| {
	if args.map(OsStr.display) == ["--version"] {
		Stdout.line!(ToolVersion.json("lifecycle-github"))?
		return Ok({})
	}
	result = match args.map(OsStr.display) {
		["plan"] => plan!()
		["resolve", plan, stages, output] => resolve!(plan, stages, output)
		_ => Err("expected plan or resolve PLAN STAGES OUTPUT")
	}
	match result {
		Ok({}) => Ok({})
		Err(problem) => {
			Stderr.line!("lifecycle: ${Compatibility.console(problem)}")?
			Err(Exit(1))
		}
	}
}

## Intermediate read-only jobs select this attempt's explicit upstream IDs.
## Publication retries use admission's generation-aware discover operation.
resolve! = |plan_path, stages, output_path| {
	source = Path.read_utf8!(Path.utf8(plan_path)).map_err(|_| "missing lifecycle plan")?
	parsed : Try(LifecyclePlan.Plan, _)
	parsed = Json.parse(source)
	plan = parsed.map_err(|_| "invalid lifecycle plan")?
	requested = stages.split_on(",")
	if requested.is_empty() or !requested.all(|stage| ["validate", "host", "source", "prepare", "sbom", "seal", "candidate"].contains(stage)) {
		return Err("invalid upstream stages")
	}
	repository = env!("GITHUB_REPOSITORY")?
	run_id = env!("GITHUB_RUN_ID")?
	attempt = env!("GITHUB_RUN_ATTEMPT")?
	if plan.context.repository != repository or plan.context.run_id != run_id or plan.context.run_attempt != attempt {
		return Err("intermediate plan belongs to a different run or attempt")
	}
	var $names = []
	for obligation in plan.obligations.keep_if(|obligation| requested.contains(obligation.stage)) {
		identity = if obligation.stage == "sbom" obligation.id else "${obligation.stage}-${obligation.runner}"
		name = "lifecycle-${identity}-${attempt}"
		if !$names.contains(name) {
			$names = $names.append(name)
		}
	}
	response = get!("repos/${repository}/actions/runs/${run_id}/artifacts?per_page=100", "map(.artifacts) | flatten | map({id,name,digest,expired,run_id:.workflow_run.id})", Bool.True)?
	parsed_artifacts : Try(List(RunArtifact), _)
	parsed_artifacts = Json.parse(response)
	available = parsed_artifacts.map_err(|_| "invalid run artifact inventory")?
	var $selected = []
	for name in $names {
		artifact = match available.keep_if(|entry| entry.name == name) {
			[one] if one.id > 0 and !one.expired and one.run_id.to_str() == run_id and Compatibility.digest(one.digest) => one
			_ => return Err("missing, duplicate, expired or unbound upstream artifact ${name}")
		}
		$selected = $selected.append({ id: artifact.id, name: artifact.name, digest: artifact.digest })
	}
	write!(Path.utf8(output_path), Json.to_str({ artifacts: $selected }))?
	output = Path.utf8(env!("GITHUB_OUTPUT")?)
	before = Path.read_utf8!(output).map_err(|_| "missing GitHub outputs file")?
	write!(output, "${before}artifact-ids=${Str.join_with($selected.map(|artifact| artifact.id.to_str()), ",")}\nhas-artifacts=${if $selected.is_empty() "false" else "true"}\n")
}

env! = |name| Env.var_str!(OsStr.from_str(name)).map_err(|_| "missing ${name}")

get! = |endpoint, query, paginate| {
	arguments = ["api", "--method", "GET", endpoint]
	options = if paginate ["--paginate", "--slurp"] else []
	ran = Cmd.new("gh").args(arguments.concat(options).concat(["--jq", query]).map(OsStr.from_str)).stdout(Capture).stderr(Capture).timeout_ms(60_000).output_limit(2_000_000).manage_tree(Bool.True).run!().map_err(|_| "GitHub evidence request did not complete")?
	if ran.status != Exited(0) {
		return Err("GitHub evidence request failed or exceeded its bounds")
	}
	Ok(Str.from_utf8_lossy(ran.stdout_bytes).trim())
}

reviewed_topology! = |repository, sha| {
	if !LifecyclePlan.sha(sha) {
		return Err("invalid immutable topology source")
	}
	arguments = ["api", "--method", "GET", "repos/${repository}/contents/release-topology.json?ref=${sha}", "--header", "Accept: application/vnd.github.raw+json"]
	ran = Cmd.new("gh").args(arguments.map(OsStr.from_str)).stdout(Capture).stderr(Capture).timeout_ms(60_000).output_limit(65536).manage_tree(Bool.True).run!().map_err(|_| "reviewed topology fetch did not complete")?
	if ran.status != Exited(0) {
		return Err("cannot read release-topology.json at the selected event commit")
	}
	Str.from_utf8(ran.stdout_bytes).map_err(|_| "reviewed topology must be UTF-8")
}

write! = |path, value| Path.write_utf8!(path, value).map_err(|_| "cannot write lifecycle evidence")

plan! = || {
	repository = env!("GITHUB_REPOSITORY")?
	if !Compatibility.valid_repository(repository) {
		return Err("invalid repository")
	}
	run_id = env!("GITHUB_RUN_ID")?
	if !run_id.to_utf8().all(|b| b >= '0' and b <= '9') or run_id == "" {
		return Err("invalid run ID")
	}
	event = env!("GITHUB_EVENT_NAME")?
	if event != "pull_request" and event != "workflow_dispatch" {
		return Err("only pull requests and manual dispatches may plan a release")
	}
	actual_event = get!("repos/${repository}/actions/runs/${run_id}", ".event", Bool.False)?
	if actual_event != event {
		return Err("run event differs from GitHub runtime context")
	}
	run_head = get!("repos/${repository}/actions/runs/${run_id}", ".head_sha", Bool.False)?
	if event == "workflow_dispatch" and run_head != env!("GITHUB_SHA")? {
		return Err("dispatch source differs from the actual run")
	}
	default_branch = get!("repos/${repository}", ".default_branch", Bool.False)?
	compiler = get!("repos/roc-lang/nightlies/releases/latest", ".tag_name", Bool.False)?
	if !Compatibility.tag(compiler) or !compiler.starts_with("nightly-") {
		return Err("latest compiler response is not a nightly tag")
	}
	topology = reviewed_topology!(repository, env!("GITHUB_SHA")?)?
	if topology.to_utf8().len() > 65536 {
		return Err("topology exceeds 64 KiB")
	}
	full = env!("INPUT_FULL_DRY_RUN")?
	if full != "true" and full != "false" {
		return Err("full-dry-run must be true or false")
	}
	context : LifecyclePlan.Context
	context = {
		event,
		ref: env!("GITHUB_REF")?,
		default_branch,
		repository,
		sha: env!("GITHUB_SHA")?,
		run_id,
		run_attempt: env!("GITHUB_RUN_ATTEMPT")?,
		compiler,
		version: if event == "pull_request" "" else env!("INPUT_VERSION")?,
		full_dry_run: full == "true",
		topology_sha256: Sha256.hex(topology.to_utf8()),
	}
	changes : LifecyclePlan.Changes
	changes = if event == "workflow_dispatch" {
		{ complete: Bool.True, files: [] }
	} else {
		number = get!("repos/${repository}/actions/runs/${run_id}", ".pull_requests | if length == 1 then .[0].number else 0 end", Bool.False)?
		if number == "0" or !number.to_utf8().all(|b| b >= '0' and b <= '9') {
			{ complete: Bool.False, files: [] }
		} else {
			# The changed-files API follows the live PR. Only use it for narrow
			# selection when both live parents still identify the tested merge.
			head = get!("repos/${repository}/pulls/${number}", ".head.sha", Bool.False)
			base = get!("repos/${repository}/pulls/${number}", ".base.sha", Bool.False)
			parents_source = get!("repos/${repository}/git/commits/${context.sha}", "[.parents[].sha]", Bool.False)
			parents : Try(List(Str), _)
			parents = match parents_source {
				Ok(value) => Json.parse(value)
				Err(_) => Err(Missing)
			}
			bound = match (head, base, parents) {
				(Ok(h), Ok(b), Ok([first, second])) => h == run_head and first == b and second == h
				_ => Bool.False
			}
			# An unreadable/truncated list requires full rehearsal, never a guessed
			# source-only selection. GitHub caps this endpoint at 3000 files.
			response = get!("repos/${repository}/pulls/${number}/files?per_page=100", "flatten | map({status, old_path:(.previous_filename // .filename), new_path:.filename})", Bool.True)
			parsed : Try(List(LifecyclePlan.Change), _)
			parsed = match response {
				Ok(source) => Json.parse(source)
				Err(_) => Err(Missing)
			}
			match parsed {
				Ok(files) if bound and files.len() < 3000 => { complete: Bool.True, files }
				_ => { complete: Bool.False, files: [] }
			}
		}
	}
	directory = Path.utf8(env!("RO_DIRECTORY")?)
	Path.create_all!(directory).map_err(|_| "cannot create lifecycle directory")?
	write!(Path.join(directory, "context.json"), Json.to_str(context))?
	write!(Path.join(directory, "topology.json"), topology)?
	write!(Path.join(directory, "changes.json"), Json.to_str(changes))?
	# The planner executable is fixed by this action, never a candidate task.
	planner = env!("RO_PLAN_BINARY")?
	ran = Cmd.new(OsStr.from_str(planner)).args(
		[
			"plan",
			Path.display(Path.join(directory, "context.json")),
			Path.display(Path.join(directory, "topology.json")),
			Path.display(Path.join(directory, "changes.json")),
			Path.display(Path.join(directory, "plan.json")),
		].map(OsStr.from_str),
	).stdout(Capture).stderr(Capture).timeout_ms(60_000).output_limit(2_000_000).manage_tree(Bool.True).run!().map_err(|_| "planner did not complete")?
	if ran.status != Exited(0) {
		return Err("planner rejected lifecycle inputs: ${Compatibility.console(Str.from_utf8_lossy(ran.stderr_bytes))}")
	}
	plan_source = Path.read_utf8!(Path.join(directory, "plan.json")).map_err(|_| "planner returned no plan")?
	parsed : Try(LifecyclePlan.Plan, _)
	parsed = Json.parse(plan_source)
	plan = parsed.map_err(|_| "planner returned malformed plan")?
	pages = Env.var_str!("INPUT_PAGES_DOCUMENTATION") ?? ""
	if pages != "" and !plan.topology.documentation.any(|manual| manual.id == pages) {
		return Err("Pages documentation ID must be declared by the committed topology")
	}
	if event == "workflow_dispatch" {
		refs_source = get!("repos/${repository}/git/matching-refs/tags/${context.version}", "map(.ref)", Bool.False)?
		refs : Try(List(Str), _)
		refs = Json.parse(refs_source)
		found_refs = refs.map_err(|_| "malformed existing tag inventory")?
		if found_refs.contains("refs/tags/${context.version}") {
			return Err("release version already has a tag")
		}
		tags_source = get!("repos/${repository}/releases?per_page=100", "flatten | map(.tag_name)", Bool.True)?
		tags : Try(List(Str), _)
		tags = Json.parse(tags_source)
		found_tags = tags.map_err(|_| "malformed existing release inventory")?
		if found_tags.contains(context.version) {
			return Err("release version already has a release or draft")
		}
		notes = get!("repos/${repository}/contents/docs/releases/${context.version}.adoc?ref=${context.sha}", "if .type == \"file\" and .size > 0 and .size <= 262144 then \"regular\" else \"invalid\" end", Bool.False)?
		if notes != "regular" {
			return Err("release requires bounded regular authored notes at its event commit")
		}
	}
	# A failed release lookup is not a first release. Pagination and the output
	# bound apply before selecting the latest published final product version.
	prior_source = get!("repos/${repository}/releases?per_page=100", "flatten | map(select(.draft == false and .prerelease == false and (.tag_name | test(\"^v?[0-9]+\\.[0-9]+\\.[0-9]+$\")))) | sort_by(.published_at) | reverse | map({tag:.tag_name,assets:[.assets[] | {name,url:.browser_download_url}]})", Bool.True)?
	prior_parsed : Try(List(PriorRelease), _)
	prior_parsed = Json.parse(prior_source)
	prior_releases = prior_parsed.map_err(|_| "malformed published release inventory")?
	var $previous_bundles = []
	for prior in prior_releases.take_first(1) {
		for bundle in plan.topology.bundles {
			archives = prior.assets.keep_if(|asset| asset.name.ends_with(".tar.zst"))
			matching = archives.keep_if(|asset| asset.name.starts_with("${bundle.prefix}-"))
			selected = if matching.is_empty() and plan.topology.bundles.len() == 1 archives else matching
			asset = match selected {
				[one] => one
				_ => return Err("prior release must have exactly one archive for each declared bundle")
			}
			if !Compatibility.path(asset.name) or !Compatibility.tag(prior.tag) or asset.url != "https://github.com/${repository}/releases/download/${prior.tag}/${asset.name}" {
				return Err("prior bundle URL does not identify its immutable product release")
			}
			$previous_bundles = $previous_bundles.append({ id: bundle.id, url: asset.url })
		}
	}
	write!(Path.join(directory, "prior.json"), Json.to_str({ first_release: prior_releases.is_empty(), bundles: $previous_bundles }))?
	seed : LifecycleAdmission.Trusted
	seed = {
		context,
		changes,
		server_url: env!("GITHUB_SERVER_URL")?,
		run_head_sha: run_head,
		workflow_ref: "",
		workflow_sha: "",
		publish: event == "workflow_dispatch",
		compositions: [],
	}
	write!(Path.join(directory, "trusted-seed.json"), Json.to_str(seed))?
	var $lines = ["source=${context.sha}", "compiler=${compiler}", "version=${if event == "pull_request" "0.0.1-dry-run" else context.version}", "full-rehearsal=${if plan.full_rehearsal "true" else "false"}"]
	for stage in ["validate", "host", "source", "prepare", "candidate"] {
		runners = Compatibility.unique(plan.obligations.keep_if(|obligation| obligation.stage == stage).map(|obligation| obligation.runner))
		$lines = $lines.append("${stage}-matrix=${Json.to_str({ runner: if runners.is_empty() ["ubuntu-24.04"] else runners })}")
	}
	sbom_matrix = plan.topology.sboms.map_with_index(|mapping, index| { index, subject: mapping.subject, target: mapping.target, composition: mapping.composition, supplier_imports: mapping.supplier_imports, author: plan.topology.sbom_author })
	$lines = $lines.append("sbom-matrix=${Json.to_str({ include: sbom_matrix })}")
	$lines = $lines.append("has-host=${if plan.obligations.any(|obligation| obligation.stage == "host") "true" else "false"}")
	output = Path.utf8(env!("GITHUB_OUTPUT")?)
	before = Path.read_utf8!(output).map_err(|_| "missing GitHub outputs file")?
	write!(output, "${before}${Str.join_with($lines, "\n")}\n")
}
