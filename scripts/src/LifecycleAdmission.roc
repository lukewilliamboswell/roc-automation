import GitHub
import LifecycleJson
import LifecyclePlan

## Independent release-evidence admission. All arguments are inert data. Only
## the trusted controller may supply API observations and the expected context.
LifecycleAdmission := [].{
	profile = "roc-automation-release-evidence-v1"
	Trusted : { context : LifecyclePlan.Context, changes : LifecyclePlan.Changes, server_url : Str, run_head_sha : Str, workflow_ref : Str, workflow_sha : Str, publish : Bool, compositions : List({ path : Str, text : Str, sha256 : Str }) }
	Job : { id : U64, name : Str, conclusion : Str, run_id : U64, run_attempt : U64, head_sha : Str, labels : List(Str) }
	RemoteArtifact : { id : U64, name : Str, digest : Str, expired : Bool, workflow_run : { id : U64, head_sha : Str } }
	Observations : { complete : Bool, jobs : List(Job), artifacts : List(RemoteArtifact) }
	File : { path : Str, role : Str, subject : Str, target : Str, size : U64, sha256 : Str }
	Obligation : { id : Str, job_id : U64, run_attempt : U64 }
	Artifact : { id : U64, digest : Str, job_id : U64, run_attempt : U64, trace_sha256 : Str, obligations : List(Str), files : List(Str), metadata : List({ path : Str, size : U64, sha256 : Str }) }
	Evidence : { schema_version : U64, profile : Str, repository : Str, source_sha : Str, run_id : Str, producing_attempt : Str, compiler : Str, version : Str, topology_sha256 : Str, workflow_ref : Str, workflow_sha : Str, obligations : List(Obligation), artifacts : List(Artifact), files : List(File), producer_evidence : List({ id : Str, sha256 : Str, document : Str }) }
	ObservedFile : { path : Str, size : U64, sha256 : Str }
	Report : { schema_version : U64, profile : Str, repository : Str, source_sha : Str, run_id : Str, producing_attempt : Str, compiler : Str, version : Str, topology_sha256 : Str, workflow_ref : Str, workflow_sha : Str, policy_version : Str, schema_profile : Str, verdict : Str, publish : Bool, assets : List(File), notes_sha256 : Str, artifacts : List(Artifact) }

	## Reconstruct every obligation and asset role from reviewed topology, not
	## the producer's matrix, pass flag or claimed inventory.
	validate : Trusted, LifecyclePlan.Topology, Observations, Evidence, List(ObservedFile) -> Try(Report, Str)
	validate = |trusted, topology, observed, evidence, actual| {
		context = trusted.context
		version = release_version(context)
		if trusted.server_url != "https://github.com" or !LifecyclePlan.sha(trusted.run_head_sha) or !LifecyclePlan.sha(trusted.workflow_sha) or trusted.workflow_ref != "lukewilliamboswell/roc-automation/.github/workflows/release-lifecycle.yml@${trusted.workflow_sha}" {
			return Err("ADMIT_CONTEXT: unsupported server or workflow identity")
		}
		if trusted.publish and (context.event != "workflow_dispatch" or context.ref != "refs/heads/${context.default_branch}" or trusted.run_head_sha != context.sha) {
			return Err("ADMIT_EVENT: publication requires dispatch on the actual default branch")
		}
		plan = LifecyclePlan.plan(context, topology, trusted.changes).map_err(|reason| "ADMIT_TOPOLOGY: ${reason}")?
		if evidence.schema_version != 1 or evidence.profile != profile or evidence.repository != context.repository or evidence.source_sha != context.sha or evidence.run_id != context.run_id or evidence.compiler != context.compiler or evidence.version != version or evidence.topology_sha256 != context.topology_sha256 or evidence.workflow_ref != trusted.workflow_ref or evidence.workflow_sha != trusted.workflow_sha {
			return Err("ADMIT_BINDING: evidence does not describe this source, run, compiler, version, topology and workflow")
		}
		attempt = U64.from_str(evidence.producing_attempt).map_err(|_| "ADMIT_ATTEMPT: invalid producing attempt")?
		current = U64.from_str(context.run_attempt).map_err(|_| "ADMIT_ATTEMPT: invalid current attempt")?
		run = U64.from_str(context.run_id).map_err(|_| "ADMIT_RUN: invalid run ID")?
		if attempt == 0 or attempt > current or run == 0 or !observed.complete or observed.jobs.len() > 4096 or observed.artifacts.len() > 4096 {
			return Err("ADMIT_OBSERVATIONS: incomplete API evidence or invalid run attempt")
		}
		if !unique_ids(observed.jobs.map(|job| job.id)) or !unique_ids(observed.artifacts.map(|artifact| artifact.id)) {
			return Err("ADMIT_IDENTITIES: duplicate API job or artifact IDs")
		}
		if evidence.obligations.len() != plan.obligations.len() or !unique(evidence.obligations.map(|item| item.id)) {
			return Err("ADMIT_OBLIGATIONS: missing, duplicate or undeclared obligations")
		}
		for required in plan.obligations {
			claim = evidence.obligations.find_first(|item| item.id == required.id).map_err(|_| "ADMIT_OBLIGATIONS: missing ${required.id}")?
			if claim.run_attempt != attempt {
				return Err("ADMIT_GENERATION: obligations from different producing attempts")
			}
			display = job_name(required)?
			matching = observed.jobs.keep_if(|job| job.run_attempt == claim.run_attempt and (job.name == display or job.name.ends_with(" / ${display}")))
			job = match matching {
				[only] => only
				_ => return Err("ADMIT_JOB: missing or ambiguous actual job for ${required.id}")
			}
			if job.id != claim.job_id or job.id == 0 or job.run_id != run or job.head_sha != trusted.run_head_sha or job.conclusion != "success" or !job.labels.contains(required.runner) {
				return Err("ADMIT_JOB: wrong source, run, attempt, runner or conclusion for ${required.id}")
			}
		}
		if evidence.files.len() > 4096 or evidence.files.len() != plan.assets.len() or !unique(evidence.files.map(|file| lower(file.path))) {
			return Err("ADMIT_FILES: empty, duplicate, missing or undeclared file set")
		}
		for file in evidence.files {
			if !LifecyclePlan.safe_path(file.path) or file.path.to_utf8().len() > 240 or file.size > 536870912 or !LifecyclePlan.digest(file.sha256) {
				return Err("ADMIT_FILE: unsafe path, excessive size or invalid digest")
			}
			matches = plan.assets.keep_if(|rule| rule.role == file.role and rule.subject == file.subject and rule.target == file.target and matches_path(rule.path, file.path))
			if matches.len() != 1 {
				return Err("ADMIT_ROLE: undeclared subject, role, target or path: ${file.path}")
			}
		}
		for rule in plan.assets {
			if evidence.files.keep_if(|file| rule.role == file.role and rule.subject == file.subject and rule.target == file.target and matches_path(rule.path, file.path)).len() != 1 {
				return Err("ADMIT_ROLE: required role has missing or ambiguous files")
			}
		}
		# Sidecars for a hash-named bundle must use the SAME hash-named subject,
		# rather than independently matching another valid-looking bundle name.
		for file in evidence.files {
			if ["spdx.json", "cdx.json", "sbom-manifest.json"].contains(file.role) {
				subjects = evidence.files.keep_if(|subject| subject.subject == file.subject and subject.target == file.target and ["bundle", "host", "binary"].contains(subject.role))
				subject = match subjects {
					[only] => only
					_ => return Err("ADMIT_SUBJECT: sidecar lacks one final subject")
				}
				suffix = ".${file.role}"
				if file.path != "${subject.path}${suffix}" {
					return Err("ADMIT_SUBJECT: sidecar name binds different subject bytes")
				}
			}
		}
		if actual.len() != evidence.files.len() or !unique(actual.map(|file| lower(file.path))) {
			return Err("ADMIT_BYTES: actual directory differs from declared complete file set")
		}
		for file in evidence.files {
			actual_file = actual.find_first(|item| item.path == file.path).map_err(|_| "ADMIT_BYTES: missing file ${file.path}")?
			if actual_file.size != file.size or actual_file.sha256 != file.sha256 {
				return Err("ADMIT_BYTES: size or SHA-256 differs for ${file.path}")
			}
		}
		if (evidence.artifacts.is_empty() and !evidence.files.is_empty()) or evidence.artifacts.len() > 128 or !unique_ids(evidence.artifacts.map(|artifact| artifact.id)) {
			return Err("ADMIT_ARTIFACTS: missing or duplicate artifact IDs")
		}
		var $transported = []
		for artifact in evidence.artifacts {
			if artifact.run_attempt != attempt or artifact.id == 0 or !LifecyclePlan.digest(artifact.trace_sha256) or artifact.obligations.is_empty() or !unique(artifact.obligations) or !unique(artifact.files) or !artifact.digest.starts_with("sha256:") or !LifecyclePlan.digest(artifact.digest.drop_prefix("sha256:")) {
				return Err("ADMIT_ARTIFACT: invalid producing identity or digest")
			}
			remote = observed.artifacts.find_first(|item| item.id == artifact.id).map_err(|_| "ADMIT_ARTIFACT: artifact not present in selected run")?
			if remote.expired or remote.digest != artifact.digest or remote.workflow_run.id != run or remote.workflow_run.head_sha != trusted.run_head_sha {
				return Err("ADMIT_ARTIFACT: expired, substituted or wrong-run artifact")
			}
			claims = evidence.obligations.keep_if(|item| item.job_id == artifact.job_id and item.run_attempt == artifact.run_attempt)
			if claims.is_empty() {
				return Err("ADMIT_ARTIFACT: unverified producer job")
			}
			producer = plan.obligations.find_first(|item| claims.any(|claim| claim.id == item.id)).map_err(|_| "ADMIT_ARTIFACT: no expected producer")?
			key = if producer.stage == "sbom" producer.id else "${producer.stage}-${producer.runner}"
			if remote.name != "lifecycle-${key}-${attempt.to_str()}" {
				return Err("ADMIT_ARTIFACT: wrong immutable upload name for actual producer job")
			}
			if !unique(artifact.metadata.map(|item| lower(item.path))) or artifact.metadata.len() > 1024 or !artifact.metadata.all(|item| metadata_path(producer.stage, item.path) and item.size <= 1048576 and LifecyclePlan.digest(item.sha256)) or artifact.metadata.fold(0.U64, |sum, item| sum + item.size) > 33554432 {
				return Err("ADMIT_METADATA: unsafe or excessive inert diagnostics")
			}
			if claims.len() != artifact.obligations.len() or !claims.all(|claim| artifact.obligations.contains(claim.id)) {
				return Err("ADMIT_TRACE: artifact trace does not cover exactly its job's obligations")
			}
			$transported = $transported.concat(artifact.files)
		}
		if LifecyclePlan.unique($transported).len() != evidence.files.len() or !evidence.files.all(|file| $transported.contains(file.path)) {
			return Err("ADMIT_ARTIFACTS: artifact membership differs from complete file inventory")
		}
		claimed_ids = evidence.artifacts.fold([], |ids, artifact| ids.concat(artifact.obligations))
		if !unique(claimed_ids) or claimed_ids.len() != evidence.obligations.len() or !evidence.obligations.all(|item| claimed_ids.contains(item.id)) {
			return Err("ADMIT_TRACE: missing, duplicated or undeclared trace obligations")
		}
		notes_sha256 = if plan.assets.is_empty() and !trusted.publish "" else evidence.files.find_first(|file| file.path == "release-notes.md").map_err(|_| "ADMIT_NOTES: missing exact rendered notes")?.sha256
		Ok({ schema_version: 1, profile, repository: context.repository, source_sha: context.sha, run_id: context.run_id, producing_attempt: evidence.producing_attempt, compiler: context.compiler, version, topology_sha256: context.topology_sha256, workflow_ref: trusted.workflow_ref, workflow_sha: trusted.workflow_sha, policy_version: "roc-automation-release-policy-1", schema_profile: "release-evidence-1", verdict: "admitted", publish: trusted.publish, assets: evidence.files, notes_sha256, artifacts: evidence.artifacts })
	}

	parse_evidence : Str -> Try(Evidence, Str)
	parse_evidence = |text| {
		raw = LifecycleJson.decode_limit(text, 1048576).map_err(LifecycleJson.message)?
		LifecycleJson.object(raw, ["schema_version", "profile", "repository", "source_sha", "run_id", "producing_attempt", "compiler", "version", "topology_sha256", "workflow_ref", "workflow_sha", "obligations", "artifacts", "files", "producer_evidence"])?
		for item in LifecycleJson.array(LifecycleJson.field(raw, "obligations")?)? {
			LifecycleJson.object(item, ["id", "job_id", "run_attempt"])?
		}
		check_artifacts(LifecycleJson.array(LifecycleJson.field(raw, "artifacts")?)?)?
		check_files(LifecycleJson.array(LifecycleJson.field(raw, "files")?)?)?
		for record in LifecycleJson.array(LifecycleJson.field(raw, "producer_evidence")?)? {
			LifecycleJson.object(record, ["id", "sha256", "document"])?
		}
		parsed : Try(Evidence, _)
		parsed = Json.parse(text)
		parsed.map_err(|_| "ADMIT_JSON: invalid evidence field types")
	}
	parse_trusted : Str -> Try(Trusted, Str)
	parse_trusted = |text| {
		raw = LifecycleJson.decode_limit(text, 4194304).map_err(LifecycleJson.message)?
		LifecycleJson.object(raw, ["context", "changes", "server_url", "run_head_sha", "workflow_ref", "workflow_sha", "publish", "compositions"])?
		LifecycleJson.object(LifecycleJson.field(raw, "context")?, ["event", "ref", "default_branch", "repository", "sha", "run_id", "run_attempt", "compiler", "version", "full_dry_run", "topology_sha256"])?
		changes = LifecycleJson.field(raw, "changes")?
		LifecycleJson.object(changes, ["complete", "files"])?
		for change in LifecycleJson.array(LifecycleJson.field(changes, "files")?)? {
			LifecycleJson.object(change, ["status", "old_path", "new_path"])?
		}
		for item in LifecycleJson.array(LifecycleJson.field(raw, "compositions")?)? {
			LifecycleJson.object(item, ["path", "text", "sha256"])?
		}
		parsed : Try(Trusted, _)
		parsed = Json.parse(text)
		found = parsed.map_err(|_| "ADMIT_JSON: invalid trusted context types")?
		_ = LifecyclePlan.parse_context(Json.to_str(found.context))?
		_ = LifecyclePlan.parse_changes(Json.to_str(found.changes))?
		Ok(found)
	}
	parse_observations : Str -> Try(Observations, Str)
	parse_observations = |text| {
		raw = LifecycleJson.decode_limit(text, 4194304).map_err(LifecycleJson.message)?
		LifecycleJson.object(raw, ["complete", "jobs", "artifacts"])?
		for job in LifecycleJson.array(LifecycleJson.field(raw, "jobs")?)? {
			LifecycleJson.object(job, ["id", "name", "conclusion", "run_id", "run_attempt", "head_sha", "labels"])?
		}
		for artifact in LifecycleJson.array(LifecycleJson.field(raw, "artifacts")?)? {
			LifecycleJson.object(artifact, ["id", "name", "digest", "expired", "workflow_run"])?
			LifecycleJson.object(LifecycleJson.field(artifact, "workflow_run")?, ["id", "head_sha"])?
		}
		parsed : Try(Observations, _)
		parsed = Json.parse(text)
		parsed.map_err(|_| "ADMIT_JSON: invalid API observation types")
	}
	parse_report : Str -> Try(Report, Str)
	parse_report = |text| {
		raw = LifecycleJson.decode_limit(text, 1048576).map_err(LifecycleJson.message)?
		LifecycleJson.object(raw, ["schema_version", "profile", "repository", "source_sha", "run_id", "producing_attempt", "compiler", "version", "topology_sha256", "workflow_ref", "workflow_sha", "policy_version", "schema_profile", "verdict", "publish", "assets", "notes_sha256", "artifacts"])?
		check_files(LifecycleJson.array(LifecycleJson.field(raw, "assets")?)?)?
		check_artifacts(LifecycleJson.array(LifecycleJson.field(raw, "artifacts")?)?)?
		parsed : Try(Report, _)
		parsed = Json.parse(text)
		parsed.map_err(|_| "ADMIT_JSON: invalid admission report types")
	}
	check_files = |files| {
		for file in files {
			LifecycleJson.object(file, ["path", "role", "subject", "target", "size", "sha256"])?
		}
		Ok({})
	}
	check_artifacts = |artifacts| {
		for artifact in artifacts {
			LifecycleJson.object(artifact, ["id", "digest", "job_id", "run_attempt", "trace_sha256", "obligations", "files", "metadata"])?
			for file in LifecycleJson.array(LifecycleJson.field(artifact, "metadata")?)? {
				LifecycleJson.object(file, ["path", "size", "sha256"])?
			}
		}
		Ok({})
	}

	## Defense in depth at the existing publisher. The report is not authority
	## by itself: the pinned controller must rerun admission from trusted API
	## observations in this job, then compare all local bytes before any write.
	publication : Report, Str, Str, Str, Str, Str, List(ObservedFile) -> Try({}, Str)
	publication = |report, repository, source_sha, run_id, version, notes_sha256, files| {
		if report.schema_version != 1 or report.profile != profile or report.verdict != "admitted" or !report.publish or report.policy_version != "roc-automation-release-policy-1" or report.schema_profile != "release-evidence-1" or report.repository != repository or report.source_sha != source_sha or report.run_id != run_id or report.version != version or report.notes_sha256 != notes_sha256 {
			return Err("ADMIT_PUBLISH: report cannot authorize this publication context or notes")
		}
		if !unique(report.assets.map(|file| basename(file.path))) or files.len() != report.assets.len() or !unique(files.map(|file| file.path)) {
			return Err("ADMIT_PUBLISH: exact asset set required")
		}
		for expected in report.assets {
			file = files.find_first(|item| item.path == basename(expected.path)).map_err(|_| "ADMIT_PUBLISH: declared asset missing")?
			if file.size != expected.size or file.sha256 != expected.sha256 {
				return Err("ADMIT_PUBLISH: asset bytes differ from admitted bytes")
			}
		}
		Ok({})
	}
	release_version = |context| if context.event == "pull_request" "0.0.1-dry-run" else context.version
	basename = |path| path.split_on("/").last() ?? ""

	job_name = |obligation| {
		runner = obligation.runner
		match obligation.stage {
			"validate" => Ok("Validate release")
			"host" => Ok("Host (${runner})")
			"source" => Ok("Source (${runner})")
			"prepare" => Ok("Prepare (${runner})")
			"candidate" => Ok("Candidate (${runner})")
			"sbom" => Ok("SBOM (${obligation.id.drop_prefix("sbom-")})")
			"seal" => Ok("Seal")
			_ => Err("ADMIT_STAGE: unknown obligation stage")
		}
	}
	matches_path = |rule, path| match rule.split_on("{hash}") {
		[exact] => exact == path
		[prefix, suffix] => path.starts_with(prefix) and path.ends_with(suffix) and {
			hash = path.drop_prefix(prefix).drop_suffix(suffix)
			hash.to_utf8().len() >= 40 and hash.to_utf8().len() <= 64 and hash.to_utf8().all(|b| (b >= '1' and b <= '9') or (b >= 'A' and b <= 'H') or (b >= 'J' and b <= 'N') or (b >= 'P' and b <= 'Z') or (b >= 'a' and b <= 'k') or (b >= 'm' and b <= 'z'))
		}
		_ => Bool.False
	}
	metadata_path = |stage, path| LifecyclePlan.safe_path(path) and ((stage == "prepare" and path == "bundle-map.json") or (path.starts_with("diagnostics/") and !path.split_on("/").any(|part| ["cache", ".cache", "node_modules", "target", ".git"].contains(lower(part)))))
	unique_ids : List(U64) -> Bool
	unique_ids = |values| values.all(|value| values.keep_if(|other| value == other).len() == 1)
	unique : List(Str) -> Bool
	unique = |values| LifecyclePlan.unique(values).len() == values.len()
	lower = |value| Str.from_utf8_lossy(value.to_utf8().map(|b| if b >= 'A' and b <= 'Z' b + 32 else b))
}

