import GitHub
import LifecycleAdmission
import LifecyclePlan

## Select inert documentation bytes after the trusted workflow has observed
## successful publication. A report alone grants no Pages deployment authority.
LifecyclePages := [].{
	Binding : { event : Str, repository : Str, source_sha : Str, run_id : Str }
	Selection : { documentation_id : Str, archive : LifecycleAdmission.File, root : Str }

	select : LifecycleAdmission.Report, LifecyclePlan.Topology, Str, Str, Binding -> Try(Selection, Str)
	select = |report, topology, topology_sha256, requested, binding| {
		_ = LifecyclePlan.validate(topology)?
		if binding.event != "workflow_dispatch" or !GitHub.valid_repository(binding.repository) or !LifecyclePlan.sha(binding.source_sha) or !GitHub.is_digits(binding.run_id) or binding.run_id.starts_with("0") {
			return Err("PAGES_CONTEXT: restoration requires this dispatch's repository, source and run")
		}
		if report.schema_version != 1 or report.profile != LifecycleAdmission.profile or report.verdict != "admitted" or !report.publish or report.policy_version != "roc-automation-release-policy-1" or report.schema_profile != "release-evidence-1" or report.validator_version != "roc-automation-release-admit-v1" {
			return Err("PAGES_ADMISSION: a supported publication admission report is required")
		}
		if report.repository != binding.repository or report.source_sha != binding.source_sha or report.run_id != binding.run_id or !LifecyclePlan.version(report.version) or report.version == "0.0.1-dry-run" or !LifecyclePlan.sha(report.workflow_sha) or report.workflow_ref != "lukewilliamboswell/roc-automation/.github/workflows/release-lifecycle.yml@${report.workflow_sha}" {
			return Err("PAGES_BINDING: admission belongs to another source, run, version or workflow")
		}
		if !LifecyclePlan.digest(topology_sha256) or topology_sha256 != report.topology_sha256 {
			return Err("PAGES_TOPOLOGY: reviewed topology bytes differ from admission")
		}
		if !LifecyclePlan.id(requested) {
			return Err("PAGES_DOCUMENTATION: select an explicit reviewed manual ID")
		}
		documentation = topology.documentation.find_first(|item| item.id == requested).map_err(|_| "PAGES_DOCUMENTATION: manual ID is not declared by reviewed topology")?
		name = if topology.documentation.len() == 1 topology.name else "${topology.name}-${documentation.id}"
		root = "${name}-docs-${report.version}"
		expected = "${root}.zip"
		archive = match report.assets.keep_if(|file| file.role == "documentation" and file.subject == documentation.id and file.target == "source" and file.path == expected) {
			[only] => only
			_ => return Err("PAGES_ASSET: the selected manual has no unique admitted ZIP")
		}
		if archive.size == 0 or archive.size > 50_000_000 or !LifecyclePlan.digest(archive.sha256) or !LifecyclePlan.safe_path(archive.path) or report.assets.keep_if(|file| LifecyclePlan.lower(file.path) == LifecyclePlan.lower(expected)).len() != 1 {
			return Err("PAGES_ASSET: unsafe, ambiguous or excessive documentation identity")
		}
		Ok({ documentation_id: documentation.id, archive, root })
	}
}

pages_topology : LifecyclePlan.Topology
pages_topology = { schema: 1, name: "sample", profile: "package", sbom_author: "Organization: Fixture contributors", bundles: [{ id: "package", root: "package/main.roc", prefix: "sample", placeholder: "package-url", depends_on: [] }], examples: [{ id: "examples", directory: "examples", spec: "examples/spec.json", bundles: ["package"], target_ids: ["linux"] }], documentation: [{ id: "guide", directory: "docs/guide", pdf: Bool.True }, { id: "reference", directory: "docs/reference", pdf: Bool.False }], target_matrix: [{ id: "linux", runner: "ubuntu-24.04", runtime_task: "" }], adapters: [], checks: [], sboms: [{ subject: "package", target: "source", composition: "sbom/composition.json", supplier_imports: "" }], source_paths: ["package"] }

