app [config] { pf: platform "https://github.com/lukewilliamboswell/roc-blueprint/releases/download/0.8.0/FbJymjYdCxx9vqMcau7r4tQfeAbnVGqqJoYrLGqPCqKu.tar.zst" }

import scripts/Toolchain

config = [
	Name("lifecycle-platform"),
	Systems(["x86_64-linux", "aarch64-darwin", "x86_64-windows"]),
	Environment("native", [Programs(Toolchain.programs)]),
	Environment("package", [Expect(["roc"])]),
	Environment("runtime", [Expect(["bash", "clang"])]),
	Task("check-runtime", [Use("runtime"), Run(["clang", "--version"])]),
	Task("check-unit", [Use("package"), Run(["roc", "test", "platform/Contract.roc"])]),
	Task("build-linux", [Use("native"), Run(["roc-stable", "scripts/native.roc", "--", "build", "linux"])]),
	Task("build-macos", [Use("native"), Run(["roc-stable", "scripts/native.roc", "--", "build", "macos"])]),
	Task("build-windows", [Use("native"), Run(["roc-stable", "scripts/native.roc", "--", "build", "windows"])]),
]

# Shared source policy reads this data without evaluating product code.
repository_policy = {
	schema_version: 2,
	roots: ["platform", "scripts", "examples", "docs"],
	exclude: ["platform/targets/**"],
	test_roots: ["platform"],
	profiles: [{ name: "base", builtin: "roc-package", version: 1 }],
	# The exposed imported module is checked at its declaration; the compiler
	# and native consumer verify the platform header linkage.
	rules: [{ id: "public-api", kind: PublicDocs, include: ["platform/Contract.roc"], level: Error }],
	overrides: [],
}