fixture_context : LifecyclePlan.Context
fixture_context = { event: "workflow_dispatch", ref: "refs/heads/main", default_branch: "main", repository: "owner/sample", sha: Str.repeat("a", 40), run_id: "10", run_attempt: "2", compiler: "nightly-2026-10-23-18478ff", version: "1.0.0", full_dry_run: Bool.False, topology_sha256: Str.repeat("b", 64) }

fixture_topology : LifecyclePlan.Topology
fixture_topology = { schema: 1, name: "sample", profile: "package", bundles: [{ id: "package", root: "package/main.roc", prefix: "sample", placeholder: "package-url", depends_on: [] }], examples: [{ id: "examples", directory: "examples", spec: "examples/spec.json", bundles: ["package"], target_ids: ["linux"] }], documentation: [], target_matrix: [{ id: "linux", runner: "ubuntu-24.04", runtime_task: "" }], adapters: [], checks: [{ id: "unit", task: "check-unit", kind: "unit", target_ids: [] }], sboms: [{ subject: "package", target: "source", composition: "sbom/composition.json", supplier_imports: "" }], source_paths: ["package"] }

fixture_trusted : LifecycleAdmission.Trusted
fixture_trusted = { context: fixture_context, changes: { complete: Bool.False, files: [] }, server_url: "https://github.com", run_head_sha: fixture_context.sha, workflow_ref: "lukewilliamboswell/roc-automation/.github/workflows/release-lifecycle.yml@${Str.repeat("c", 40)}", workflow_sha: Str.repeat("c", 40), publish: Bool.True, compositions: [] }

