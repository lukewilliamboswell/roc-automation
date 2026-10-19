app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
	roc: "nightly-2026-10-18-1df370f",
}

import cli.Cmd
import cli.Env
import cli.OsStr
import cli.Path
import cli.Stdout
import cli.Stderr

# Run from the repository root with ROC naming the pinned compiler. This
# separate build keeps documentation renderer independent of the controller toolchain.
# Optional argument selects one release system; no arguments build all systems.
main! = |args| {
	requested = args.map(OsStr.display)
	compiler = Env.var_str!("ROC")?
	Path.create_all!("dist")?
	release_targets = [
		{ roc: "x64musl", system: "x86_64-linux" },
		{ roc: "arm64musl", system: "aarch64-linux" },
		{ roc: "arm64mac", system: "aarch64-darwin" },
	]
	selected = match requested {
		[] => release_targets
		[system] => release_targets.keep_if(|target| target.system == system)
		_ => []
	}
	if selected.is_empty() {
		Stderr.line!("usage: roc tools/roc-docs/build.roc [x86_64-linux|aarch64-linux|aarch64-darwin]")?
		return Err(Exit(2))
	}
	for target in selected {
		output = "dist/roc-docs-${target.system}"
		Stdout.line!("Building ${output}")?
		Cmd.new(OsStr.from_str(compiler)).args(
			[
				"build",
				"tools/roc-docs/main.roc",
				"--opt=speed",
				"--target=${target.roc}",
				"--output=${output}",
			].map(OsStr.from_str),
		).exec_cmd!()?
	}
	Ok({})
}
