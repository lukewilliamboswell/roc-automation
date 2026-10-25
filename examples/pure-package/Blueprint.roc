# Package testing uses the latest nightly supplied by CI as bare `roc`.
# Fixed tooling and released utilities have separate names and locked bytes.
app [config] { pf: platform "https://github.com/lukewilliamboswell/roc-blueprint/releases/download/0.8.0/FbJymjYdCxx9vqMcau7r4tQfeAbnVGqqJoYrLGqPCqKu.tar.zst" }

import pf.Recipes

config = [
	Name("pure-package-example"),
	Systems(["x86_64-linux", "aarch64-linux", "aarch64-darwin"]),
	Environment("docs", [Programs([Fetch("roc-docs", Recipes.file("https://github.com/lukewilliamboswell/roc-automation/releases/download/tools-0.2.0/roc-docs-{system}"))]), Expect(["realpath", "sh"])]),
	Environment("fixed-tools", [Programs([Fetch("roc-stable", Recipes.roc_nightly("nightly-2026-10-18-1df370f"))]), Expect(["realpath", "sh"])]),
	Environment("package", [Expect(["roc"])]),
	Task("docs", [Use("docs"), Run(["roc-docs", "build", "--workspace", ".", "--pdf-filename", "example.pdf"])]),
	Task("docs-path", [Use("docs"), Run(["sh", "-c", "realpath \"$(command -v roc-docs)\""])]),
	Task("tooling-version", [Use("fixed-tools"), Run(["roc-stable", "version"])]),
	Task("tooling-path", [Use("fixed-tools"), Run(["sh", "-c", "realpath \"$(command -v roc-stable)\""])]),
	Task("check-package", [Use("package"), Run(["roc", "test", "package/main.roc"])]),
]