fixture_plan = LifecyclePlan.plan(fixture_context, fixture_topology, fixture_trusted.changes) ?? crash "fixture plan invalid"

fixture_files = fixture_plan.assets.map(|rule| { path: rule.path.replace_each("{hash}", Str.repeat("a", 44)), role: rule.role, subject: rule.subject, target: rule.target, size: 3.U64, sha256: Str.repeat("d", 64) })

fixture_id = |stage| match stage {
	"source" => 1.U64
	"prepare" => 2
	"candidate" => 3
	"sbom" => 4
	"seal" => 5
	_ => 6
}

fixture_stages = ["source", "prepare", "candidate", "sbom", "seal", "validate"]

fixture_jobs = fixture_stages.map(
	|stage| {
		obligation = fixture_plan.obligations.find_first(|item| item.stage == stage) ?? crash "missing fixture stage"
		{ id: fixture_id(stage), name: LifecycleAdmission.job_name(obligation) ?? "", conclusion: "success", run_id: 10.U64, run_attempt: 1.U64, head_sha: fixture_context.sha, labels: ["ubuntu-24.04"] }
	},
)

fixture_artifacts = fixture_stages.map(|stage| { id: fixture_id(stage) + 100, digest: "sha256:${Str.repeat("e", 64)}", job_id: fixture_id(stage), run_attempt: 1.U64, trace_sha256: Str.repeat("f", 64), obligations: fixture_plan.obligations.keep_if(|item| item.stage == stage).map(|item| item.id), files: if stage == "seal" fixture_files.map(|file| file.path) else [], metadata: [] })

