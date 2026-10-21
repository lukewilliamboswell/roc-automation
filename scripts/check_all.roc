#!/usr/bin/env roc-stable
app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
	roc: "nightly-2026-10-09-258ab27",
}

import cli.Cmd
import cli.OsStr
import cli.Path
import cli.Stderr
import cli.Stdout
import src/Pins

## Everything CI checks of the Roc scripts in this repository.
##
##     blueprint run check
##
## `roc-stable`, pinned by Blueprint.roc, runs this script and checks the
## others. Nothing here asks GitHub anything: comparing a repository's settings
## with the manual is `blueprint run check-settings`, which a maintainer runs.
main! = |_args| {
	Stdout.line!("\n== Compiler")?
	run!(["version"])?

	scripts = Path.list!("scripts")?.keep_if(|path| Path.ext(path).map_ok(Path.display) == Ok("roc")).map(Path.display)

	# Each script names the tooling compiler, and Blueprint.roc is the authority:
	# someone who runs a script with another `roc` is then told why it misbehaves.
	Stdout.line!("\n== Compiler pins")?
	tag = match Pins.stable_tag(Path.read_utf8!("Blueprint.roc")?) {
		Ok(found) => found
		Err(NoStableCompiler) => return fail!("Blueprint.roc does not name roc-stable with Command(\"roc-stable\", \"rocpkgs.<tag>\")")
	}
	for script in scripts {
		source = Path.read_utf8!(Path.utf8(script))?
		if Pins.is_app(source) and Pins.roc_pin(source) != Ok(tag) {
			return fail!("${script}: the app header must declare roc: \"${tag}\", the roc-stable release in Blueprint.roc")
		}
	}
	Stdout.line!("PASS every script names ${tag}")?

	Stdout.line!("\n== Scripts")?
	# The modules without effects carry the tests of the scripts' logic.
	for module_name in ["AppHeader", "Compatibility", "DownstreamStatus", "GitHub", "NightlyReport", "Pins", "PublishRelease", "Settings"] {
		run!(["test", "scripts/src/${module_name}.roc"])?
	}
	for script in scripts {
		run!(["check", script])?
	}
	run!(["fmt", "--check", "scripts"])
}

# Print a `roc-stable` command, then run it with inherited output.
run! = |args| {
	Stdout.line!("+ roc-stable ${Str.join_with(args, " ")}")?
	Cmd.new("roc-stable").args(args.map(OsStr.from_str)).exec_cmd!()
}

fail! = |message| {
	Stderr.line!("error: ${message}")?
	Err(Exit(1))
}
