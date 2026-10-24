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