fixture_observed : LifecycleAdmission.Observations
fixture_observed = { complete: Bool.True, jobs: fixture_jobs, artifacts: fixture_artifacts.map(|artifact| { id: artifact.id, name: "lifecycle-${if artifact.job_id == 4 "sbom-0" else "${fixture_stages.get(artifact.job_id - 1) ?? ""}-ubuntu-24.04"}-1", digest: artifact.digest, expired: Bool.False, workflow_run: { id: 10.U64, head_sha: fixture_context.sha } }) }

fixture_evidence : LifecycleAdmission.Evidence
fixture_evidence = { schema_version: 1, profile: LifecycleAdmission.profile, repository: fixture_context.repository, source_sha: fixture_context.sha, run_id: fixture_context.run_id, producing_attempt: "1", compiler: fixture_context.compiler, version: fixture_context.version, topology_sha256: fixture_context.topology_sha256, workflow_ref: fixture_trusted.workflow_ref, workflow_sha: fixture_trusted.workflow_sha, obligations: fixture_plan.obligations.map(|item| { id: item.id, job_id: fixture_id(item.stage), run_attempt: 1.U64 }), artifacts: fixture_artifacts, files: fixture_files, producer_evidence: [] }

fixture_actual = fixture_files.map(|file| { path: file.path, size: file.size, sha256: file.sha256 })

