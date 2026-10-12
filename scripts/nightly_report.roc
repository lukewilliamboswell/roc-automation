#!/usr/bin/env roc-stable
app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
	roc: "nightly-2026-10-09-258ab27",
}

import cli.Cmd
import cli.Env
import cli.OsStr
import cli.Path
import cli.Stderr
import cli.Stdout
import src/GitHub
import src/NightlyReport

## Keep the one tracking issue of a nightly compatibility workflow. This is
## what `actions/nightly-report` runs.
##
##     blueprint run nightly-report
##
## Everything it is told arrives in the environment: the action's inputs as
## `INPUT_*`, the token as `GH_TOKEN`, and the runner's `GITHUB_*` variables.
## What it decides is in `src/NightlyReport.roc`. This file only reads the
## environment, carries each request that module asks for to `gh`, and writes
## the outputs.
main! = |_args| {
	inputs = {
		outcome: env!("INPUT_OUTCOME")?,
		nightly_tag: env!("INPUT_NIGHTLY_TAG")?,
		release_tag: env!("INPUT_RELEASE_TAG")?,
		title: env!("INPUT_TITLE")?,
		server: env!("GITHUB_SERVER_URL")?,
		repository: env!("GITHUB_REPOSITORY")?,
		run_id: env!("GITHUB_RUN_ID")?,
		token: env!("GH_TOKEN")?,
	}
	# No request can be made before this: `ask!` needs a plan.
	made = match NightlyReport.plan(inputs) {
		Ok(checked) => checked
		Err(reason) => return fail!(reason)
	}
	output_file = match env!("GITHUB_OUTPUT")? {
		"" => return fail!("GITHUB_OUTPUT is not set, so the outputs cannot be written")
		path => Path.utf8(path)
	}
	report = match drive!(made, NightlyReport.start(made)) {
		Ok(done) => done
		Err(reason) => return fail!(reason)
	}
	append!(output_file, NightlyReport.outputs(report))?
	Stdout.line!(NightlyReport.summary(made, report))
}

# Make each request the plan asks for, in the order it asks, until it ends.
drive! : NightlyReport.Plan, NightlyReport.Next => Try(NightlyReport.Report, Str)
drive! = |made, next|
	match next {
		Finished(result) => result
		Do(request, stage) => drive!(made, NightlyReport.advance(made, stage, ask!(made, request)))
	}

# The only place a request leaves this script. `gh` takes the token from
# `GH_TOKEN`, which it inherits; no argument or input carries it.
ask! : NightlyReport.Plan, NightlyReport.Request => NightlyReport.Reply
ask! = |made, request| {
	call = NightlyReport.call(made, request)
	ran = Cmd.new("gh")
		.args(GitHub.args(call).map(OsStr.from_str))
		.stdin(Bytes(GitHub.stdin(call)))
		.stdout(Capture)
		.stderr(Capture)
		.timeout_ms(60_000)
		.run!()
	match ran {
		Ok(output) =>
			NightlyReport.reply(
				made,
				request,
				{ succeeded: output.status == Exited(0), stdout: Str.from_utf8_lossy(output.stdout_bytes), stderr: Str.from_utf8_lossy(output.stderr_bytes) },
			)
		Err(Timeout(_)) => Failed(GitHub.not_run(call, "no answer within 60 seconds"))
		Err(_) => Failed(GitHub.not_run(call, "gh could not be run"))
	}
}

# A variable that is not set reads as empty, which every check then rejects.
env! : Str => Try(Str, _)
env! = |name|
	match Env.var_str!(OsStr.from_str(name)) {
		Ok(value) => Ok(value)
		Err(VarNotFound(_)) => Ok("")
		Err(_) => {
			Stderr.line!("::error::${name} is not UTF-8 text")?
			Err(Exit(1))
		}
	}

# Add `text` to a file the runner reads after the step.
append! : Path, Str => Try({}, _)
append! = |path, text| {
	before = match Path.read_bytes!(path) {
		Ok(bytes) => bytes
		Err(PathErr(NotFound, _)) => []
		Err(other) => return Err(other)
	}
	Path.write_bytes!(path, before.concat(Str.to_utf8(text)))
}

# Report an error as a workflow command and fail the step.
fail! = |reason| {
	Stderr.line!("::error::${GitHub.workflow_command_text(reason)}")?
	Err(Exit(1))
}
