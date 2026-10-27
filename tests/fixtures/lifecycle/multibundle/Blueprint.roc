# The integration harness supplies the selected package compiler as roc.
app [config] { pf: platform "https://github.com/lukewilliamboswell/roc-blueprint/releases/download/0.8.0/FbJymjYdCxx9vqMcau7r4tQfeAbnVGqqJoYrLGqPCqKu.tar.zst" }

config = [
	Name("lifecycle-multibundle"),
	Systems(["x86_64-linux", "aarch64-darwin", "x86_64-windows"]),
	Environment("package", [Expect(["roc"])]),
	Task("check-unit", [Use("package"), Run(["roc", "test", "greeting/main.roc"])]),
]

# Shared source policy reads this data without evaluating product code.
repository_policy = {
	schema_version: 2,
	roots: ["core", "greeting", "examples", "docs"],
	exclude: ["platform/targets/**"],
	test_roots: ["core", "greeting"],
	profiles: [{ name: "base", builtin: "roc-package", version: 1 }],
	rules: [{ id: "public-api", kind: PublicDocs, include: ["core/**/*.roc", "greeting/**/*.roc"], level: Error }],
	overrides: [],
}
