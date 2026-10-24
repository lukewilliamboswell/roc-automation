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
import src/GitHub
import src/LifecycleJson
import src/LifecyclePlan
import src/LifecycleAdmission
import src/LifecycleSbom

## Trusted inert-file admission and read-only GitHub observation. No candidate
## executable, Blueprint file or shell fragment is ever evaluated here.
main! = |args| {
	result = match args.map(OsStr.display) {
		["admit", trusted, topology, jobs, evidence, candidate, output] => admit!(trusted, topology, jobs, evidence, candidate, output)
		["collect", trusted, topology, traces, candidate, output] => collect!(trusted, topology, traces, candidate, output)
		["discover", trusted, topology, output] => discover!(trusted, topology, output)
		_ => Err("usage: release-admit admit TRUSTED TOPOLOGY JOBS EVIDENCE CANDIDATE OUTPUT | collect TRUSTED TOPOLOGY TRACES CANDIDATE OUTPUT_DIR")
	}
	match result {
		Ok(_) => Ok({})
		Err(reason) => {
			Stderr.line!("release-admit: ${reason}")?
			Err(Exit(1))
		}
	}
}

read! = |path, limit| {
	value = Path.utf8(path)
	if Path.type!(value).map_err(|_| "cannot inspect input")? != IsFile {
		return Err("admission input must be a regular file")
	}
	Str.from_utf8(BuildFiles.read!(value, limit)?).map_err(|_| "admission input must be UTF-8")
}

write! = |path, text| Path.write_utf8!(Path.utf8(path), text.concat("\n")).map_err(|_| "cannot write admission output")

tree! = |directory| {
	root = Path.utf8(directory)
	if Path.type!(root).map_err(|_| "cannot inspect candidate directory")? != IsDir {
		return Err("candidate root must be a regular directory")
	}
	entries = BuildFiles.list!(root)?
	var $files = []
	var $total = 0.U64
	for entry in entries {
		if !LifecyclePlan.safe_path(entry.path) {
			return Err("unsafe candidate filename")
		}
		bytes = BuildFiles.read!(entry.absolute, 536870912)?
		$total = $total + bytes.len()
		if $total > 1073741824 {
			return Err("candidate files exceed 1 GiB aggregate limit")
		}
		$files = $files.append({ path: entry.path, size: bytes.len(), sha256: Sha256.hex(bytes) })
	}
	Ok($files)
}

admit! = |trusted_path, topology_path, jobs_path, evidence_path, candidate, output| {
	trusted = LifecycleAdmission.parse_trusted(read!(trusted_path, 4194304)?)?
	topology_text = read!(topology_path, 65536)?
	if Sha256.hex(topology_text.to_utf8()) != trusted.context.topology_sha256 {
		return Err("ADMIT_TOPOLOGY: reviewed topology digest differs")
	}
	topology = LifecyclePlan.parse_topology(topology_text)?
	jobs = LifecycleAdmission.parse_observations(read!(jobs_path, 4194304)?)?
	evidence = LifecycleAdmission.parse_evidence(read!(evidence_path, 1048576)?)?
	actual = tree!(candidate)?
	report = LifecycleAdmission.validate(trusted, topology, jobs, evidence, actual)?
	validate_sboms!(trusted, topology, evidence, candidate)?
	write!(output, Json.to_str(report))?
	Stdout.line!("PASS admitted exact source/run/workflow, obligations, artifacts and candidate bytes").map_err(|_| "cannot display result")
}