fixture_admit = |trusted, observations, evidence, actual| LifecycleAdmission.validate(trusted, fixture_topology, observations, evidence, actual)

expect fixture_admit(fixture_trusted, fixture_observed, fixture_evidence, fixture_actual).is_ok()
# A publish-only retry keeps the original successful producing attempt.
expect fixture_admit({ ..fixture_trusted, context: { ..fixture_context, run_attempt: "3" } }, fixture_observed, fixture_evidence, fixture_actual).is_ok()
expect fixture_admit({ ..fixture_trusted, context: { ..fixture_context, ref: "refs/heads/other" } }, fixture_observed, fixture_evidence, fixture_actual).is_err()
expect fixture_admit({ ..fixture_trusted, workflow_sha: Str.repeat("d", 40) }, fixture_observed, fixture_evidence, fixture_actual).is_err()
expect fixture_admit(fixture_trusted, { ..fixture_observed, complete: Bool.False }, fixture_evidence, fixture_actual).is_err()
expect fixture_admit(fixture_trusted, { ..fixture_observed, jobs: fixture_jobs.drop_first(1) }, fixture_evidence, fixture_actual).is_err()
expect fixture_admit(fixture_trusted, { ..fixture_observed, jobs: fixture_jobs.concat(fixture_jobs.take_first(1)) }, fixture_evidence, fixture_actual).is_err()
expect ["failure", "cancelled", "skipped", "timed_out", ""].all(|conclusion| fixture_admit(fixture_trusted, { ..fixture_observed, jobs: fixture_jobs.map(|job| { ..job, conclusion }) }, fixture_evidence, fixture_actual).is_err())
expect fixture_admit(fixture_trusted, { ..fixture_observed, jobs: fixture_jobs.map(|job| { ..job, run_attempt: 2 }) }, fixture_evidence, fixture_actual).is_err()
expect fixture_admit(fixture_trusted, { ..fixture_observed, jobs: fixture_jobs.map(|job| { ..job, head_sha: Str.repeat("b", 40) }) }, fixture_evidence, fixture_actual).is_err()
expect fixture_admit(fixture_trusted, { ..fixture_observed, jobs: fixture_jobs.map(|job| { ..job, labels: ["windows-2025"] }) }, fixture_evidence, fixture_actual).is_err()
expect fixture_admit(fixture_trusted, fixture_observed, { ..fixture_evidence, repository: "other/repo" }, fixture_actual).is_err()
expect fixture_admit(fixture_trusted, fixture_observed, { ..fixture_evidence, source_sha: Str.repeat("b", 40) }, fixture_actual).is_err()
expect fixture_admit(fixture_trusted, fixture_observed, { ..fixture_evidence, compiler: "nightly-other" }, fixture_actual).is_err()
expect fixture_admit(fixture_trusted, fixture_observed, { ..fixture_evidence, version: "2.0.0" }, fixture_actual).is_err()
expect fixture_admit(fixture_trusted, fixture_observed, { ..fixture_evidence, run_id: "11" }, fixture_actual).is_err()
expect fixture_admit(fixture_trusted, fixture_observed, { ..fixture_evidence, producing_attempt: "3" }, fixture_actual).is_err()
expect fixture_admit(fixture_trusted, fixture_observed, { ..fixture_evidence, obligations: fixture_evidence.obligations.drop_first(1) }, fixture_actual).is_err()
expect fixture_admit(fixture_trusted, fixture_observed, { ..fixture_evidence, files: fixture_files.map(|file| { ..file, target: "wrong" }) }, fixture_actual).is_err()
expect fixture_admit(fixture_trusted, fixture_observed, fixture_evidence, fixture_actual.map(|file| { ..file, sha256: Str.repeat("a", 64) })).is_err()
expect fixture_admit(fixture_trusted, fixture_observed, fixture_evidence, fixture_actual.drop_first(1)).is_err()
expect fixture_admit(fixture_trusted, fixture_observed, { ..fixture_evidence, files: fixture_files.map(|file| { ..file, path: "../escape" }) }, fixture_actual).is_err()
expect fixture_admit(fixture_trusted, { ..fixture_observed, artifacts: fixture_observed.artifacts.map(|artifact| { ..artifact, expired: Bool.True }) }, fixture_evidence, fixture_actual).is_err()
expect fixture_admit(fixture_trusted, { ..fixture_observed, artifacts: fixture_observed.artifacts.map(|artifact| { ..artifact, digest: "sha256:${Str.repeat("a", 64)}" }) }, fixture_evidence, fixture_actual).is_err()
expect fixture_admit(fixture_trusted, { ..fixture_observed, artifacts: fixture_observed.artifacts.map(|artifact| { ..artifact, name: "wrong" }) }, fixture_evidence, fixture_actual).is_err()
expect LifecycleAdmission.parse_evidence("{\"schema_version\":1,\"schema_version\":1}").is_err()
expect LifecycleAdmission.parse_report("{\"schema_version\":1,\"unknown\":true}").is_err()

fixture_report = fixture_admit(fixture_trusted, fixture_observed, fixture_evidence, fixture_actual) ?? crash "fixture admission failed"

fixture_published = fixture_actual.map(|file| { ..file, path: LifecycleAdmission.basename(file.path) })

fixture_publication = |report, files| LifecycleAdmission.publication(report, fixture_context.repository, fixture_context.sha, fixture_context.run_id, fixture_context.version, Str.repeat("d", 64), files)
expect fixture_publication(fixture_report, fixture_published).is_ok()
expect fixture_publication({ ..fixture_report, publish: Bool.False }, fixture_published).is_err()
expect fixture_publication({ ..fixture_report, run_id: "11" }, fixture_published).is_err()
expect fixture_publication({ ..fixture_report, source_sha: Str.repeat("b", 40) }, fixture_published).is_err()
expect fixture_publication({ ..fixture_report, notes_sha256: Str.repeat("a", 64) }, fixture_published).is_err()
expect fixture_publication(fixture_report, fixture_published.drop_first(1)).is_err()
expect fixture_publication(fixture_report, fixture_published.map(|file| { ..file, sha256: Str.repeat("a", 64) })).is_err()
