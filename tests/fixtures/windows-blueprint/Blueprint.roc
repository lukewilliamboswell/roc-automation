app [config] { pf: platform "https://github.com/lukewilliamboswell/roc-blueprint/releases/download/0.8.0/FbJymjYdCxx9vqMcau7r4tQfeAbnVGqqJoYrLGqPCqKu.tar.zst" }

config = [
	Name("native-windows-setup-smoke"),
	Systems(["x86_64-windows"]),
	Environment("host", [Expect(["python"])]),
	Task("smoke", [Use("host"), Run(["python", "smoke.py"])]),
]
