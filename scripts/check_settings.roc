#!/usr/bin/env roc-stable
app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
	roc: "nightly-2026-10-09-258ab27",
}

import cli.Cmd
import cli.Env
import cli.OsStr
import cli.Stderr
import cli.Stdout
import src/Settings

## Compare a repository's GitHub settings with the "Repository settings"
## chapter of the manual, and report each line as passing or failing.
##
##     blueprint run check-settings -- lukewilliamboswell/roc-ansi
##     scripts/check_settings.roc -- --help
##
## It reads and never writes: `get!` is the only way it reaches GitHub. It
## needs `gh` to be logged in as someone who can administer the repository.
## What it decides from the answers is in `src/Settings.roc`.
main! = |args| {
	asked = match Settings.parse_args(args.map(OsStr.display)) {
		Ok(found) => found
		Err(Help) => return Stdout.line!(Settings.usage)
		Err(Invalid(reason)) => {
			Stderr.line!("error: ${reason}\n\n${Settings.usage}")?
			return Err(Exit(2))
		}
	}
	colour = colour!()?
	var $passed = Bool.True
	for name in asked.repositories {
		results = Settings.checks(name, asked.options, answers!(name)?)
		Stdout.line!("${Settings.report(name, results, colour)}\n")?
		$passed = $passed and Settings.passed(results)
	}
	if $passed Ok({}) else Err(Exit(1))
}

# Ask GitHub everything the checks read about one repository. A request that
# depends on an earlier answer is made only when that answer allows it.
answers! : Str => Try(Settings.Answers, _)
answers! = |name| {
	skipped = Unreadable("it was not requested")
	repository = get!(Bool.False, "repos/${name}", Json)?
	(admin, branch) = match Settings.repository(repository) {
		Ok(repo) => (repo.admin, repo.default_branch)
		Err(_) => (Bool.False, "")
	}
	ask! = |path| get!(admin, "repos/${name}/${path}", Json)
	on_branch! = |path| if branch == "" Ok(skipped) else ask!(path)

	actions = ask!("actions/permissions")?
	listed = ask!("rulesets")?
	# A list that could not be read stands in for the rulesets it would have named.
	var $rulesets = match listed {
		Body(_) => []
		_ => [listed]
	}
	for id in Settings.ruleset_ids(listed) {
		$rulesets = $rulesets.append(ask!("rulesets/${id.to_str()}")?)
	}
	head_pulls = on_branch!("commits/${branch}/pulls")?
	release_environment = ask!("environments/release")?
	pages_environment = ask!("environments/github-pages")?
	policies! = |environment, answer|
		match answer {
			Body(_) => ask!("environments/${environment}/deployment-branch-policies")
			_ => Ok(skipped)
		}

	Ok({
		repository,
		workflow: ask!("actions/permissions/workflow")?,
		actions,
		selected_actions: if Settings.restricts_actions(actions) ask!("actions/permissions/selected-actions")? else skipped,
		rulesets: $rulesets,
		branch_rules: on_branch!("rules/branches/${branch}")?,
		head_pulls,
		check_runs: match Settings.head_pull(head_pulls) {
			Ok(pull) => ask!("commits/${pull.sha}/check-runs?per_page=100")?
			Err(_) => skipped
		},
		immutable: ask!("immutable-releases")?,
		latest_release: ask!("releases/latest")?,
		release_environment,
		release_policies: policies!("release", release_environment)?,
		pages: ask!("pages")?,
		pages_environment,
		pages_policies: policies!("github-pages", pages_environment)?,
		dependabot: get!(admin, "repos/${name}/contents/.github/dependabot.yml", Raw)?,
		security: get!(admin, "repos/${name}/contents/SECURITY.md", Raw)?,
		security_in_github: get!(admin, "repos/${name}/contents/.github/SECURITY.md", Raw)?,
		private_reporting: ask!("private-vulnerability-reporting")?,
	})
}

# The only request this script can make: `Settings.get_args` fixes the method
# to GET, and nothing here adds to its arguments.
get! : Bool, Str, [Json, Raw] => Try(Settings.Reply, _)
get! = |admin, path, format| {
	output = Cmd.new("gh")
		.args(Settings.get_args(path, format).map(OsStr.from_str))
		.stdout(Capture)
		.stderr(Capture)
		.run!()
		.map_err(|err| CommandRunFailed("gh", err))?
	Ok(
		Settings.reply({
			path,
			admin,
			succeeded: output.status == Exited(0),
			stdout: Str.from_utf8_lossy(output.stdout_bytes),
			stderr: Str.from_utf8_lossy(output.stderr_bytes),
		}),
	)
}

# Colour is an aid, never the only signal, and is left out when nobody is
# looking at a terminal or `NO_COLOR` asks for that.
colour! = || {
	match Env.var_str!("NO_COLOR") {
		Ok("") => {}
		Err(VarNotFound(_)) => {}
		_ => return Ok(Bool.False)
	}
	# `test` inherits this script's standard output, and -t asks whether it is a terminal.
	code = Cmd.new("test").args(["-t", "1"]).exec_exit_code!().map_err(|_| CommandRunFailed("test -t 1", Unavailable))?
	Ok(code == 0)
}
