lint = {
	schema_version: 1,
	roots: ["examples", "scripts"],
	rules: [
		{ kind: Dependency, scope: "examples", name: "ansi", value: "../package/main.roc" },
		{ kind: NoCompilerPin, scope: "examples" },
		{ kind: CompilerPin, scope: "scripts", value: "nightly-2026-10-09-258ab27" },
		{ kind: ForbiddenImport, scope: "scripts", name: "Internal" },
	],
}