pages_binding : LifecyclePages.Binding
pages_binding = { event: "workflow_dispatch", repository: "owner/sample", source_sha: Str.repeat("a", 40), run_id: "10" }

pages_guide : LifecycleAdmission.File
pages_guide = { path: "sample-guide-docs-1.2.3.zip", role: "documentation", subject: "guide", target: "source", size: 10, sha256: Str.repeat("d", 64) }

pages_reference : LifecycleAdmission.File
pages_reference = { path: "sample-reference-docs-1.2.3.zip", role: "documentation", subject: "reference", target: "source", size: 20, sha256: Str.repeat("e", 64) }

pages_report : LifecycleAdmission.Report
pages_report = { schema_version: 1, profile: LifecycleAdmission.profile, repository: pages_binding.repository, source_sha: pages_binding.source_sha, run_id: pages_binding.run_id, producing_attempt: "1", compiler: "nightly-2026-10-23-18478ff", version: "1.2.3", topology_sha256: Str.repeat("b", 64), workflow_ref: "lukewilliamboswell/roc-automation/.github/workflows/release-lifecycle.yml@${Str.repeat("c", 40)}", workflow_sha: Str.repeat("c", 40), policy_version: "roc-automation-release-policy-1", schema_profile: "release-evidence-1", validator_version: "roc-automation-release-admit-v1", verdict: "admitted", publish: Bool.True, assets: [pages_guide, pages_reference], intermediates: [], notes_sha256: Str.repeat("f", 64), artifacts: [] }

expect LifecyclePages.select(pages_report, pages_topology, Str.repeat("b", 64), "guide", pages_binding) == Ok({ documentation_id: "guide", archive: pages_guide, root: "sample-guide-docs-1.2.3" })
expect LifecyclePages.select(pages_report, pages_topology, Str.repeat("b", 64), "reference", pages_binding).map_ok(|value| value.archive) == Ok(pages_reference)
expect ["", "unknown", "../guide", "Guide"].all(|id| LifecyclePages.select(pages_report, pages_topology, Str.repeat("b", 64), id, pages_binding).is_err())
expect LifecyclePages.select(pages_report, pages_topology, Str.repeat("0", 64), "guide", pages_binding).is_err()
expect LifecyclePages.select({ ..pages_report, publish: Bool.False }, pages_topology, Str.repeat("b", 64), "guide", pages_binding).is_err()
expect LifecyclePages.select({ ..pages_report, verdict: "passed" }, pages_topology, Str.repeat("b", 64), "guide", pages_binding).is_err()
expect LifecyclePages.select({ ..pages_report, validator_version: "unknown" }, pages_topology, Str.repeat("b", 64), "guide", pages_binding).is_err()
expect LifecyclePages.select({ ..pages_report, repository: "other/sample" }, pages_topology, Str.repeat("b", 64), "guide", pages_binding).is_err()
expect LifecyclePages.select({ ..pages_report, run_id: "11" }, pages_topology, Str.repeat("b", 64), "guide", pages_binding).is_err()
expect LifecyclePages.select({ ..pages_report, source_sha: Str.repeat("0", 40) }, pages_topology, Str.repeat("b", 64), "guide", pages_binding).is_err()
expect LifecyclePages.select(pages_report, pages_topology, Str.repeat("b", 64), "guide", { ..pages_binding, event: "pull_request" }).is_err()
expect LifecyclePages.select({ ..pages_report, assets: [pages_reference] }, pages_topology, Str.repeat("b", 64), "guide", pages_binding).is_err()
expect LifecyclePages.select({ ..pages_report, assets: [pages_guide, pages_guide] }, pages_topology, Str.repeat("b", 64), "guide", pages_binding).is_err()
expect LifecyclePages.select({ ..pages_report, assets: [{ ..pages_guide, target: "native" }] }, pages_topology, Str.repeat("b", 64), "guide", pages_binding).is_err()
expect LifecyclePages.select({ ..pages_report, assets: [{ ..pages_guide, size: 50_000_001 }] }, pages_topology, Str.repeat("b", 64), "guide", pages_binding).is_err()
