app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
	roc: "nightly-2026-10-15-38e6c42",
}

import cli.Cmd
import cli.Env
import cli.OsStr
import cli.Path
import cli.Stdout

# Run from the repository root with ROC naming the pinned compiler. This
# separate build keeps the experiment out of the controller tools' toolchain.
main! = |_args| {
	compiler = Env.var_str!("ROC")?
	Path.create_all!("dist")?
	for target in [
		{ roc: "x64musl", system: "x86_64-linux" },
		{ roc: "arm64musl", system: "aarch64-linux" },
		{ roc: "arm64mac", system: "aarch64-darwin" },
	] {
		output = "dist/roc-policy-${target.system}"
		Stdout.line!("Building ${output}")?
		Cmd.new(OsStr.from_str(compiler)).args(
			[
				"build",
				"tools/roc-policy/main.roc",
				"--opt=size",
				"--target=${target.roc}",
				"--output=${output}",
			].map(OsStr.from_str),
		).exec_cmd!()?
	}
	Ok({})
}