validate_sboms! = |trusted, topology, evidence, candidate| {
	if LifecyclePlan.unique(trusted.compositions.map(|item| item.path)).len() != trusted.compositions.len() or !trusted.compositions.all(|item| LifecyclePlan.safe_path(item.path) and Sha256.hex(item.text.to_utf8()) == item.sha256) {
		return Err("SBOM_COMPOSITION: duplicate, unsafe or altered reviewed source record")
	}
	if !evidence.files.is_empty() and (evidence.producer_evidence.len() != topology.sboms.len() or LifecyclePlan.unique(evidence.producer_evidence.map(|item| item.id)).len() != evidence.producer_evidence.len()) {
		return Err("SBOM_PRODUCER: missing or duplicate producer transfer records")
	}
	var $mapping_index = 0.U64
	for mapping in topology.sboms {
		if !evidence.files.is_empty() {
			subject = evidence.files.find_first(|file| file.subject == mapping.subject and file.target == mapping.target and ["bundle", "host", "binary"].contains(file.role)).map_err(|_| "SBOM_SUBJECT: declared subject missing")?
			obligation_id = "sbom-${$mapping_index.to_str()}"
			producer_record = evidence.producer_evidence.find_first(|record| record.id == obligation_id).map_err(|_| "SBOM_PRODUCER: no exact job-bound producer record")?
			if Sha256.hex(producer_record.document.to_utf8()) != producer_record.sha256 {
				return Err("SBOM_PRODUCER: producer record digest differs")
			}
			claim = evidence.obligations.find_first(|item| item.id == obligation_id).map_err(|_| "SBOM_PRODUCER: no expected producer job")?
			if !evidence.artifacts.any(|artifact| artifact.job_id == claim.job_id and artifact.run_attempt == claim.run_attempt and artifact.metadata.any(|item| item.path == "diagnostics/sbom-evidence.json" and item.sha256 == producer_record.sha256 and item.size == producer_record.document.to_utf8().len())) {
				return Err("SBOM_PRODUCER: producer record is not bound to its actual job artifact")
			}
			LifecycleSbom.producer(producer_record.document, trusted.context.repository, trusted.context.sha, trusted.context.run_id, claim.run_attempt.to_str(), LifecycleAdmission.release_version(trusted.context), { path: subject.path, size: subject.size, sha256: subject.sha256, target: subject.target }, evidence.files.map(|file| { path: file.path, size: file.size, sha256: file.sha256 }))?

			composition = trusted.compositions.find_first(|item| item.path == mapping.composition).map_err(|_| "SBOM_COMPOSITION: no independently fetched reviewed composition")?
			if Sha256.hex(composition.text.to_utf8()) != composition.sha256 {
				return Err("SBOM_COMPOSITION: reviewed composition digest differs")
			}
			supplier_text = if mapping.supplier_imports == "" "" else trusted.compositions.find_first(|item| item.path == mapping.supplier_imports).map_err(|_| "SBOM_IMPORT: no independently fetched reviewed imports")?.text
			merged = LifecycleSbom.merged_composition(composition.text, supplier_text)?
			for record in LifecycleSbom.optional_array_field(merged, "imported_documents")? {
				document = LifecycleSbom.string_field(record, "document")?
				sha256 = LifecycleSbom.string_field(record, "sha256")?
				if Sha256.hex(document.to_utf8()) != sha256 {
					return Err("SBOM_IMPORT: reviewed supplier document digest differs")
				}
				_ = LifecycleJson.decode_limit(document, 1048576).map_err(LifecycleJson.message)?
			}
			root = Path.utf8(candidate)
			manifest = Path.display(BuildFiles.inside!(root, "${subject.path}.sbom-manifest.json")?)
			spdx = Path.display(BuildFiles.inside!(root, "${subject.path}.spdx.json")?)
			cdx = Path.display(BuildFiles.inside!(root, "${subject.path}.cdx.json")?)
			LifecycleSbom.validate({ path: subject.path, size: subject.size, sha256: subject.sha256, target: subject.target }, LifecycleAdmission.release_version(trusted.context), trusted.context.repository, trusted.context.sha, read!(manifest, 1048576)?, read!(spdx, 1048576)?, read!(cdx, 1048576)?, composition.text, supplier_text, evidence.files.map(|file| { path: file.path, size: file.size, sha256: file.sha256 }))?
		}
		$mapping_index = $mapping_index + 1
	}
	Ok({})
}

# Every request is an explicit bounded GET. GH_TOKEN is inherited only by gh,
# never placed in the arguments, report, candidate environment or diagnostics.
get! = |path, projection| {
	call : GitHub.Call
	call = { method: "GET", path, version: "2026-03-10", accept: "application/vnd.github+json", jq: projection, send: Nothing }
	output = Cmd.new("gh").args(GitHub.args(call).map(OsStr.from_str)).stdout(Capture).stderr(Capture).timeout_ms(60000).output_limit(4194304).manage_tree(Bool.True).run!().map_err(|_| "ADMIT_API: bounded read-only GitHub request failed")?
	if output.status != Exited(0) {
		return Err("ADMIT_API: GitHub GET failed for selected run evidence")
	}
	Str.from_utf8(output.stdout_bytes).map_err(|_| "ADMIT_API: invalid UTF-8 response")
}

