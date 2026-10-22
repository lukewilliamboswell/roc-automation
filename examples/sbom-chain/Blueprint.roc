# A consumer that pins a supplier's release file by its published SHA-256, so
# the supplier's SBOM beside that file can be imported bound to it, and that
# fetches a fixed compiler whose publisher ships no SBOM.
app [config] { pf: platform "https://github.com/lukewilliamboswell/roc-blueprint/releases/download/0.8.0/FbJymjYdCxx9vqMcau7r4tQfeAbnVGqqJoYrLGqPCqKu.tar.zst" }

import pf.Recipes

config = [
	Name("sbom-chain-example"),
	Systems(["x86_64-linux", "aarch64-linux", "aarch64-darwin"]),
	SourceFile("blueprint-core", "https://github.com/lukewilliamboswell/roc-blueprint/releases/download/0.7.0/roc-blueprint-core-9Z7WuswxTGgKd4i3cM5JDf5Kq9JqBpWoMxtWHALnjMcC.tar.zst", "1e1190e25a117ab5996e3f6e780e43dcdaf9f7da88aaaf9536f2ba0571c78265"),
	Environment("fixed-tools", [Programs([Fetch("roc-stable", Recipes.roc_nightly("nightly-2026-10-21-5349569"))])]),
	Environment("package", [Expect(["roc"])]),
	Task("check-package", [Use("package"), Run(["roc", "check", "package/main.roc"])]),
	Task("build-app", [Use("fixed-tools"), Run(["roc-stable", "build", "app/main.roc", "--output=dist/hello"])]),
]
