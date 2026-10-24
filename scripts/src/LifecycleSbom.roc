import "../../actions/setup-blueprint/sbom-tools.json" as tool_registry : Str
import LifecycleJson
import LifecyclePlan

## Reviewed offline semantic profile for Blueprint's SPDX 3.0.1/CycloneDX 1.7
## handoff. No schemas, references, supplier URLs or advisory data are fetched.
LifecycleSbom := [].{
	Subject : { path : Str, size : U64, sha256 : Str, target : Str }
	Sidecar : { path : Str, size : U64, sha256 : Str }
	Coverage : { scope : Str, status : Str, reason : Str, contact : Str }
	Component : { id : Str, name : Str, producer : Str, version : Str, identity : Str, download : Str, sha256 : Str, license : Str, license_url : Str, modified : Str, target : Str, origin : Str }
	Edge : { from : Str, to : Str, relation : Str, scope : Str, basis : Str }
	Disposition : { input : Str, component : Str, reason : Str }
	Inventory : { target : Str, components : List(Component), relationships : List(Edge), coverage : List(Coverage), inputs : List(Disposition), inspected : List(Str) }
	SemanticEvidence : { profile : Str, revision : Str, target : Str, coverage : List(Coverage), dispositions : List(Disposition), inspected : List(Str) }
	Manifest : { profile : Str, subject : { filename : Str, version : Str, target : Str, sha256 : Str, size : U64 }, inventory_sha256 : Str, context : { namespace : Str, revision : Str, created : Str, generator : Str }, sidecars : List({ filename : Str, format : Str, sha256 : Str, size : U64 }) }

	validate : Subject, Str, Str, Str, Str, Str, Str, Str, Str, List(Sidecar) -> Try({}, Str)
	validate = |subject, version, repository, source_sha, manifest_text, spdx_text, cdx_text, composition_text, imports_text, sidecars| {
		manifest_raw = decode(manifest_text)?
		LifecycleJson.object(manifest_raw, ["profile", "subject", "inventory_sha256", "context", "sidecars"])?
		LifecycleJson.object(LifecycleJson.field(manifest_raw, "subject")?, ["filename", "version", "target", "sha256", "size"])?
		LifecycleJson.object(LifecycleJson.field(manifest_raw, "context")?, ["namespace", "revision", "created", "generator"])?
		for item in array_field(manifest_raw, "sidecars")? {
			LifecycleJson.object(item, ["filename", "format", "sha256", "size"])?
		}
		parsed : Try(Manifest, _)
		parsed = Json.parse(manifest_text)
		manifest = parsed.map_err(|_| "SBOM_MANIFEST: invalid field types")?
		name = basename(subject.path)
		if manifest.profile != "blueprint-spdx-3.0-v1" or manifest.subject != { filename: name, version, target: subject.target, sha256: subject.sha256, size: subject.size } or !LifecyclePlan.digest(manifest.inventory_sha256) {
			return Err("SBOM_SUBJECT: manifest binds a different subject, target, version or digest")
		}
		namespace = manifest.context.namespace
		if !namespace.starts_with("https://github.com/${repository}/sbom/${version}/${source_sha}/") or !namespace.contains("/${name}/revision-") or manifest.context.revision == "" or !manifest.context.revision.to_utf8().all(|b| b >= '0' and b <= '9') or manifest.context.created == "" or !manifest.context.generator.starts_with("Tool: blueprint-sbom-") {
			return Err("SBOM_CONTEXT: source or generator context differs")
		}
		if manifest.sidecars.len() != 2 or !unique(manifest.sidecars.map(|item| item.filename)) {
			return Err("SBOM_MANIFEST: exactly the two supported sidecars required")
		}
		for pair in [{ suffix: ".spdx.json", format: "spdx-3.0.1" }, { suffix: ".cdx.json", format: "cyclonedx-1.7" }] {
			declared = manifest.sidecars.find_first(|item| item.filename == "${name}${pair.suffix}" and item.format == pair.format).map_err(|_| "SBOM_MANIFEST: missing supported sidecar")?
			actual = sidecars.find_first(|item| item.path == "${subject.path}${pair.suffix}").map_err(|_| "SBOM_MANIFEST: declared sidecar absent")?
			if declared.size != actual.size or declared.sha256 != actual.sha256 {
				return Err("SBOM_MANIFEST: sidecar size or digest differs")
			}
		}
		composition = merged_composition(composition_text, imports_text)?
		spdx_document = decode(spdx_text)?
		cdx_document = decode(cdx_text)?
		spdx(subject, version, namespace, spdx_document, composition)?
		cdx(subject, version, namespace, cdx_document, composition)?
		consistent_formats(namespace, spdx_document, cdx_document)
	}

	spdx = |subject, version, namespace, document, composition| {
		if string_field(document, "@context")? != "https://spdx.org/rdf/3.0.1/spdx-context.jsonld" {
			return Err("SBOM_SPDX: unsupported SPDX context")
		}
		graph = array_field(document, "@graph")?
		if graph.is_empty() or graph.len() > 4096 {
			return Err("SBOM_SPDX: graph size outside supported profile")
		}
		var $ids = []
		for item in graph {
			id = string_field(item, "spdxId") ?? string_field(item, "@id")?
			if id == "" or $ids.contains(id) {
				return Err("SBOM_SPDX: duplicate or missing element identity")
			}
			$ids = $ids.append(id)
		}
		subject_id = "${namespace}#Subject"
		root = graph.find_first(|item| string_field(item, "spdxId") == Ok(subject_id)).map_err(|_| "SBOM_SPDX: final subject missing")?
		if string_field(root, "type")? != "software_Package" or string_field(root, "name")? != basename(subject.path) or string_field(root, "software_packageVersion")? != version {
			return Err("SBOM_SPDX: wrong subject identity or version")
		}
		hashes = array_field(root, "verifiedUsing")?
		if hashes.keep_if(|item| string_field(item, "type") == Ok("Hash") and string_field(item, "algorithm") == Ok("sha256") and string_field(item, "hashValue") == Ok(subject.sha256)).len() != 1 {
			return Err("SBOM_SPDX: final subject SHA-256 missing or ambiguous")
		}
		comment = decode(string_field(root, "comment")?)?
		if string_field(comment, "target")? != subject.target or string_field(comment, "digest_kind")? != "final-deliverable-sha256" {
			return Err("SBOM_SPDX: final subject target or digest semantics differs")
		}
		for kind in ["CreationInfo", "SpdxDocument", "software_Sbom"] {
			if graph.keep_if(|item| string_field(item, "type") == Ok(kind)).len() != 1 {
				return Err("SBOM_SPDX: missing or ambiguous required profile node")
			}
		}
		creation = graph.find_first(|item| string_field(item, "type") == Ok("CreationInfo")).map_err(|_| "SBOM_SPDX: missing creation info")?
		if string_field(creation, "specVersion")? != "3.0.1" or string_field(creation, "created")? == "" {
			return Err("SBOM_SPDX: invalid creation information")
		}
		document_node = graph.find_first(|item| string_field(item, "type") == Ok("SpdxDocument")).map_err(|_| "SBOM_SPDX: missing document")?
		sbom_node = graph.find_first(|item| string_field(item, "type") == Ok("software_Sbom")).map_err(|_| "SBOM_SPDX: missing SBOM")?
		if !array_field(sbom_node, "rootElement")?.any(|id| string(id) == Ok(subject_id)) or !array_field(document_node, "rootElement")?.any(|id| string(id) == string_field(sbom_node, "spdxId")) {
			return Err("SBOM_SPDX: document does not identify this final subject")
		}
		for relationship in array_field(composition, "relationships")? {
			from = "${namespace}#${string_field(relationship, "from")?}"
			to = "${namespace}#${string_field(relationship, "to")?}"
			relation = string_field(relationship, "relation")?
			scope = string_field(relationship, "scope")?
			basis = string_field(relationship, "basis")?
			if !graph.any(|item| ["Relationship", "LifecycleScopedRelationship"].contains(string_field(item, "type") ?? "") and string_field(item, "from") == Ok(from) and string_field(item, "relationshipType") == Ok(relation) and (string_field(item, "scope") ?? "") == scope and (array_field(item, "to") ?? []).any(|id| string(id) == Ok(to)) and string_field(item, "comment") == Ok(basis)) {
				return Err("SBOM_RELATIONSHIP: reviewed relationship, scope or basis omitted")
			}
		}
		for item in graph {
			for key in ["creationInfo", "from"] {
				match LifecycleJson.field(item, key) {
					Ok(value) => {
						id = string(value)?
						if !known_id($ids, id) {
							return Err("SBOM_SPDX: dangling relationship or creation-info reference")
						}
					}
					Err(_) => {}
				}
			}
			for key in ["to", "rootElement", "element", "createdBy", "createdUsing"] {
				match LifecycleJson.field(item, key) {
					Ok(value) => for reference in LifecycleJson.array(value)? {
						if !known_id($ids, string(reference)?) {
							return Err("SBOM_SPDX: dangling graph reference")
						}
					}
					Err(_) => {}
				}
			}
			if ["Relationship", "LifecycleScopedRelationship"].contains(string_field(item, "type") ?? "") {
				_ = string_field(item, "from")?
				if array_field(item, "to")?.is_empty() or string_field(item, "relationshipType")? == "" {
					return Err("SBOM_SPDX: incomplete relationship")
				}
			}
		}
		Ok({})
	}
	known_id = |ids, id| ids.contains(id) or ["expandedlicensing_NoAssertionLicense", "expandedlicensing_NoneLicense"].contains(id)

	cdx = |subject, version, namespace, document, composition| {
		if string_field(document, "bomFormat")? != "CycloneDX" or string_field(document, "specVersion")? != "1.7" {
			return Err("SBOM_CDX: unsupported CycloneDX profile")
		}
		metadata = LifecycleJson.field(document, "metadata")?
		root = LifecycleJson.field(metadata, "component")?
		if string_field(root, "bom-ref")? != "Subject" or string_field(root, "name")? != basename(subject.path) or string_field(root, "version")? != version {
			return Err("SBOM_CDX: final subject identity or version differs")
		}
		hashes = array_field(root, "hashes")?
		if hashes.keep_if(|item| string_field(item, "alg") == Ok("SHA-256") and string_field(item, "content") == Ok(subject.sha256)).len() != 1 {
			return Err("SBOM_CDX: final subject SHA-256 missing or ambiguous")
		}
		properties = array_field(root, "properties")?
		if property(properties, "blueprint:target")? != subject.target or property(properties, "blueprint:digest-kind")? != "final-deliverable-sha256" {
			return Err("SBOM_CDX: target or digest semantics differs")
		}
		all_components = components(array_field(document, "components")?, 0)?
		ids = ["Subject"].concat(all_components.map(|item| string_field(item, "bom-ref") ?? ""))
		if ids.contains("") or !unique(ids) {
			return Err("SBOM_CDX: duplicate or missing component identity")
		}
		for item in all_components {
			if string_field(item, "name")? == "" or !["application", "framework", "library", "container", "platform", "operating-system", "device", "device-driver", "firmware", "file", "machine-learning-model", "data", "cryptographic-asset"].contains(string_field(item, "type")?) {
				return Err("SBOM_CDX: invalid component profile")
			}
		}
		for reviewed_component in array_field(composition, "components")? {
			id = string_field(reviewed_component, "id")?
			emitted = all_components.find_first(|item| string_field(item, "bom-ref") == Ok(id)).map_err(|_| "SBOM_COMPONENT: reviewed component omitted")?
			for key in ["name", "version"] {
				declared = string_field(reviewed_component, key)?
				if declared != "" and string_field(emitted, key) != Ok(declared) {
					return Err("SBOM_COMPONENT: reviewed component identity changed")
				}
			}
		}
		dependencies = array_field(document, "dependencies")?
		var $dependency_ids = []
		for dependency in dependencies {
			id = string_field(dependency, "ref")?
			if !ids.contains(id) or $dependency_ids.contains(id) {
				return Err("SBOM_CDX: duplicate or dangling dependency identity")
			}
			$dependency_ids = $dependency_ids.append(id)
			for child in optional_array_field(dependency, "dependsOn")? {
				if !ids.contains(string(child)?) {
					return Err("SBOM_CDX: dangling dependency relationship")
				}
			}
		}
		meta_properties = array_field(metadata, "properties")?
		if property(meta_properties, "blueprint:namespace")? != namespace {
			return Err("SBOM_CDX: namespace differs from manifest")
		}
		evidence = decode(property(meta_properties, "blueprint:evidence")?)?
		if string_field(evidence, "profile")? != "blueprint-spdx-3.0-v1" or string_field(evidence, "target")? != subject.target {
			return Err("SBOM_EVIDENCE: unsupported profile or target")
		}
		dispositions = array_field(evidence, "dispositions")?
		for reviewed_input in array_field(composition, "inputs")? {
			if !dispositions.any(|item| string_field(item, "input") == string_field(reviewed_input, "input") and string_field(item, "component") == string_field(reviewed_input, "component") and string_field(item, "reason") == string_field(reviewed_input, "reason")) {
				return Err("SBOM_INPUT: reviewed input disposition omitted or changed")
			}
		}
		coverage = array_field(evidence, "coverage")?
		reviewed = array_field(composition, "coverage")?
		var $scopes = []
		for item in coverage {
			scope = string_field(item, "scope")?
			status = string_field(item, "status")?
			reason = string_field(item, "reason")?
			contact = string_field(item, "contact")?
			if scope == "" or $scopes.contains(scope) or !["complete", "verified-empty", "incomplete", "excluded"].contains(status) or reason == "" or contact == "" {
				return Err("SBOM_COVERAGE: invalid scope, status, reason or contact")
			}
			$scopes = $scopes.append(scope)
			justified = reviewed.any(|record| string_field(record, "scope") == Ok(scope) and string_field(record, "status") == Ok(status) and string_field(record, "reason") == Ok(reason) and string_field(record, "contact") == Ok(contact))
			if !justified {
				# Supplier gaps can add incomplete scopes, but their explanation
				# and contact must come from the reviewed composition.
				unavailable = array_field(composition, "unavailable_sboms")?
				gap_basis = unavailable.any(|record| scope == "sbom:${string_field(record, "component") ?? ""}" and reason == "No supplier SBOM: ${string_field(record, "reason") ?? ""}" and string_field(record, "contact") == Ok(contact))
				imported = optional_array_field(composition, "imported_documents")?
				import_basis = imported.any(|record| scope == "import:${string_field(record, "id") ?? ""}" and string_field(record, "contact") == Ok(contact) and reason.starts_with("Document digest and upstream subject artifact identity match; completeness and undisclosed closure remain unverified"))
				if status != "incomplete" or (!gap_basis and !import_basis) {
					return Err("SBOM_COVERAGE: claim has no reviewed coverage or supplier-gap basis")
				}
			}
		}
		for imported in optional_array_field(composition, "imported_documents")? {
			id = string_field(imported, "id")?
			document_sha256 = string_field(imported, "sha256")?
			url = string_field(imported, "url")?
			upstream_namespace = string_field(imported, "namespace")?
			if !LifecyclePlan.id(id) or !LifecyclePlan.digest(document_sha256) or !url.starts_with("https://") or !$scopes.contains("import:${id}") {
				return Err("SBOM_IMPORT: reviewed supplier binding or coverage missing")
			}
			imported_components = all_components.keep_if(|item| (string_field(item, "bom-ref") ?? "").starts_with("${id}-"))
			if imported_components.is_empty() {
				return Err("SBOM_IMPORT: reviewed supplier graph omitted")
			}
			for component in imported_components {
				modifications = property(array_field(component, "properties")?, "blueprint:modifications")?
				if !modifications.starts_with("Imported ${upstream_namespace}#") or !modifications.contains(" from ${url}; document SHA256 ${document_sha256}; upstream metadata ") {
					return Err("SBOM_IMPORT: graph is not bound to the reviewed supplier document")
				}
			}
		}
		for item in reviewed {
			if !$scopes.contains(string_field(item, "scope")?) {
				return Err("SBOM_COVERAGE: reviewed scope omitted")
			}
		}
		Ok({})
	}
	author : Str, Str, Str -> Try({}, Str)
	author = |reviewed, spdx_text, cdx_text| {
		kind = if reviewed.starts_with("Person: ") "Person" else if reviewed.starts_with("Organization: ") "Organization" else return Err("SBOM_AUTHOR: unsupported reviewed document author")
		name = reviewed.drop_prefix("${kind}: ")
		if name == "" {
			return Err("SBOM_AUTHOR: reviewed document author must name an agent")
		}
		graph = array_field(decode(spdx_text)?, "@graph")?
		creation = graph.find_first(|item| string_field(item, "type") == Ok("CreationInfo")).map_err(|_| "SBOM_AUTHOR: missing creation information")?
		author_id = match array_field(creation, "createdBy")? {
			[only] => string(only)?
			_ => return Err("SBOM_AUTHOR: exact reviewed author must create the document")
		}
		agent = graph.find_first(|item| string_field(item, "spdxId") == Ok(author_id)).map_err(|_| "SBOM_AUTHOR: missing document creator")?
		if string_field(agent, "type") != Ok(kind) or string_field(agent, "name") != Ok(name) {
			return Err("SBOM_AUTHOR: SPDX creator differs from reviewed topology")
		}
		metadata = LifecycleJson.field(decode(cdx_text)?, "metadata")?
		if kind == "Person" {
			person = match array_field(metadata, "authors")? {
				[only] => only
				_ => return Err("SBOM_AUTHOR: exact reviewed CycloneDX author required")
			}
			if string_field(person, "name") != Ok(name) {
				return Err("SBOM_AUTHOR: CycloneDX author differs from reviewed topology")
			}
		} else if string_field(LifecycleJson.field(metadata, "manufacturer")?, "name") != Ok(name) {
			return Err("SBOM_AUTHOR: CycloneDX manufacturer differs from reviewed topology")
		}
		Ok({})
	}

	consistent_formats : Str, LifecycleJson, LifecycleJson -> Try({}, Str)
	consistent_formats = |namespace, spdx_document, cdx_document| {
		spdx_nodes = array_field(spdx_document, "@graph")?.keep_if(|item| string_field(item, "type") == Ok("software_Package"))
		cdx_packages = [LifecycleJson.field(LifecycleJson.field(cdx_document, "metadata")?, "component")?].concat(components(array_field(cdx_document, "components")?, 0)?)
		if spdx_nodes.len() != cdx_packages.len() {
			return Err("SBOM_FORMATS: sidecars describe different component sets")
		}
		for component in cdx_packages {
			id = string_field(component, "bom-ref")?
			spdx_node = spdx_nodes.find_first(|item| string_field(item, "spdxId") == Ok("${namespace}#${id}")).map_err(|_| "SBOM_FORMATS: component absent from SPDX sidecar")?
			if string_field(spdx_node, "name") != string_field(component, "name") or (string_field(spdx_node, "software_packageVersion") ?? "") != (string_field(component, "version") ?? "") {
				return Err("SBOM_FORMATS: component identities differ between sidecars")
			}
			spdx_hashes = optional_array_field(spdx_node, "verifiedUsing")?.keep_if(|item| string_field(item, "type") == Ok("Hash") and string_field(item, "algorithm") == Ok("sha256")).map(|item| string_field(item, "hashValue") ?? "")
			cdx_hashes = optional_array_field(component, "hashes")?.keep_if(|item| string_field(item, "alg") == Ok("SHA-256")).map(|item| string_field(item, "content") ?? "")
			if spdx_hashes.len() > 1 or cdx_hashes.len() > 1 or spdx_hashes != cdx_hashes {
				return Err("SBOM_FORMATS: component content identities differ between sidecars")
			}
		}
		Ok({})
	}

	inventory : Subject, Str, Str, Str, Str -> Try({}, Str)
	inventory = |subject, manifest_text, spdx_text, cdx_text, inventory_text| {
		raw = decode(inventory_text)?
		LifecycleJson.object(raw, ["target", "components", "relationships", "coverage", "inputs", "inspected"])?
		for item in array_field(raw, "components")? {
			LifecycleJson.object(item, ["id", "name", "producer", "version", "identity", "download", "sha256", "license", "license_url", "modified", "target", "origin"])?
		}
		for item in array_field(raw, "relationships")? {
			LifecycleJson.object(item, ["from", "to", "relation", "scope", "basis"])?
		}
		for item in array_field(raw, "coverage")? {
			LifecycleJson.object(item, ["scope", "status", "reason", "contact"])?
		}
		for item in array_field(raw, "inputs")? {
			LifecycleJson.object(item, ["input", "component", "reason"])?
		}
		parsed : Try(Inventory, _)
		parsed = Json.parse(inventory_text)
		resolved = parsed.map_err(|_| "SBOM_INVENTORY: invalid resolved inventory types")?
		ids = ["Subject"].concat(resolved.components.map(|component| component.id))
		if resolved.target != subject.target or !unique(ids) or !unique(resolved.inspected) or !unique(resolved.inputs.map(|item| item.input)) or resolved.inputs.len() != resolved.inspected.len() or !resolved.inputs.all(|item| resolved.inspected.contains(item.input) and item.reason != "" and (item.component == "" or ids.contains(item.component))) {
			return Err("SBOM_INVENTORY: target, identities or input accounting differs")
		}
		for relationship in resolved.relationships {
			if !ids.contains(relationship.from) or !ids.contains(relationship.to) or relationship.basis == "" or !["contains", "dependsOn", "generates", "patchedBy", "hasVariant", "copiedTo", "other"].contains(relationship.relation) or (relationship.scope != "" and !["build", "test", "development", "runtime"].contains(relationship.scope)) {
				return Err("SBOM_INVENTORY: invalid relationship or scope")
			}
		}
		manifest = decode(manifest_text)?
		manifest_context = LifecycleJson.field(manifest, "context")?
		namespace = string_field(manifest_context, "namespace")?
		cdx_document = decode(cdx_text)?
		metadata = LifecycleJson.field(cdx_document, "metadata")?
		components_ = components(array_field(cdx_document, "components")?, 0)?
		if components_.len() != resolved.components.len() {
			return Err("SBOM_INVENTORY: sidecar component set differs from resolved inventory")
		}
		for declared in resolved.components {
			emitted = components_.find_first(|item| string_field(item, "bom-ref") == Ok(declared.id)).map_err(|_| "SBOM_INVENTORY: resolved component omitted")?
			if string_field(emitted, "name") != Ok(declared.name) or (string_field(emitted, "version") ?? "") != declared.version {
				return Err("SBOM_INVENTORY: resolved component identity differs")
			}
			hashes = optional_array_field(emitted, "hashes")?.keep_if(|item| string_field(item, "alg") == Ok("SHA-256")).map(|item| string_field(item, "content") ?? "")
			if (declared.sha256 != "" and (!LifecyclePlan.digest(declared.sha256) or hashes != [declared.sha256])) or (declared.sha256 == "" and !hashes.is_empty()) {
				return Err("SBOM_INVENTORY: resolved component content identity differs")
			}
			props = array_field(emitted, "properties")?
			if property(props, "blueprint:origin")? != declared.origin or property(props, "blueprint:target")? != declared.target or property(props, "blueprint:modifications")? != declared.modified {
				return Err("SBOM_INVENTORY: resolved origin, target or modifications differ")
			}
		}
		encoded_relationships = optional_array_field(cdx_document, "properties")?.keep_if(|item| string_field(item, "name") == Ok("blueprint:relationship"))
		if encoded_relationships.len() != resolved.relationships.len() {
			return Err("SBOM_INVENTORY: relationship evidence set differs")
		}
		var $relationships = []
		for item in encoded_relationships {
			text = string_field(item, "value")?
			LifecycleJson.object(decode(text)?, ["from", "to", "relation", "scope", "basis"])?
			parsed_edge : Try(Edge, _)
			parsed_edge = Json.parse(text)
			edge = parsed_edge.map_err(|_| "SBOM_INVENTORY: invalid encoded relationship")?
			if !resolved.relationships.contains(edge) or $relationships.contains(edge) {
				return Err("SBOM_INVENTORY: substituted or duplicate relationship")
			}
			$relationships = $relationships.append(edge)
		}
		semantic_text = property(array_field(metadata, "properties")?, "blueprint:evidence")?
		semantic_raw = decode(semantic_text)?
		LifecycleJson.object(semantic_raw, ["profile", "revision", "target", "coverage", "dispositions", "inspected"])?
		semantic_parsed : Try(SemanticEvidence, _)
		semantic_parsed = Json.parse(semantic_text)
		semantic = semantic_parsed.map_err(|_| "SBOM_INVENTORY: invalid embedded accounting evidence")?
		if semantic.profile != "blueprint-spdx-3.0-v1" or semantic.target != resolved.target or semantic.revision != string_field(manifest_context, "revision")? or semantic.coverage != resolved.coverage or semantic.dispositions != resolved.inputs or semantic.inspected != resolved.inspected {
			return Err("SBOM_INVENTORY: coverage or input accounting differs from resolved inventory")
		}
		graph = array_field(decode(spdx_text)?, "@graph")?
		annotation = match graph.keep_if(|item| string_field(item, "type") == Ok("Annotation") and string_field(item, "contentType") == Ok("application/json") and string_field(item, "subject") == Ok("${namespace}#spdx/document")) {
			[only] => only
			_ => return Err("SBOM_INVENTORY: SPDX accounting annotation missing or ambiguous")
		}
		spdx_semantic : Try(SemanticEvidence, _)
		spdx_semantic = Json.parse(string_field(annotation, "statement")?)
		if spdx_semantic != Ok(semantic) {
			return Err("SBOM_INVENTORY: sidecars disagree on resolved coverage/input accounting")
		}
		created = string_field(manifest_context, "created")?
		creation = graph.find_first(|item| string_field(item, "type") == Ok("CreationInfo")).map_err(|_| "SBOM_INVENTORY: missing creation information")?
		if string_field(creation, "created") != Ok(created) or string_field(metadata, "timestamp") != Ok(created) {
			return Err("SBOM_INVENTORY: document creation context differs")
		}
		Ok({})
	}

	producer : Str, Str, Str, Str, Str, Str, Subject, List(Sidecar) -> Try({}, Str)
	producer = |text, repository, source_sha, run_id, run_attempt, version, subject, files| {
		raw = decode(text)?
		LifecycleJson.object(raw, ["format", "source", "producer", "generator", "subjects", "dependencies", "coverage"])?
		if string_field(raw, "format")? != "roc-automation-sbom-evidence-v1" {
			return Err("SBOM_PRODUCER: unsupported transfer evidence format")
		}
		source = LifecycleJson.field(raw, "source")?
		LifecycleJson.object(source, ["server", "repository", "commit"])?
		if string_field(source, "server")? != "https://github.com" or string_field(source, "repository")? != repository or string_field(source, "commit")? != source_sha {
			return Err("SBOM_PRODUCER: source repository or commit differs")
		}
		producing = LifecycleJson.field(raw, "producer")?
		LifecycleJson.object(producing, ["workflow", "run_id", "run_attempt"])?
		if string_field(producing, "run_id")? != run_id or string_field(producing, "run_attempt")? != run_attempt or !string_field(producing, "workflow")?.starts_with("${repository}/.github/workflows/") {
			return Err("SBOM_PRODUCER: producing source workflow, run or attempt differs")
		}
		generator = LifecycleJson.field(raw, "generator")?
		LifecycleJson.object(generator, ["tool", "tool_sha256", "compiler", "generator", "blueprint", "created"])?
		blueprint = string_field(generator, "blueprint")?
		registry = LifecycleJson.field(decode(tool_registry)?, blueprint)?
		tool = string_field(generator, "tool")?
		tool_sha256 = string_field(generator, "tool_sha256")?
		bundle = tool == string_field(registry, "url")? and tool_sha256 == ""
		native = tool == "https://github.com/lukewilliamboswell/roc-blueprint/releases/download/${blueprint}/blueprint-sbom-x86_64-linux" and string_field(LifecycleJson.field(registry, "binaries") ?? Object([]), "x86_64-linux") == Ok(tool_sha256) and LifecyclePlan.digest(tool_sha256)
		if (!bundle and !native) or string_field(generator, "compiler") != string_field(registry, "compiler") or string_field(generator, "generator") != string_field(registry, "generator") or string_field(generator, "created")? == "" {
			return Err("SBOM_PRODUCER: released tool identity differs from embedded reviewed registry")
		}
		subjects = array_field(raw, "subjects")?
		declared = match subjects {
			[only] => only
			_ => return Err("SBOM_PRODUCER: exactly one mapped subject per SBOM job required")
		}
		LifecycleJson.object(declared, ["name", "path", "size", "sha256", "target", "version", "url", "inventory_sha256", "sidecars"])?
		if string_field(declared, "name")? != basename(subject.path) or !LifecyclePlan.safe_path(string_field(declared, "path")?) or basename(string_field(declared, "path")?) != basename(subject.path) or string_field(declared, "sha256")? != subject.sha256 or number_field(declared, "size")? != subject.size or string_field(declared, "target")? != subject.target or string_field(declared, "version")? != version or string_field(declared, "url")? != "https://github.com/${repository}/releases/download/${version}/${basename(subject.path)}" or !LifecyclePlan.digest(string_field(declared, "inventory_sha256")?) {
			return Err("SBOM_PRODUCER: mapped subject identity, target, version or bytes differ")
		}
		sidecars = array_field(declared, "sidecars")?
		if sidecars.len() != 3 or !unique(sidecars.map(|item| string_field(item, "name") ?? "")) {
			return Err("SBOM_PRODUCER: complete supported sidecar set required")
		}
		for sidecar in sidecars {
			LifecycleJson.object(sidecar, ["name", "path", "size", "sha256", "format"])?
			name = string_field(sidecar, "name")?
			file = files.find_first(|item| basename(item.path) == name).map_err(|_| "SBOM_PRODUCER: declared sidecar missing")?
			suffix = name.drop_prefix(basename(subject.path))
			format = match suffix {
				".spdx.json" => "SPDX-3.0.1"
				".cdx.json" => "CycloneDX-1.7"
				".sbom-manifest.json" => "manifest"
				_ => return Err("SBOM_PRODUCER: unsupported extra sidecar")
			}
			if number_field(sidecar, "size")? != file.size or string_field(sidecar, "sha256")? != file.sha256 or string_field(sidecar, "format")? != format or !LifecyclePlan.safe_path(string_field(sidecar, "path")?) or basename(string_field(sidecar, "path")?) != name {
				return Err("SBOM_PRODUCER: sidecar format, size, path or hash differs")
			}
		}
		_ = array_field(raw, "dependencies")?
		_ = array_field(raw, "coverage")?
		Ok({})
	}
	number_field : LifecycleJson, Str -> Try(U64, Str)
	number_field = |value, key| match LifecycleJson.field(value, key)? {
		Number(text) => U64.from_str(text).map_err(|_| "SBOM_SHAPE: expected unsigned integer")
		_ => Err("SBOM_SHAPE: expected unsigned integer")
	}

	merged_composition = |composition_text, imports_text| {
		composition = decode(composition_text)?
		own = optional_array_field(composition, "imported_documents")?
		added = if imports_text == "" [] else array_field(decode(imports_text)?, "imported_documents")?
		combined = own.concat(added)
		if !unique(combined.map(|record| string_field(record, "id") ?? "")) {
			return Err("SBOM_IMPORT: duplicate supplier document identities")
		}
		match composition {
			Object(fields) => Ok(Object(fields.keep_if(|field| field.name != "imported_documents").append({ name: "imported_documents", value: Array(combined) })))
			_ => Err("SBOM_COMPOSITION: reviewed composition must be an object")
		}
	}

	components = |items, depth| {
		if depth > 16 or items.len() > 4096 {
			return Err("SBOM_CDX: nested component limit exceeded")
		}
		var $all = []
		for item in items {
			$all = $all.append(item)
			match LifecycleJson.field(item, "components") {
				Ok(value) => {
					$all = $all.concat(components(LifecycleJson.array(value)?, depth + 1)?)
				}
				Err(_) => {}
			}
			if $all.len() > 4096 {
				return Err("SBOM_CDX: component limit exceeded")
			}
		}
		Ok($all)
	}
	property = |properties, name| match properties.keep_if(|item| string_field(item, "name") == Ok(name)) {
		[only] => string_field(only, "value")
		_ => Err("SBOM_PROPERTY: missing or duplicate required property")
	}
	decode = |text| LifecycleJson.decode_limit(text, 1048576).map_err(LifecycleJson.message)
	string = |value| match value {
		String(text) => Ok(text)
		_ => Err("SBOM_SHAPE: expected string")
	}
	string_field = |value, key| string(LifecycleJson.field(value, key)?)
	optional_array_field = |value, key| match LifecycleJson.field(value, key) {
		Ok(items) => LifecycleJson.array(items)
		Err(_) => Ok([])
	}
	array_field = |value, key| LifecycleJson.array(LifecycleJson.field(value, key)?)
	unique = |items| LifecyclePlan.unique(items).len() == items.len()
	basename = |path| path.split_on("/").last() ?? ""
}