Trace : { schema_version : U64, profile : Str, repository : Str, source_sha : Str, run_id : Str, run_attempt : Str, stage : Str, runner : Str, obligations : List({ id : Str, outcome : Str }), files : List(Str) }

Transport : { id : U64, name : Str, digest : Str, stage : Str, runner : Str, attempt : U64, job_id : U64, obligations : List(Str) }

Snapshot : { trusted : LifecycleAdmission.Trusted, topology : LifecyclePlan.Topology, observations : LifecycleAdmission.Observations, producing_attempt : U64, artifacts : List(Transport) }

snapshot! : Str, Str => Try(Snapshot, Str)
snapshot! = |trusted_path, topology_path| {
	original_seed = LifecycleAdmission.parse_trusted(read!(trusted_path, 4194304)?)?
	runtime_attempt = env!("GITHUB_RUN_ATTEMPT")?
	seed_attempt = U64.from_str(original_seed.context.run_attempt).map_err(|_| "ADMIT_ATTEMPT: malformed seed attempt")?
	observing_attempt = U64.from_str(runtime_attempt).map_err(|_| "ADMIT_ATTEMPT: malformed actual attempt")?
	if seed_attempt == 0 or seed_attempt > observing_attempt {
		return Err("ADMIT_ATTEMPT: immutable seed comes from a future generation")
	}
	seed = { ..original_seed, context: { ..original_seed.context, run_attempt: runtime_attempt } }
	context = seed.context
	if seed.server_url != "https://github.com" or context.repository != env!("GITHUB_REPOSITORY")? or context.run_id != env!("GITHUB_RUN_ID")? or context.run_attempt != env!("GITHUB_RUN_ATTEMPT")? or context.sha != env!("GITHUB_SHA")? or context.event != env!("GITHUB_EVENT_NAME")? or context.ref != env!("GITHUB_REF")? or seed.server_url != env!("GITHUB_SERVER_URL")? {
		return Err("ADMIT_ENVIRONMENT: seed differs from the actual caller environment")
	}
	if !GitHub.valid_repository(context.repository) or !LifecyclePlan.sha(context.sha) {
		return Err("ADMIT_CONTEXT: unsafe repository or source identity")
	}
	current = U64.from_str(context.run_attempt).map_err(|_| "ADMIT_ATTEMPT: invalid attempt")?
	if current == 0 or current > 32 {
		return Err("ADMIT_ATTEMPT: at most 32 attempts supported")
	}
	repository = context.repository
	default_text = get!("repos/${repository}", ".default_branch")?
	if default_text.trim() != context.default_branch or (seed.publish and (context.event != "workflow_dispatch" or context.ref != "refs/heads/${context.default_branch}")) {
		return Err("ADMIT_DEFAULT_BRANCH: dispatch is not from the actual default branch")
	}
	run_text = get!("repos/${repository}/actions/runs/${context.run_id}", "{id,event,head_sha,path,run_attempt,repository:{full_name:.repository.full_name},referenced_workflows:[.referenced_workflows[]|{path,sha}]}")?
	run_parsed : Try({ id : U64, event : Str, head_sha : Str, path : Str, run_attempt : U64, repository : { full_name : Str }, referenced_workflows : List({ path : Str, sha : Str }) }, _)
	run_parsed = Json.parse(run_text)
	run = run_parsed.map_err(|_| "ADMIT_RUN: malformed actual run record")?
	if run.id.to_str() != context.run_id or run.repository.full_name != repository or run.event != context.event or run.run_attempt != current or run.head_sha != seed.run_head_sha {
		return Err("ADMIT_RUN: selected run identity differs from actual GitHub record")
	}
	workflows = run.referenced_workflows.keep_if(|item| item.path.starts_with("lukewilliamboswell/roc-automation/.github/workflows/release-lifecycle.yml@"))
	workflow = match workflows {
		[only] => only
		_ => return Err("ADMIT_WORKFLOW: missing or ambiguous actual reusable workflow")
	}
	if ((seed.workflow_ref != "" or seed.workflow_sha != "") and (workflow.path != seed.workflow_ref or workflow.sha != seed.workflow_sha)) or workflow.path != "lukewilliamboswell/roc-automation/.github/workflows/release-lifecycle.yml@${workflow.sha}" or !LifecyclePlan.sha(workflow.sha) {
		return Err("ADMIT_WORKFLOW: expected full-SHA workflow differs from actual referenced workflow")
	}
	caller_path = run.path.split_on("@").first() ?? ""
	if !LifecyclePlan.safe_path(caller_path) or !caller_path.starts_with(".github/workflows/") {
		return Err("ADMIT_WORKFLOW: unsafe caller path")
	}
	caller_source_sha = env!("GITHUB_WORKFLOW_SHA")?
	caller = raw_source!(repository, caller_path, caller_source_sha)?
	pins = caller.split_on("\n").map(|line| line.trim()).keep_if(|line| line.starts_with("uses:")).map(|line| line.drop_prefix("uses:").trim().split_on(" ").first() ?? "").keep_if(|line| line.starts_with("lukewilliamboswell/roc-automation/.github/workflows/release-lifecycle.yml@"))
	if pins != [workflow.path] {
		return Err("ADMIT_WORKFLOW: immutable caller must name exactly the actual full-SHA reusable workflow")
	}
	var $changes = seed.changes
	if context.event == "workflow_dispatch" {
		if context.sha != run.head_sha {
			return Err("ADMIT_SOURCE: dispatch source differs from actual run")
		}
	} else if context.event == "pull_request" {
		commit_text = get!("repos/${repository}/git/commits/${context.sha}", "{sha,parents:[.parents[].sha]}")?
		parsed_commit : Try({ sha : Str, parents : List(Str) }, _)
		parsed_commit = Json.parse(commit_text)
		commit = parsed_commit.map_err(|_| "ADMIT_SOURCE: malformed tested merge commit")?
		if commit.sha != context.sha or commit.parents.len() != 2 or !commit.parents.contains(run.head_sha) {
			return Err("ADMIT_SOURCE: tested merge commit does not include actual PR head")
		}
		pr_number = context.ref.drop_prefix("refs/pull/").drop_suffix("/merge")
		pr_text = get!("repos/${repository}/pulls/${pr_number}", "{head:{sha:.head.sha},base:{sha:.base.sha},changed_files}")?
		parsed_pr : Try({ head : { sha : Str }, base : { sha : Str }, changed_files : U64 }, _)
		parsed_pr = Json.parse(pr_text)
		pr = parsed_pr.map_err(|_| "ADMIT_CHANGES: malformed pull request identity")?
		$changes = { complete: Bool.False, files: [] }
		if pr.head.sha == run.head_sha and commit.parents.get(0) == Ok(pr.base.sha) and commit.parents.get(1) == Ok(pr.head.sha) and pr.changed_files <= 3000 {
			var $files = []
			var $change_page = 1.U64
			while Bool.True {
				change_text = get!("repos/${repository}/pulls/${pr_number}/files?per_page=100&page=${$change_page.to_str()}", "[.[]|{status,filename,previous_filename:(.previous_filename // \"\")}]")?
				parsed_files : Try(List({ status : Str, filename : Str, previous_filename : Str }), _)
				parsed_files = Json.parse(change_text)
				batch = parsed_files.map_err(|_| "ADMIT_CHANGES: malformed changed-file inventory")?
				if batch.len() > 100 or $files.len() + batch.len() > 3000 or $change_page > 31 {
					return Err("ADMIT_CHANGES: changed-file pagination exceeds bounds")
				}
				$files = $files.concat(batch.map(|file| { status: file.status, old_path: if file.status == "renamed" file.previous_filename else file.filename, new_path: file.filename }))
				if batch.len() < 100 {
					break
				}
				$change_page = $change_page + 1
			}
			if $files.len() == pr.changed_files {
				$changes = { complete: Bool.True, files: $files }
			}
		}
	} else {
		return Err("ADMIT_EVENT: only pull request and default-branch dispatch are eligible")
	}
	topology_text = read!(topology_path, 65536)?
	reviewed_topology = raw_source!(repository, "release-topology.json", context.sha)?
	if reviewed_topology != topology_text {
		return Err("ADMIT_TOPOLOGY: supplied topology differs from the committed event-source topology")
	}
	if Sha256.hex(topology_text.to_utf8()) != context.topology_sha256 {
		return Err("ADMIT_TOPOLOGY: topology digest differs")
	}
	topology = LifecyclePlan.parse_topology(topology_text)?
	plan = LifecyclePlan.plan(context, topology, $changes)?
	var $compositions = []
	for mapping in topology.sboms {
		for path in [mapping.composition, mapping.supplier_imports] {
			if path != "" and !$compositions.any(|item| item.path == path) {
				text = raw_source!(repository, path, context.sha)?
				$compositions = $compositions.append({ path, text, sha256: Sha256.hex(text.to_utf8()) })
			}
		}
	}
	trusted = { ..seed, changes: $changes, workflow_ref: workflow.path, workflow_sha: workflow.sha, compositions: $compositions }
	if Json.to_str(trusted).to_utf8().len() > 4194304 {
		return Err("ADMIT_COMPOSITION: reviewed source records exceed the 4 MiB context limit")
	}
	var $jobs = []
	for attempt in 1..=current {
		var $page = 1.U64
		var $seen = 0.U64
		var $expected = 0.U64
		while Bool.True {
			text = get!("repos/${repository}/actions/runs/${context.run_id}/attempts/${attempt.to_str()}/jobs?per_page=100&page=${$page.to_str()}", "{total_count,jobs:[.jobs[]|{id,name,conclusion:(.conclusion // \"\"),run_id,run_attempt,head_sha,labels}]}")?
			parsed : Try({ total_count : U64, jobs : List(LifecycleAdmission.Job) }, _)
			parsed = Json.parse(text)
			batch = parsed.map_err(|_| "ADMIT_JOBS: malformed actual job inventory")?
			if batch.total_count > 4096 or batch.jobs.len() > 100 or $page > 41 {
				return Err("ADMIT_JOBS: excessive or incomplete pagination")
			}
			if $page == 1 {
				$expected = batch.total_count
			}
			if $expected != batch.total_count {
				return Err("ADMIT_JOBS: job inventory changed while paginating")
			}
			$seen = $seen + batch.jobs.len()
			$jobs = $jobs.concat(batch.jobs)
			if batch.jobs.len() < 100 {
				if $seen != $expected {
					return Err("ADMIT_JOBS: incomplete actual job inventory")
				}
				break
			}
			$page = $page + 1
		}
	}
	if $jobs.len() > 4096 {
		return Err("ADMIT_JOBS: job observation limit exceeded")
	}
	var $remote_artifacts = []
	var $page = 1.U64
	var $expected_artifacts = 0.U64
	while Bool.True {
		text = get!("repos/${repository}/actions/runs/${context.run_id}/artifacts?per_page=100&page=${$page.to_str()}", "{total_count,artifacts:[.artifacts[]|{id,name,digest,expired,workflow_run:{id:.workflow_run.id,head_sha:.workflow_run.head_sha}}]}")?
		parsed : Try({ total_count : U64, artifacts : List(LifecycleAdmission.RemoteArtifact) }, _)
		parsed = Json.parse(text)
		batch = parsed.map_err(|_| "ADMIT_ARTIFACTS: malformed actual artifact inventory")?
		if batch.total_count > 4096 or batch.artifacts.len() > 100 or $page > 41 {
			return Err("ADMIT_ARTIFACTS: excessive or incomplete pagination")
		}
		if $page == 1 {
			$expected_artifacts = batch.total_count
		}
		if $expected_artifacts != batch.total_count {
			return Err("ADMIT_ARTIFACTS: inventory changed while paginating")
		}
		$remote_artifacts = $remote_artifacts.concat(batch.artifacts)
		if batch.artifacts.len() < 100 {
			if $remote_artifacts.len() != $expected_artifacts {
				return Err("ADMIT_ARTIFACTS: incomplete actual artifact inventory")
			}
			break
		}
		$page = $page + 1
	}
	observations = { complete: Bool.True, jobs: $jobs, artifacts: $remote_artifacts }
	var $producing_attempt = 0.U64
	for job in $jobs {
		if plan.obligations.any(
			|item| {
				display = LifecycleAdmission.job_name(item) ?? ""
				job.name == display or job.name.ends_with(" / ${display}")
			},
		) and job.run_attempt > $producing_attempt {
			$producing_attempt = job.run_attempt
		}
	}
	if $producing_attempt == 0 or $producing_attempt > current {
		return Err("ADMIT_GENERATION: cannot establish producing attempt")
	}
	var $artifacts = []
	var $keys = []
	for obligation in plan.obligations {
		key = if obligation.stage == "sbom" obligation.id else "${obligation.stage}-${obligation.runner}"
		if !$keys.contains(key) {
			$keys = $keys.append(key)
			display = LifecycleAdmission.job_name(obligation)?
			matches = $jobs.keep_if(|job| job.run_attempt == $producing_attempt and (job.name == display or job.name.ends_with(" / ${display}")))
			job = match matches {
				[only] => only
				_ => return Err("ADMIT_JOB: missing or ambiguous producing job")
			}
			if job.conclusion != "success" or job.run_id.to_str() != context.run_id or job.head_sha != trusted.run_head_sha or !job.labels.contains(obligation.runner) {
				return Err("ADMIT_JOB: required producing job did not succeed on selected source/runner")
			}
			name = "lifecycle-${key}-${$producing_attempt.to_str()}"
			artifact = match $remote_artifacts.keep_if(|item| item.name == name) {
				[only] => only
				_ => return Err("ADMIT_ARTIFACT: missing or ambiguous expected transport")
			}
			if artifact.expired or artifact.workflow_run.id.to_str() != context.run_id or artifact.workflow_run.head_sha != trusted.run_head_sha or !artifact.digest.starts_with("sha256:") or !LifecyclePlan.digest(artifact.digest.drop_prefix("sha256:")) {
				return Err("ADMIT_ARTIFACT: wrong transport identity or expired bytes")
			}
			ids = plan.obligations.keep_if(|item| (if item.stage == "sbom" item.id else "${item.stage}-${item.runner}") == key).map(|item| item.id)
			$artifacts = $artifacts.append({ id: artifact.id, name, digest: artifact.digest, stage: obligation.stage, runner: obligation.runner, attempt: $producing_attempt, job_id: job.id, obligations: ids })
		}
	}
	Ok({ trusted, topology, observations, producing_attempt: $producing_attempt, artifacts: $artifacts })
}

