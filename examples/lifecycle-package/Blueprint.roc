# Shared utilities are supplied as exact built/released binaries on PATH.
# The package compiler is the one selected once by the caller, never a second pin.
app [config] { pf: platform "https://github.com/lukewilliamboswell/roc-blueprint/releases/download/0.8.0/FbJymjYdCxx9vqMcau7r4tQfeAbnVGqqJoYrLGqPCqKu.tar.zst" }

config = [
	Name("lifecycle-package"),
	Systems(["x86_64-linux", "aarch64-darwin", "x86_64-windows"]),
	Environment("package", [Expect(["roc"])]),
	Environment("shared", [Expect(["release-plan", "release-operations"])]),
	Task("check-unit", [Use("package"), Run(["roc", "test", "package/main.roc"])]),
	Task("plan", [Use("shared"), Run(["release-plan", "plan"])]),
	Task("source", [Use("shared"), Run(["release-operations", "source"])]),
	Task("prepare", [Use("shared"), Run(["release-operations", "prepare"])]),
	Task("candidate", [Use("shared"), Run(["release-operations", "candidate"])]),
]

# Shared source policy reads this data without evaluating product code.
repository_policy = {
	schema_version: 2,
	roots: ["package", "examples", "docs"],
	exclude: ["platform/targets/**"],
	test_roots: ["package"],
	profiles: [{ name: "base", builtin: "roc-package", version: 1 }],
	rules: [],
	overrides: [],
}
