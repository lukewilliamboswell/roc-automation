# A consumer that pins a supplier's release file by its published SHA-256, so
# the supplier's SBOM beside that file can be imported bound to it.
app [config] { pf: platform "https://github.com/lukewilliamboswell/roc-blueprint/releases/download/0.7.0/2zwBq8cAkEPfRNMaWgtwCsVwxSJ3z9FncazjNofBXspG.tar.zst" }

config = [
	Name("sbom-chain-example"),
	Systems(["x86_64-linux", "aarch64-linux", "aarch64-darwin"]),
	SourceFile("blueprint-core", "https://github.com/lukewilliamboswell/roc-blueprint/releases/download/0.7.0/roc-blueprint-core-9Z7WuswxTGgKd4i3cM5JDf5Kq9JqBpWoMxtWHALnjMcC.tar.zst", "1e1190e25a117ab5996e3f6e780e43dcdaf9f7da88aaaf9536f2ba0571c78265"),
	Environment("package", [Expect(["roc"])]),
	Task("check-package", [Use("package"), Run(["roc", "check", "package/main.roc"])]),
]