raw_source! = |repository, path, sha| {
	if !LifecyclePlan.safe_path(path) or !LifecyclePlan.sha(sha) {
		return Err("ADMIT_SOURCE: unsafe immutable source request")
	}
	call : GitHub.Call
	call = { method: "GET", path: "repos/${repository}/contents/${path}?ref=${sha}", version: "2026-03-10", accept: "application/vnd.github.raw+json", jq: "", send: Nothing }
	result = Cmd.new("gh").args(GitHub.args(call).map(OsStr.from_str)).stdout(Capture).stderr(Capture).timeout_ms(60000).output_limit(1048576).manage_tree(Bool.True).run!().map_err(|_| "ADMIT_SOURCE: immutable source fetch failed")?
	if result.status != Exited(0) {
		return Err("ADMIT_SOURCE: cannot read reviewed source at event SHA")
	}
	Str.from_utf8(result.stdout_bytes).map_err(|_| "ADMIT_SOURCE: reviewed source is not UTF-8")
}

env! = |name| Env.var_str!(name).map_err(|_| "ADMIT_ENVIRONMENT: required caller environment is missing")

save_snapshot! : Snapshot, Str => Try({}, Str)
save_snapshot! = |snapshot, output| {
	Path.create_all!(Path.utf8(output)).map_err(|_| "cannot create admission output")?
	write!("${output}/trusted.json", Json.to_str(snapshot.trusted))?
	write!("${output}/jobs.json", Json.to_str(snapshot.observations))?
	write!("${output}/transport.json", Json.to_str({ schema_version: 1.U64, producing_attempt: snapshot.producing_attempt, artifacts: snapshot.artifacts }))?
	match Env.var_str!("GITHUB_OUTPUT") {
		Ok(path) if path != "" => append!(Path.utf8(path), "artifact-ids=${Str.join_with(snapshot.artifacts.map(|item| item.id.to_str()), ",")}\nproducing-attempt=${snapshot.producing_attempt.to_str()}\n").map_err(|_| "cannot write admission outputs")?
		_ => {}
	}
	Ok({})
}

