#!/usr/bin/env roc-stable
app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
	roc: "nightly-2026-10-09-258ab27",
}

import cli.Cmd
import cli.OsStr
import cli.Path
import cli.Stdout

## Build the scripts that shared actions run as static Linux executables.
##
##     blueprint run build-tools
##
## Each tool is written to dist/<tool>-<system>, where <system> is the name
## `actions/setup-blueprint` gives a runner. A static executable needs nothing
## from the runner but the programs the script starts, so a calling job runs it
## without Nix, Blueprint or a compiler.
main! = |_args| {
	Path.create_all!(Path.utf8("dist"))?
	for tool in tools {
		for system in systems {
			output = "dist/${tool.name}-${system.name}"
			run!(["build", "scripts/${tool.script}.roc", "--target=${system.roc}", "--opt=size", "--output=${output}"])?
		}
	}
	Stdout.line!("PASS built every tool for every system in dist/")
}

tools = [
	{ name: "nightly-report", script: "nightly_report" },
	{ name: "publish-release", script: "publish_release" },
	{ name: "release-compatibility", script: "release_compatibility" },
]

# Both are built on one machine: the musl systems link statically and need no
# toolchain for the other architecture.
systems = [
	{ roc: "x64musl", name: "x86_64-linux" },
	{ roc: "arm64musl", name: "aarch64-linux" },
]

# Print a `roc-stable` command, then run it with inherited output.
run! = |args| {
	Stdout.line!("+ roc-stable ${Str.join_with(args, " ")}")?
	Cmd.new("roc-stable").args(args.map(OsStr.from_str)).exec_cmd!()
}