discover! = |trusted, topology, output| {
	snapshot = snapshot!(trusted, topology)?
	save_snapshot!(snapshot, output)
}

parse_trace = |text| {
	raw = LifecycleJson.decode_limit(text, 1048576).map_err(LifecycleJson.message)?
	LifecycleJson.object(raw, ["schema_version", "profile", "repository", "source_sha", "run_id", "run_attempt", "stage", "runner", "obligations", "files"])?
	for item in LifecycleJson.array(LifecycleJson.field(raw, "obligations")?)? {
		LifecycleJson.object(item, ["id", "outcome"])?
	}
	parsed : Try(Trace, _)
	parsed = Json.parse(text)
	parsed.map_err(|_| "ADMIT_TRACE: malformed inert trace")
}

collect! = |trusted_path, topology_path, traces, candidate, output| {
	snapshot = snapshot!(trusted_path, topology_path)?
	save_snapshot!(snapshot, output)?
	trusted = snapshot.trusted
	context = trusted.context
	plan = LifecyclePlan.plan(context, snapshot.topology, trusted.changes)?
	actual = tree!(candidate)?
	var $files = []
	for file in actual {
		rule = match plan.assets.keep_if(|candidate_rule| LifecycleAdmission.matches_path(candidate_rule.path, file.path)) {
			[only] => only
			_ => return Err("ADMIT_FILES: file has no unique expected topology role")
		}
		$files = $files.append({ path: file.path, size: file.size, sha256: file.sha256, subject: rule.subject, target: rule.target, role: rule.role })
	}
	var $artifacts = []
	var $obligations = []
	var $producer_evidence = []
	for artifact in snapshot.artifacts {
		root = Path.utf8("${traces}/${artifact.name}")
		if Path.type!(root).map_err(|_| "ADMIT_TRACE: cannot inspect artifact root")? != IsDir {
			return Err("ADMIT_TRACE: artifact root must be a regular directory")
		}
		trace_path = BuildFiles.inside!(root, "${artifact.stage}-trace.json")?
		text = read!(Path.display(trace_path), 1048576)?
		trace = parse_trace(text)?
		if trace.schema_version != 1 or trace.profile != "roc-automation-lifecycle-trace-v1" or trace.repository != context.repository or trace.source_sha != context.sha or trace.run_id != context.run_id or trace.run_attempt != artifact.attempt.to_str() or trace.stage != artifact.stage or trace.runner != artifact.runner or trace.obligations.len() != artifact.obligations.len() or LifecyclePlan.unique(trace.obligations.map(|item| item.id)).len() != trace.obligations.len() or !trace.obligations.all(|item| item.outcome == "passed" and artifact.obligations.contains(item.id)) or LifecyclePlan.unique(trace.files).len() != trace.files.len() {
			return Err("ADMIT_TRACE: context, actual stage or required obligation set differs")
		}
		# Rehash EACH artifact copy, including duplicates transported through
		# later stages. No byte from one artifact can stand in for another.
		for path in trace.files {
			expected = $files.find_first(|file| file.path == path).map_err(|_| "ADMIT_TRACE: undeclared transported file")?
			bytes = BuildFiles.read!(BuildFiles.inside!(root, path)?, 536870912)?
			if bytes.len() != expected.size or Sha256.hex(bytes) != expected.sha256 {
				return Err("ADMIT_TRACE: transported bytes differ from final candidate")
			}
		}
		entries = BuildFiles.list!(root)?
		var $metadata = []
		var $metadata_bytes = 0.U64
		for entry in entries {
			if entry.path != "${artifact.stage}-trace.json" and !trace.files.contains(entry.path) {
				if !LifecycleAdmission.metadata_path(artifact.stage, entry.path) {
					return Err("ADMIT_TRACE: transport contains undeclared files or executable caches")
				}
				bytes = BuildFiles.read!(entry.absolute, 1048576)?
				$metadata_bytes = $metadata_bytes + bytes.len()
				if $metadata_bytes > 33554432 or $metadata.len() >= 1024 {
					return Err("ADMIT_METADATA: diagnostics exceed bounded limits")
				}
				if entry.path == "bundle-map.json" {
					_ = LifecycleJson.decode_limit(Str.from_utf8(bytes).map_err(|_| "ADMIT_METADATA: bundle map must be UTF-8 JSON")?, 1048576).map_err(LifecycleJson.message)?
				}
				$metadata = $metadata.append({ path: entry.path, size: bytes.len(), sha256: Sha256.hex(bytes) })
			}
		}
		if artifact.stage == "sbom" {
			document = read!(Path.display(BuildFiles.inside!(root, "diagnostics/sbom-evidence.json")?), 1048576)?
			id = match artifact.obligations {
				[only] => only
				_ => return Err("SBOM_PRODUCER: SBOM job must have one exact obligation")
			}
			$producer_evidence = $producer_evidence.append({ id, sha256: Sha256.hex(document.to_utf8()), document })
		}

		$artifacts = $artifacts.append({ id: artifact.id, digest: artifact.digest, job_id: artifact.job_id, run_attempt: artifact.attempt, trace_sha256: Sha256.hex(text.to_utf8()), obligations: artifact.obligations, files: trace.files, metadata: $metadata })
		$obligations = $obligations.concat(trace.obligations.map(|item| { id: item.id, job_id: artifact.job_id, run_attempt: artifact.attempt }))
	}
	evidence : LifecycleAdmission.Evidence
	evidence = { schema_version: 1, profile: LifecycleAdmission.profile, repository: context.repository, source_sha: context.sha, run_id: context.run_id, producing_attempt: snapshot.producing_attempt.to_str(), compiler: context.compiler, version: LifecycleAdmission.release_version(context), topology_sha256: context.topology_sha256, workflow_ref: trusted.workflow_ref, workflow_sha: trusted.workflow_sha, obligations: $obligations, artifacts: $artifacts, files: $files, producer_evidence: $producer_evidence }
	report = LifecycleAdmission.validate(trusted, snapshot.topology, snapshot.observations, evidence, actual)?
	validate_sboms!(trusted, snapshot.topology, evidence, candidate)?
	write!("${output}/evidence.json", Json.to_str(evidence))?
	write!("${output}/admission.json", Json.to_str(report))?
	match Env.var_str!("GITHUB_OUTPUT") {
		Ok(output_path) if output_path != "" => {
			workspace = env!("GITHUB_WORKSPACE")?.drop_suffix("/")
			candidate_prefix = "${workspace}/"
			if !candidate.starts_with(candidate_prefix) {
				return Err("ADMIT_PUBLISH: final candidate directory must be inside GITHUB_WORKSPACE")
			}
			relative = candidate.drop_prefix(candidate_prefix).drop_suffix("/")
			if !LifecyclePlan.safe_path(relative) {
				return Err("ADMIT_PUBLISH: unsafe candidate workspace path")
			}
			assets = report.assets.map(|file| "${relative}/${file.path}")
			var $delimiter = "ROC_ADMITTED_ASSETS"
			while assets.contains($delimiter) {
				$delimiter = $delimiter.concat("X")
			}
			append!(Path.utf8(output_path), "admission-file=${output}/admission.json\nnotes-file=${relative}/release-notes.md\nexplicit-assets<<${$delimiter}\n${Str.join_with(assets, "\n")}\n${$delimiter}\n")?
		}
		_ => {}
	}
	Stdout.line!("PASS independently admitted actual jobs, immutable transports, complete traces and final bytes").map_err(|_| "cannot display admission result")
}

append! = |path, text| {
	old = Path.read_utf8!(path).map_err(|_| "cannot read workflow output file")?
	Path.write_utf8!(path, old.concat(text)).map_err(|_| "cannot append workflow output")
}
