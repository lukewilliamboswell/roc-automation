## What the "Repository settings" chapter of the manual asks of a repository,
## decided from GitHub's answers to read-only API requests.
##
## Nothing here has an effect: the script fetches the answers and this module
## turns them into checks, so every decision is covered by an `expect` below.
Settings := [].{

	## GitHub's answer to one request. `NotFound` and `Unreadable` carry the
	## words to show the maintainer. A 404 is only `NotFound` when the login
	## can administer the repository; otherwise it may be hiding the setting.
	Reply : [Body(Str), NotFound(Str), Unreadable(Str)]

	## One line of the report. `Fail` carries the fix. `Unknown` means the
	## setting could not be read, which is never reported as a pass.
	Check : { heading : Str, outcome : [Pass, Fail(Str), Note, Unknown], text : Str }

	## The answers only a maintainer can give about a repository.
	Options : { linker_inputs : Bool, reviewer_required : Bool, site : Bool }

	## Every answer the checks read. `selected_actions` is asked for only where
	## actions are restricted, and the two `*_policies` only for an existing
	## environment; the checks do not look at them otherwise.
	Answers : {
		repository : Reply,
		workflow : Reply,
		actions : Reply,
		selected_actions : Reply,
		rulesets : List(Reply),
		branch_rules : Reply,
		head_pulls : Reply,
		check_runs : Reply,
		immutable : Reply,
		latest_release : Reply,
		release_environment : Reply,
		release_policies : Reply,
		pages : Reply,
		pages_environment : Reply,
		pages_policies : Reply,
		dependabot : Reply,
		security : Reply,
		security_in_github : Reply,
		private_reporting : Reply,
	}

	usage : Str
	usage =
		\\usage: blueprint run check-settings -- [flags] <owner>/<name>...
		\\
		\\Compares each repository's GitHub settings with the "Repository settings"
		\\chapter of the manual. It only reads: every request is a GET, made with
		\\your own `gh` login, which needs administration access to the repository.
		\\
		\\  --platform           the repository publishes linker inputs: note whether
		\\                       merge commits are allowed
		\\  --reviewer-required  the project has more than one maintainer: the
		\\                       `release` environment must name a required reviewer
		\\  --no-site            the repository publishes no site: skip the Pages checks
		\\  --help               print this text
		\\
		\\Flags apply to every repository named. The exit code is 1 when any check
		\\fails or a setting cannot be read.

	## The repositories and options a command line names.
	parse_args : List(Str) -> Try({ repositories : List(Str), options : Options }, [Help, Invalid(Str)])
	parse_args = |args| {
		flags = args.keep_if(|arg| arg.starts_with("-"))
		repositories = args.keep_if(|arg| !arg.starts_with("-"))
		if flags.contains("--help") or flags.contains("-h") {
			Err(Help)
		} else {
			match (flags.keep_if(|flag| !["--platform", "--reviewer-required", "--no-site"].contains(flag)), repositories.keep_if(|name| !valid_repository(name))) {
				([flag, ..], _) => Err(Invalid("unknown flag ${flag}"))
				(_, [name, ..]) => Err(Invalid("${name} is not an <owner>/<name> repository"))
				_ if repositories.is_empty() => Err(Invalid("name at least one repository"))
				_ => Ok({ repositories, options: { linker_inputs: flags.contains("--platform"), reviewer_required: flags.contains("--reviewer-required"), site: !flags.contains("--no-site") } })
			}
		}
	}

	## The arguments of the one `gh` invocation this tool makes.
	##
	## The tool must never change a setting, so the method is not left to
	## `gh api`, which switches to POST as soon as it is given a field or a
	## body. It is stated as GET here, and no caller can add an argument.
	## `Raw` asks for a file's bytes instead of its JSON description.
	get_args : Str, [Json, Raw] -> List(Str)
	get_args = |path, format| {
		accept = match format {
			Json => "application/vnd.github+json"
			Raw => "application/vnd.github.raw"
		}
		["api", "--method", "GET", "-H", "Accept: ${accept}", path]
	}

	## Classify what `gh api` printed for `path`.
	reply : { path : Str, admin : Bool, succeeded : Bool, stdout : Str, stderr : Str } -> Reply
	reply = |got| {
		problem : Try({ status : Str }, _)
		problem = Json.parse(got.stdout)
		if got.succeeded {
			Body(got.stdout)
		} else {
			match problem {
				Ok({ status: "404" }) if got.admin => NotFound("gh api ${got.path} answered 404")
				Ok({ status }) => Unreadable("gh api ${got.path} answered ${status}")
				Err(_) => Unreadable("gh api ${got.path} failed: ${first_line(got.stderr)}")
			}
		}
	}

	## The default branch, and whether the login can administer the repository.
	repository : Reply -> Try({ default_branch : Str, admin : Bool }, Str)
	repository = |answer| {
		repo = repository_details(answer)?
		admin = match repo.permissions {
			Ok(permissions) => permissions.admin
			Err(Missing) => Bool.False
		}
		if is_plain(repo.default_branch, "-._/") {
			Ok({ default_branch: repo.default_branch, admin })
		} else {
			Err("the default branch name ${repo.default_branch} cannot be used in a request")
		}
	}

	## The identifiers in a list of rulesets.
	ruleset_ids : Reply -> List(U64)
	ruleset_ids = |answer|
		match ruleset_list(answer) {
			Ok(rulesets) => rulesets.map(|ruleset| ruleset.id)
			Err(_) => []
		}

	## Whether only selected actions may run, so the allowlist matters.
	restricts_actions : Reply -> Bool
	restricts_actions = |answer| allowed_actions(answer) == Ok({ allowed_actions: "selected" })

	## The head commit of the pull request that brought in the latest commit
	## on the default branch.
	head_pull : Reply -> Try({ number : U64, sha : Str }, Str)
	head_pull = |answer|
		match pulls(answer)? {
			[pull, ..] if is_plain(pull.head.sha, "") => Ok({ number: pull.number, sha: pull.head.sha })
			_ => Err("the latest commit on the default branch did not arrive through a pull request, so there is no run to compare with")
		}

	## Every check of the chapter for `name`, in the chapter's order.
	checks : Str, Options, Answers -> List(Check)
	checks = |name, options, answers| {
		(default_branch, admin) = match Settings.repository(answers.repository) {
			Ok(repo) => (repo.default_branch, repo.admin)
			# Nothing else can be read either, and one line says so.
			Err(reason) => return [unknown("Repository", "the repository", reason)]
		}
		access = if admin [] else [unknown("Repository", "administration access", "this gh login cannot administer ${name}, so GitHub hides some settings from it")]
		rulesets = answers.rulesets.keep_oks(ruleset)
		unread = answers.rulesets.keep_errs(ruleset)
		access
			.concat(workflow_token(name, answers.workflow))
			.concat([allowed(name, answers.actions, answers.selected_actions)])
			.concat(branch_rules(name, default_branch, answers.branch_rules, rulesets, unread))
			.concat(required_checks(name, answers.branch_rules, answers.head_pulls, answers.check_runs))
			.concat(ruleset_notes(default_branch, answers.branch_rules, rulesets))
			.concat(if options.linker_inputs [merge_commits(answers.repository, answers.branch_rules)] else [])
			.concat(immutable_releases(name, answers.immutable, answers.latest_release))
			.concat(environment("Releases", name, "release", default_branch, answers.release_environment, answers.release_policies))
			.concat(if options.reviewer_required reviewer(name, answers.release_environment) else [])
			.concat([tag_rules(name, rulesets, unread)])
			.concat(if options.site [pages_source(name, answers.pages)].concat(environment("Pages", name, "github-pages", default_branch, answers.pages_environment, answers.pages_policies)) else [])
			.concat([dependabot(name, answers.dependabot)])
			.concat([private_reporting(name, answers.private_reporting), security_policy(name, answers.security, answers.security_in_github)])
	}

	## Whether nothing failed and nothing was left unknown.
	passed : List(Check) -> Bool
	passed = |results| count(results, "FAIL") == 0 and count(results, "UNKNOWN") == 0

	## The report for one repository: a line for each check under the chapter's
	## headings, then the fixes, then a summary that stands on its own.
	report : Str, List(Check), Bool -> Str
	report = |name, results, colour| {
		sections = unique(results.map(|check| check.heading), []).join_map(
			|heading| ["", "== ${heading}"].concat(results.keep_if(|check| check.heading == heading).map(|check| "${word(label(check), colour)} ${check.text}")),
		)
		fixes = results.join_map(
			|check|
				match check.outcome {
					Fail(fix) => ["  ${check.text}", "      ${fix}"]
					_ => []
				},
		)
		Str.join_with(
			[name]
				.concat(sections)
				.concat(if fixes.is_empty() [] else ["", "Fixes"].concat(fixes))
				.concat(["", "Summary: ${name}: ${count(results, "PASS").to_str()} passed, ${count(results, "FAIL").to_str()} failed, ${count(results, "UNKNOWN").to_str()} unknown, ${count(results, "NOTE").to_str()} noted"]),
			"\n",
		)
	}
}

# Shared actions are referenced under this prefix.
shared_actions = "lukewilliamboswell/roc-automation/"

# What each response is read as. A field GitHub may send as `null` is never
# declared, because `Json.parse` does not read `null` as a missing field.

repository_details : Settings.Reply -> Try({ default_branch : Str, allow_merge_commit : Try(Bool, [Missing]), permissions : Try({ admin : Bool }, [Missing]) }, Str)
repository_details = |answer| {
	parsed : Try({ default_branch : Str, allow_merge_commit : Try(Bool, [Missing]), permissions : Try({ admin : Bool }, [Missing]) }, _)
	parsed = Json.parse(body(answer)?)
	parsed.map_err(|_| unexpected)
}

Ruleset : {
	id : U64,
	name : Str,
	target : Str,
	enforcement : Str,
	conditions : Try({ ref_name : Try({ include : List(Str), exclude : List(Str) }, [Missing]) }, [Missing]),
	rules : Try(List({ type : Str }), [Missing]),
	bypass_actors : Try(List({ actor_type : Str }), [Missing]),
}

ruleset : Settings.Reply -> Try(Ruleset, Str)
ruleset = |answer| {
	parsed : Try(Ruleset, _)
	parsed = Json.parse(body(answer)?)
	parsed.map_err(|_| unexpected)
}

ruleset_list : Settings.Reply -> Try(List({ id : U64 }), Str)
ruleset_list = |answer| {
	parsed : Try(List({ id : U64 }), _)
	parsed = Json.parse(body(answer)?)
	parsed.map_err(|_| unexpected)
}

Rule : {
	type : Str,
	ruleset_id : U64,
	parameters : Try(
		{
			strict_required_status_checks_policy : Try(Bool, [Missing]),
			required_status_checks : Try(List({ context : Str }), [Missing]),
			allowed_merge_methods : Try(List(Str), [Missing]),
		},
		[Missing],
	),
}

rules : Settings.Reply -> Try(List(Rule), Str)
rules = |answer| {
	parsed : Try(List(Rule), _)
	parsed = Json.parse(body(answer)?)
	parsed.map_err(|_| unexpected)
}

allowed_actions : Settings.Reply -> Try({ allowed_actions : Str }, Str)
allowed_actions = |answer| {
	parsed : Try({ allowed_actions : Str }, _)
	parsed = Json.parse(body(answer)?)
	parsed.map_err(|_| unexpected)
}

enabled : Settings.Reply -> Try(Bool, Str)
enabled = |answer| {
	parsed : Try({ enabled : Bool }, _)
	parsed = Json.parse(body(answer)?)
	parsed.map_ok(|setting| setting.enabled).map_err(|_| unexpected)
}

pulls : Settings.Reply -> Try(List({ number : U64, head : { sha : Str } }), Str)
pulls = |answer| {
	parsed : Try(List({ number : U64, head : { sha : Str } }), _)
	parsed = Json.parse(body(answer)?)
	parsed.map_err(|_| unexpected)
}

protection : Settings.Reply -> Try(List(Str), Str)
protection = |answer| {
	parsed : Try({ protection_rules : List({ type : Str }) }, _)
	parsed = Json.parse(body(answer)?)
	parsed.map_ok(|found| found.protection_rules.map(|rule| rule.type)).map_err(|_| unexpected)
}

unexpected = "the response was not in the expected shape"

body : Settings.Reply -> Try(Str, Str)
body = |answer|
	match answer {
		Body(text) => Ok(text)
		NotFound(reason) => Err(reason)
		Unreadable(reason) => Err(reason)
	}

# == Actions

workflow_token : Str, Settings.Reply -> List(Settings.Check)
workflow_token = |name, answer| {
	parsed : Try({ default_workflow_permissions : Str, can_approve_pull_request_reviews : Bool }, Str)
	parsed = match body(answer) {
		Ok(text) => {
			found : Try({ default_workflow_permissions : Str, can_approve_pull_request_reviews : Bool }, _)
			found = Json.parse(text)
			found.map_err(|_| unexpected)
		}
		Err(reason) => Err(reason)
	}
	change = "gh api --method PUT repos/${name}/actions/permissions/workflow"
	match parsed {
		Ok(setting) => [
			if setting.default_workflow_permissions == "read" pass("Actions", "the default workflow token is read-only") else fail("Actions", "the default workflow token is ${setting.default_workflow_permissions}, not read-only", "${change} -f default_workflow_permissions=read"),
			if setting.can_approve_pull_request_reviews fail("Actions", "workflows may create and approve pull requests", "${change} -F can_approve_pull_request_reviews=false") else pass("Actions", "workflows cannot create or approve pull requests"),
		]
		Err(reason) => [unknown("Actions", "the default workflow token and pull request approval", reason)]
	}
}

allowed : Str, Settings.Reply, Settings.Reply -> Settings.Check
allowed = |name, policy, selected| {
	page = "https://github.com/${name}/settings/actions"
	patterns : Try({ patterns_allowed : List(Str) }, _)
	patterns = match selected {
		Body(text) => Json.parse(text)
		_ => Err(NotRead)
	}
	match allowed_actions(policy) {
		Ok({ allowed_actions: "all" }) => pass("Actions", "actions are not restricted, so every shared action may run")
		Ok({ allowed_actions: "selected" }) =>
			match patterns {
				Ok({ patterns_allowed }) => {
					shared = patterns_allowed.keep_if(|pattern| pattern.starts_with(shared_actions))
					match shared.keep_if(|pattern| !at_full_sha(pattern)) {
						[] if shared.is_empty() => fail("Actions", "actions are restricted and the allowlist names no shared action under ${shared_actions}", "${page}: allow each shared action as ${shared_actions}actions/<name>@<full SHA>")
						[] => pass("Actions", "the allowlist names ${shared.len().to_str()} shared action(s), each at a full SHA")
						[pattern, ..] => fail("Actions", "the allowlist entry ${pattern} is not at a full SHA, so it does not match the reviewed action", "${page}: end the entry with @<full SHA>")
					}
				}
				Err(_) => unknown("Actions", "the action allowlist", reason_of(selected))
			}
		Ok({ allowed_actions: other }) => fail("Actions", "actions are restricted to ${other}, so no shared action may run", "${page}: allow selected actions, and each shared action at its full SHA")
		Err(reason) => unknown("Actions", "which actions may run", reason)
	}
}

# == Branch rules

branch_rules : Str, Str, Settings.Reply, List(Ruleset), List(Str) -> List(Settings.Check)
branch_rules = |name, default_branch, answer, rulesets, unread| {
	page = "https://github.com/${name}/settings/rules"
	match rules(answer) {
		Err(reason) => [unknown("Branch rules", "the rules on the default branch", reason)]
		Ok([]) => [fail("Branch rules", "no active ruleset targets the default branch ${default_branch}", "${page}: add a branch ruleset that targets the default branch")]
		Ok(found) => {
			enforces = |kind| found.any(|rule| rule.type == kind)
			status = found.keep_if(|rule| rule.type == "required_status_checks")
			strict = status.any(
				|rule|
					match rule.parameters {
						Ok({ strict_required_status_checks_policy: Ok(policy), .. }) => policy
						_ => Bool.False
					},
			)
			applying = rulesets.keep_if(|candidate| found.any(|rule| rule.ruleset_id == candidate.id))
			bots = applying.keep_if(|candidate| bot_bypass(candidate) == Ok(Bool.True))
			hidden = applying.keep_if(|candidate| bot_bypass(candidate).is_err())
			[
				if enforces("pull_request") pass("Branch rules", "${default_branch} requires a pull request") else fail("Branch rules", "${default_branch} does not require a pull request", "${page}: enable \"Require a pull request before merging\""),
				if status.is_empty() {
					fail("Branch rules", "${default_branch} requires no status checks", "${page}: enable \"Require status checks to pass\" and name the required jobs")
				} else if required_names(found).is_empty() {
					fail("Branch rules", "${default_branch} requires status checks but names none", "${page}: name the required jobs")
				} else if strict {
					pass("Branch rules", "${default_branch} requires status checks on an up-to-date branch")
				} else {
					fail("Branch rules", "${default_branch} requires status checks but not an up-to-date branch", "${page}: enable \"Require branches to be up to date before merging\"")
				},
				if enforces("required_signatures") pass("Branch rules", "${default_branch} requires signed commits") else fail("Branch rules", "${default_branch} does not require signed commits", "${page}: enable \"Require signed commits\""),
				match bots {
					[candidate, ..] => fail("Branch rules", "the ruleset \"${candidate.name}\" lets an app or a deploy key bypass it", "https://github.com/${name}/rules/${candidate.id.to_str()}: remove every app and deploy key from the bypass list")
					[] if !hidden.is_empty() or !unread.is_empty() or applying.is_empty() => unknown("Branch rules", "who may bypass the rules", unread.first() ?? "GitHub did not list the bypass actors")
					[] => pass("Branch rules", "no app or deploy key may bypass the rules")
				},
			]
		}
	}
}

# Whether a ruleset lets automation past it. An app, which is what a bot is,
# has the actor type Integration; a deploy key is a credential automation holds.
bot_bypass : Ruleset -> Try(Bool, [Hidden])
bot_bypass = |candidate|
	match candidate.bypass_actors {
		Ok(actors) => Ok(actors.any(|actor| actor.actor_type == "Integration" or actor.actor_type == "DeployKey"))
		Err(Missing) => Err(Hidden)
	}

required_names : List(Rule) -> List(Str)
required_names = |found|
	found.join_map(
		|rule|
			match rule.parameters {
				Ok({ required_status_checks: Ok(required), .. }) => required.map(|check| check.context)
				_ => []
			},
	)

# A required name that no job produces blocks every pull request, or protects
# nothing once someone removes it to unblock one. The jobs compared with are
# those that ran on the pull request behind the latest default-branch commit.
required_checks : Str, Settings.Reply, Settings.Reply, Settings.Reply -> List(Settings.Check)
required_checks = |name, branch, head_pulls, runs| {
	produced : Try({ total_count : U64, check_runs : List({ name : Str }) }, Str)
	produced = match body(runs) {
		Ok(text) => {
			found : Try({ total_count : U64, check_runs : List({ name : Str }) }, _)
			found = Json.parse(text)
			found.map_err(|_| unexpected)
		}
		Err(reason) => Err(reason)
	}
	match rules(branch).map_ok(required_names) {
		Ok([]) => []
		Err(_) => []
		Ok(required) =>
			match (Settings.head_pull(head_pulls), produced) {
				(Err(reason), _) => [unknown("Branch rules", "whether a job produces each required check", reason)]
				(_, Err(reason)) => [unknown("Branch rules", "whether a job produces each required check", reason)]
				(Ok(pull), Ok({ total_count, check_runs })) => {
					names = check_runs.map(|run| run.name)
					missing = required.keep_if(|context| !names.contains(context))
					if total_count > check_runs.len() {
						[unknown("Branch rules", "whether a job produces each required check", "pull request #${pull.number.to_str()} has more check runs than one request returns")]
					} else if missing.is_empty() {
						[pass("Branch rules", "a job on pull request #${pull.number.to_str()} produced each of the ${required.len().to_str()} required checks")]
					} else {
						[fail("Branch rules", "no job on pull request #${pull.number.to_str()} produced the required check(s): ${Str.join_with(missing, ", ")}", "https://github.com/${name}/settings/rules: require only the names the jobs produce")]
					}
				}
			}
	}
}

ruleset_notes : Str, Settings.Reply, List(Ruleset) -> List(Settings.Check)
ruleset_notes = |default_branch, branch, rulesets| {
	idle = rulesets.keep_if(|candidate| candidate.enforcement != "active").map(|candidate| note("Branch rules", "the ${candidate.target} ruleset \"${candidate.name}\" is ${candidate.enforcement} and enforces nothing"))
	applying = match rules(branch) {
		Ok(found) => rulesets.keep_if(|candidate| found.any(|rule| rule.ruleset_id == candidate.id))
		Err(_) => []
	}
	several = if applying.len() > 1 [note("Branch rules", "${applying.len().to_str()} rulesets apply to ${default_branch}: ${Str.join_with(applying.map(|candidate| candidate.name), ", ")}")] else []
	idle.concat(several)
}

merge_commits : Settings.Reply, Settings.Reply -> Settings.Check
merge_commits = |repo, branch| {
	by_rules = match rules(branch) {
		Ok(found) =>
			found.all(
				|rule|
					match rule.parameters {
						Ok({ allowed_merge_methods: Ok(methods), .. }) => methods.contains("merge")
						_ => Bool.True
					},
			)
		Err(_) => Bool.True
	}
	match repository_details(repo) {
		Ok({ allow_merge_commit: Ok(by_repository), .. }) =>
			if by_repository and by_rules {
				note("Branch rules", "merge commits are allowed, as a platform that publishes linker inputs needs")
			} else {
				note("Branch rules", "merge commits are not allowed; a platform that publishes linker inputs needs them")
			}
		_ => unknown("Branch rules", "whether merge commits are allowed", "GitHub did not say")
	}
}

# == Releases

immutable_releases : Str, Settings.Reply, Settings.Reply -> List(Settings.Check)
immutable_releases = |name, setting, latest| {
	release : Try({ tag_name : Str, immutable : Bool }, _)
	release = match latest {
		Body(text) => Json.parse(text)
		_ => Err(NotRead)
	}
	match enabled(setting) {
		Ok(on) if on =>
			[pass("Releases", "immutable releases are enabled")].concat(
				match release {
					Ok({ tag_name, immutable }) if !immutable => [note("Releases", "the latest release ${tag_name} was published before immutable releases were enabled and can still be changed")]
					_ => []
				},
			)
		Ok(_) => [fail("Releases", "immutable releases are not enabled", "gh api --method PUT repos/${name}/immutable-releases")]
		Err(reason) => [unknown("Releases", "immutable releases", reason)]
	}
}

# An environment is restricted to the default branch when its one deployment
# branch policy names that branch. "Protected branches only" is not the same:
# it follows whatever is protected, and so widens when a ruleset is added.
environment : Str, Str, Str, Str, Settings.Reply, Settings.Reply -> List(Settings.Check)
environment = |heading, name, env, default_branch, answer, policies| {
	page = "https://github.com/${name}/settings/environments"
	only_default = "${page}: under \"Deployment branches and tags\" choose \"Selected branches and tags\" and add only ${default_branch}"
	listed : Try({ branch_policies : List({ name : Str, type : Str }) }, _)
	listed = match policies {
		Body(text) => Json.parse(text)
		_ => Err(NotRead)
	}
	match (answer, protection(answer)) {
		(NotFound(_), _) => [fail(heading, "there is no `${env}` environment", "${page}: create `${env}`, restricted to ${default_branch}")]
		(_, Err(reason)) => [unknown(heading, "the `${env}` environment", reason)]
		(_, Ok(types)) => {
			exists = pass(heading, "the `${env}` environment exists")
			if !types.contains("branch_policy") {
				[exists, fail(heading, "any branch may deploy to `${env}`", only_default)]
			} else {
				match (policies, listed) {
					(NotFound(_), _) => [exists, fail(heading, "`${env}` accepts every protected branch, not ${default_branch} by name", only_default)]
					(_, Ok({ branch_policies: [{ name: branch, type: "branch" }] })) if branch == default_branch => [exists, pass(heading, "`${env}` is restricted to ${default_branch}")]
					(_, Ok({ branch_policies })) => [exists, fail(heading, "`${env}` accepts ${describe(branch_policies.map(|policy| "${policy.type} ${policy.name}"))}, not only ${default_branch}", only_default)]
					(_, Err(_)) => [exists, unknown(heading, "which branches may deploy to `${env}`", reason_of(policies))]
				}
			}
		}
	}
}

# A missing environment has already failed, so it says nothing more here.
reviewer : Str, Settings.Reply -> List(Settings.Check)
reviewer = |name, answer|
	match (answer, protection(answer)) {
		(NotFound(_), _) => []
		(_, Ok(types)) if types.contains("required_reviewers") => [pass("Releases", "the `release` environment has a required reviewer")]
		(_, Ok(_)) => [fail("Releases", "the `release` environment has no required reviewer", "https://github.com/${name}/settings/environments: add a required reviewer to `release`")]
		(_, Err(reason)) => [unknown("Releases", "the required reviewer of `release`", reason)]
	}

# A version tag cannot be moved or deleted when an active tag ruleset covers
# every tag and restricts both updates and deletions.
tag_rules : Str, List(Ruleset), List(Str) -> Settings.Check
tag_rules = |name, rulesets, unread| {
	protecting = rulesets.keep_if(
		|candidate| {
			kinds = match candidate.rules {
				Ok(found) => found.map(|rule| rule.type)
				Err(Missing) => []
			}
			every_tag = match candidate.conditions {
				Ok({ ref_name: Ok({ include, exclude }) }) => include.contains("~ALL") and exclude.is_empty()
				_ => Bool.False
			}
			candidate.target == "tag" and candidate.enforcement == "active" and every_tag and kinds.contains("update") and kinds.contains("deletion")
		},
	)
	match (protecting, unread) {
		([candidate, ..], _) => pass("Releases", "the tag ruleset \"${candidate.name}\" forbids updating and deleting any tag")
		([], [reason, ..]) => unknown("Releases", "the tag rulesets", reason)
		([], []) => fail("Releases", "no active tag ruleset covers all tags and restricts both updates and deletions", "https://github.com/${name}/settings/rules: add a tag ruleset that targets all tags and restricts updates and deletions")
	}
}

# == Pages

pages_source : Str, Settings.Reply -> Settings.Check
pages_source = |name, answer| {
	page = "https://github.com/${name}/settings/pages"
	parsed : Try({ build_type : Str }, _)
	parsed = match answer {
		Body(text) => Json.parse(text)
		_ => Err(NotRead)
	}
	match (answer, parsed) {
		(NotFound(_), _) => fail("Pages", "GitHub Pages is not enabled", "${page}: set the source to GitHub Actions, or pass --no-site for a repository that publishes no site")
		(_, Ok({ build_type: "workflow" })) => pass("Pages", "the Pages source is GitHub Actions")
		(_, Ok({ build_type })) => fail("Pages", "the Pages source is ${build_type}, not GitHub Actions", "${page}: set the source to GitHub Actions")
		(_, Err(_)) => unknown("Pages", "the Pages source", reason_of(answer))
	}
}

# == Dependencies

dependabot : Str, Settings.Reply -> Settings.Check
dependabot = |name, answer| {
	fix = "add to .github/dependabot.yml of ${name} an `updates` entry with `package-ecosystem: github-actions` and `schedule.interval: weekly`"
	match answer {
		Body(text) if weekly_actions(text) => pass("Dependencies", ".github/dependabot.yml has a weekly github-actions entry")
		Body(_) => fail("Dependencies", ".github/dependabot.yml has no weekly github-actions entry", fix)
		NotFound(_) => fail("Dependencies", "there is no .github/dependabot.yml", fix)
		Unreadable(reason) => unknown("Dependencies", ".github/dependabot.yml", reason)
	}
}

# Whether the text of a dependabot.yml has an update entry for github-actions
# with a weekly schedule. It reads the layout Dependabot documents, a block
# list of entries, and no more of YAML than that.
weekly_actions : Str -> Bool
weekly_actions = |text| {
	lines = Str.split_on(text, "\n").map(|line| line.drop_suffix("\r")).keep_if(|line| line.trim() != "" and !line.trim().starts_with("#"))
	starts = lines.keep_if(|line| line.trim().starts_with("- ")).map(indent)
	entries(lines, smallest(starts), [], []).any(
		|entry| entry.any(|line| yaml_value(line, "package-ecosystem") == Ok("github-actions")) and entry.any(|line| yaml_value(line, "interval") == Ok("weekly")),
	)
}

# Split the lines into list entries, each starting at a `- ` with `depth` spaces before it.
entries : List(Str), U64, List(Str), List(List(Str)) -> List(List(Str))
entries = |lines, depth, current, done|
	match lines {
		[] => done.append(current)
		[line, .. as rest] if line.trim().starts_with("- ") and indent(line) == depth => entries(rest, depth, [line.trim().drop_prefix("- ")], done.append(current))
		[line, .. as rest] => entries(rest, depth, current.append(line.trim()), done)
	}

yaml_value : Str, Str -> Try(Str, [OtherKey])
yaml_value = |line, key|
	if line.starts_with("${key}:") {
		uncommented = match Str.split_first(line.drop_prefix("${key}:"), " #") {
			Ok({ before, .. }) => before
			Err(_) => line.drop_prefix("${key}:")
		}
		Ok(Str.replace_each(Str.replace_each(uncommented, "\"", ""), "'", "").trim())
	} else {
		Err(OtherKey)
	}

indent : Str -> U64
indent = |line| Str.to_utf8(line).len() - Str.to_utf8(line.trim_start()).len()

smallest : List(U64) -> U64
smallest = |numbers|
	match numbers {
		[] => 0
		[only] => only
		[first, .. as rest] => {
			other = smallest(rest)
			if first < other first else other
		}
	}

# == Reporting

private_reporting : Str, Settings.Reply -> Settings.Check
private_reporting = |name, answer|
	match enabled(answer) {
		Ok(on) if on => pass("Reporting", "private vulnerability reporting is enabled")
		Ok(_) => fail("Reporting", "private vulnerability reporting is not enabled", "gh api --method PUT repos/${name}/private-vulnerability-reporting")
		Err(reason) => unknown("Reporting", "private vulnerability reporting", reason)
	}

security_policy : Str, Settings.Reply, Settings.Reply -> Settings.Check
security_policy = |name, root, in_github|
	match (root, in_github) {
		(Body(_), _) => pass("Reporting", "SECURITY.md exists")
		(_, Body(_)) => pass("Reporting", ".github/SECURITY.md exists")
		(NotFound(_), NotFound(_)) => fail("Reporting", "there is no SECURITY.md", "add SECURITY.md to ${name}, saying how to report a vulnerability privately")
		(Unreadable(reason), _) => unknown("Reporting", "SECURITY.md", reason)
		(_, Unreadable(reason)) => unknown("Reporting", "SECURITY.md", reason)
	}

# Helpers

pass : Str, Str -> Settings.Check
pass = |heading, text| { heading, outcome: Pass, text }

fail : Str, Str, Str -> Settings.Check
fail = |heading, text, fix| { heading, outcome: Fail(fix), text }

note : Str, Str -> Settings.Check
note = |heading, text| { heading, outcome: Note, text }

unknown : Str, Str, Str -> Settings.Check
unknown = |heading, what, reason| { heading, outcome: Unknown, text: "${what}: ${reason}" }

reason_of : Settings.Reply -> Str
reason_of = |answer|
	match answer {
		Body(_) => unexpected
		NotFound(reason) => reason
		Unreadable(reason) => reason
	}

label : Settings.Check -> Str
label = |check|
	match check.outcome {
		Pass => "PASS"
		Fail(_) => "FAIL"
		Note => "NOTE"
		Unknown => "UNKNOWN"
	}

count : List(Settings.Check), Str -> U64
count = |results, wanted| results.count_if(|check| label(check) == wanted)

# The word is always printed, padded to the longest. `colour` only adds to it.
word : Str, Bool -> Str
word = |text, colour| {
	code = match text {
		"PASS" => "32"
		"FAIL" => "31"
		"NOTE" => "36"
		_ => "33"
	}
	padding = Str.repeat(" ", 7 - Str.to_utf8(text).len())
	if colour "\u(001b)[${code}m${text}\u(001b)[0m${padding}" else "${text}${padding}"
}

unique : List(Str), List(Str) -> List(Str)
unique = |values, seen|
	match values {
		[] => seen
		[first, .. as rest] => unique(rest, if seen.contains(first) seen else seen.append(first))
	}

describe : List(Str) -> Str
describe = |items| if items.is_empty() "nothing" else Str.join_with(items, ", ")

first_line : Str -> Str
first_line = |text|
	match Str.split_on(text.trim(), "\n") {
		[line, ..] => line
		[] => ""
	}

valid_repository : Str -> Bool
valid_repository = |name|
	match Str.split_on(name, "/") {
		[owner, repo] => is_plain(owner, "-") and is_plain(repo, "-._")
		_ => Bool.False
	}

# Whether `text` is non-empty and made only of ASCII letters, digits and the bytes of `extra`.
is_plain : Str, Str -> Bool
is_plain = |text, extra| {
	extras = Str.to_utf8(extra)
	text != "" and Str.to_utf8(text).all(|byte| (byte >= '0' and byte <= '9') or (byte >= 'a' and byte <= 'z') or (byte >= 'A' and byte <= 'Z') or extras.contains(byte))
}

# Whether an allowlist entry ends in `@` and a full commit SHA.
at_full_sha : Str -> Bool
at_full_sha = |pattern|
	match Str.split_on(pattern, "@") {
		[_, sha] => Str.to_utf8(sha).len() == 40 and Str.to_utf8(sha).all(|byte| (byte >= '0' and byte <= '9') or (byte >= 'a' and byte <= 'f'))
		_ => Bool.False
	}

# Tests. The fixtures are GitHub's own responses, cut down to a few fields and
# keeping some the checks do not read, including `null` ones.

kinds : List(Settings.Check) -> List(Str)
kinds = |results| results.map(label)

denied : Settings.Reply
denied = Unreadable("gh api x answered 403")

absent : Settings.Reply
absent = NotFound("gh api x answered 404")

expect Settings.get_args("repos/o/r/rulesets", Json) == ["api", "--method", "GET", "-H", "Accept: application/vnd.github+json", "repos/o/r/rulesets"]
expect Settings.get_args("repos/o/r/contents/SECURITY.md", Raw) == ["api", "--method", "GET", "-H", "Accept: application/vnd.github.raw", "repos/o/r/contents/SECURITY.md"]

expect Settings.parse_args(["o/r"]) == Ok({ repositories: ["o/r"], options: { linker_inputs: Bool.False, reviewer_required: Bool.False, site: Bool.True } })
expect Settings.parse_args(["--platform", "o/r", "--no-site", "o/r.2", "--reviewer-required"]) == Ok({ repositories: ["o/r", "o/r.2"], options: { linker_inputs: Bool.True, reviewer_required: Bool.True, site: Bool.False } })
expect Settings.parse_args(["o/r", "--help"]) == Err(Help)
expect Settings.parse_args([]) == Err(Invalid("name at least one repository"))
expect Settings.parse_args(["--fix", "o/r"]) == Err(Invalid("unknown flag --fix"))
expect Settings.parse_args(["o/r/rulesets"]) == Err(Invalid("o/r/rulesets is not an <owner>/<name> repository"))
expect Settings.parse_args(["o/r?x=1"]).is_err() and Settings.parse_args(["roc-ansi"]).is_err()

not_found = "{\"message\":\"Not Found\",\"documentation_url\":\"https://docs.github.com/rest\",\"status\":\"404\"}"

expect Settings.reply({ path: "repos/o/r", admin: Bool.False, succeeded: Bool.True, stdout: "{}", stderr: "" }) == Body("{}")
expect Settings.reply({ path: "repos/o/r/pages", admin: Bool.True, succeeded: Bool.False, stdout: not_found, stderr: "gh: Not Found (HTTP 404)" }) == NotFound("gh api repos/o/r/pages answered 404")
# Without administration access a 404 may be hiding the setting.
expect Settings.reply({ path: "repos/o/r/pages", admin: Bool.False, succeeded: Bool.False, stdout: not_found, stderr: "" }) == Unreadable("gh api repos/o/r/pages answered 404")
expect Settings.reply({ path: "repos/o/r", admin: Bool.True, succeeded: Bool.False, stdout: "{\"message\":\"Forbidden\",\"status\":\"403\"}", stderr: "" }) == Unreadable("gh api repos/o/r answered 403")
expect Settings.reply({ path: "repos/o/r", admin: Bool.True, succeeded: Bool.False, stdout: "", stderr: "gh: not logged in\nrun gh auth login\n" }) == Unreadable("gh api repos/o/r failed: gh: not logged in")

repo = "{\"name\":\"r\",\"default_branch\":\"main\",\"homepage\":null,\"allow_merge_commit\":true,\"permissions\":{\"admin\":true,\"push\":true}}"

expect Settings.repository(Body(repo)) == Ok({ default_branch: "main", admin: Bool.True })
expect Settings.repository(Body("{\"default_branch\":\"main\"}")) == Ok({ default_branch: "main", admin: Bool.False })
expect Settings.repository(Body("{\"default_branch\":\"a?b\"}")).is_err() and Settings.repository(denied).is_err()

expect Settings.ruleset_ids(Body("[{\"id\":22403604,\"name\":\"Protect main\"},{\"id\":7}]")) == [22403604, 7]
expect Settings.ruleset_ids(Body("[]")) == [] and Settings.ruleset_ids(denied) == []

expect Settings.restricts_actions(Body("{\"enabled\":true,\"allowed_actions\":\"selected\"}"))
expect !Settings.restricts_actions(Body("{\"enabled\":true,\"allowed_actions\":\"all\"}")) and !Settings.restricts_actions(denied)

pull = "[{\"number\":83,\"milestone\":null,\"merged_at\":\"2026-10-12T03:26:08Z\",\"head\":{\"sha\":\"43f1083fa8624f117e90cdef6d1fe1e20ca5ced3\",\"ref\":\"topic\"}}]"

expect Settings.head_pull(Body(pull)) == Ok({ number: 83, sha: "43f1083fa8624f117e90cdef6d1fe1e20ca5ced3" })
expect Settings.head_pull(Body("[]")).is_err() and Settings.head_pull(denied) == Err("gh api x answered 403")

# Actions

expect kinds(workflow_token("o/r", Body("{\"default_workflow_permissions\":\"read\",\"can_approve_pull_request_reviews\":false}"))) == ["PASS", "PASS"]
expect workflow_token("o/r", Body("{\"default_workflow_permissions\":\"write\",\"can_approve_pull_request_reviews\":true}")) == [
	fail("Actions", "the default workflow token is write, not read-only", "gh api --method PUT repos/o/r/actions/permissions/workflow -f default_workflow_permissions=read"),
	fail("Actions", "workflows may create and approve pull requests", "gh api --method PUT repos/o/r/actions/permissions/workflow -F can_approve_pull_request_reviews=false"),
]
expect kinds(workflow_token("o/r", denied)) == ["UNKNOWN"] and kinds(workflow_token("o/r", absent)) == ["UNKNOWN"] and kinds(workflow_token("o/r", Body("[]"))) == ["UNKNOWN"]

selected_only : Settings.Reply
selected_only = Body("{\"enabled\":true,\"allowed_actions\":\"selected\",\"sha_pinning_required\":true}")

allowlist : Str -> Settings.Reply
allowlist = |entry| Body("{\"github_owned_allowed\":true,\"patterns_allowed\":[\"roc-lang/*\",\"${entry}\"],\"verified_allowed\":false}")

expect label(allowed("o/r", Body("{\"allowed_actions\":\"all\"}"), denied)) == "PASS"
expect label(allowed("o/r", selected_only, allowlist("lukewilliamboswell/roc-automation/actions/setup-blueprint@00f3498e2f0a9a4a6a8f8f0a0c7e1b2d3c4d5e6f"))) == "PASS"
expect label(allowed("o/r", selected_only, allowlist("lukewilliamboswell/roc-automation/actions/setup-blueprint"))) == "FAIL"
expect label(allowed("o/r", selected_only, allowlist("lukewilliamboswell/roc-automation/actions/setup-blueprint@v1"))) == "FAIL"
expect label(allowed("o/r", selected_only, allowlist("lukewilliamboswell/roc-automation/*@*"))) == "FAIL"
expect label(allowed("o/r", selected_only, allowlist("cachix/install-nix-action@*"))) == "FAIL"
expect label(allowed("o/r", Body("{\"allowed_actions\":\"local_only\"}"), denied)) == "FAIL"
expect label(allowed("o/r", selected_only, denied)) == "UNKNOWN" and label(allowed("o/r", denied, denied)) == "UNKNOWN"

# Branch rules

branch_rule_list : Str, Str -> Settings.Reply
branch_rule_list = |strictness, required|
	Body("[{\"type\":\"deletion\",\"ruleset_id\":1},{\"type\":\"required_signatures\",\"ruleset_id\":1},{\"type\":\"pull_request\",\"ruleset_id\":1,\"parameters\":{\"required_approving_review_count\":0,\"allowed_merge_methods\":[\"merge\",\"squash\"]}},{\"type\":\"required_status_checks\",\"ruleset_id\":1,\"parameters\":{\"strict_required_status_checks_policy\":${strictness},\"required_status_checks\":[${required}]}}]")

good_rules : Settings.Reply
good_rules = branch_rule_list("true", "{\"context\":\"test\",\"integration_id\":15368},{\"context\":\"Build release bundle\",\"integration_id\":15368}")

unparsed : Ruleset
unparsed = { id: 0, name: "unparsed", target: "", enforcement: "", conditions: Err(Missing), rules: Err(Missing), bypass_actors: Err(Missing) }

branch_ruleset : Str, Str -> Ruleset
branch_ruleset = |enforcement, actors|
	ruleset(Body("{\"id\":1,\"name\":\"Protect main\",\"target\":\"branch\",\"enforcement\":\"${enforcement}\",\"conditions\":{\"ref_name\":{\"exclude\":[],\"include\":[\"~DEFAULT_BRANCH\"]}},\"rules\":[{\"type\":\"deletion\"}]${actors}}")) ?? unparsed

admins_only : Ruleset
admins_only = branch_ruleset("active", ",\"bypass_actors\":[{\"actor_id\":5,\"actor_type\":\"RepositoryRole\",\"bypass_mode\":\"always\"}]")

expect admins_only.name == "Protect main" and admins_only.bypass_actors == Ok([{ actor_type: "RepositoryRole" }])
expect kinds(branch_rules("o/r", "main", good_rules, [admins_only], [])) == ["PASS", "PASS", "PASS", "PASS"]
expect kinds(branch_rules("o/r", "main", Body("[]"), [], [])) == ["FAIL"]
expect kinds(branch_rules("o/r", "main", Body("[{\"type\":\"deletion\",\"ruleset_id\":1}]"), [admins_only], [])) == ["FAIL", "FAIL", "FAIL", "PASS"]
expect kinds(branch_rules("o/r", "main", branch_rule_list("false", "{\"context\":\"test\"}"), [admins_only], [])) == ["PASS", "FAIL", "PASS", "PASS"]
expect kinds(branch_rules("o/r", "main", branch_rule_list("true", ""), [admins_only], [])) == ["PASS", "FAIL", "PASS", "PASS"]
expect kinds(branch_rules("o/r", "main", good_rules, [branch_ruleset("active", ",\"bypass_actors\":[{\"actor_id\":15368,\"actor_type\":\"Integration\",\"bypass_mode\":\"always\"}]")], [])) == ["PASS", "PASS", "PASS", "FAIL"]
expect kinds(branch_rules("o/r", "main", good_rules, [branch_ruleset("active", ",\"bypass_actors\":[{\"actor_id\":null,\"actor_type\":\"DeployKey\",\"bypass_mode\":\"always\"}]")], [])) == ["PASS", "PASS", "PASS", "FAIL"]
# GitHub leaves out the bypass list for a login that cannot administer the repository.
expect kinds(branch_rules("o/r", "main", good_rules, [branch_ruleset("active", "")], [])) == ["PASS", "PASS", "PASS", "UNKNOWN"]
expect kinds(branch_rules("o/r", "main", good_rules, [], ["gh api x answered 403"])) == ["PASS", "PASS", "PASS", "UNKNOWN"]
expect kinds(branch_rules("o/r", "main", denied, [admins_only], [])) == ["UNKNOWN"]

runs : Str -> Settings.Reply
runs = |total| Body("{\"total_count\":${total},\"check_runs\":[{\"name\":\"test\",\"conclusion\":null},{\"name\":\"Build release bundle\",\"conclusion\":\"success\"},{\"name\":\"Deploy the site\"}]}")

expect kinds(required_checks("o/r", good_rules, Body(pull), runs("3"))) == ["PASS"]
expect required_checks("o/r", branch_rule_list("true", "{\"context\":\"test\"},{\"context\":\"release-validation\"},{\"context\":\"codeql\"}"), Body(pull), runs("3")) == [
	fail("Branch rules", "no job on pull request #83 produced the required check(s): release-validation, codeql", "https://github.com/o/r/settings/rules: require only the names the jobs produce"),
]
expect kinds(required_checks("o/r", good_rules, Body("[]"), runs("3"))) == ["UNKNOWN"]
expect kinds(required_checks("o/r", good_rules, Body(pull), denied)) == ["UNKNOWN"]
expect kinds(required_checks("o/r", good_rules, Body(pull), runs("240"))) == ["UNKNOWN"]
# With no required names there is nothing to compare, and the missing rule has already failed.
expect required_checks("o/r", Body("[]"), Body(pull), runs("3")) == [] and required_checks("o/r", denied, Body(pull), runs("3")) == []

second_ruleset : Ruleset
second_ruleset = { ..admins_only, id: 2, name: "CI" }

expect ruleset_notes("main", good_rules, [admins_only]) == []
expect ruleset_notes("main", good_rules, [admins_only, { ..second_ruleset, enforcement: "disabled" }]) == [note("Branch rules", "the branch ruleset \"CI\" is disabled and enforces nothing")]
expect ruleset_notes("main", Body("[{\"type\":\"deletion\",\"ruleset_id\":1},{\"type\":\"pull_request\",\"ruleset_id\":2}]"), [admins_only, second_ruleset]) == [note("Branch rules", "2 rulesets apply to main: Protect main, CI")]

expect merge_commits(Body(repo), good_rules) == note("Branch rules", "merge commits are allowed, as a platform that publishes linker inputs needs")
expect merge_commits(Body("{\"default_branch\":\"main\",\"allow_merge_commit\":false}"), good_rules) == note("Branch rules", "merge commits are not allowed; a platform that publishes linker inputs needs them")
expect merge_commits(Body(repo), Body("[{\"type\":\"pull_request\",\"ruleset_id\":1,\"parameters\":{\"allowed_merge_methods\":[\"squash\"]}}]")).text.starts_with("merge commits are not allowed")
expect label(merge_commits(Body("{\"default_branch\":\"main\"}"), good_rules)) == "UNKNOWN"

# Releases

expect kinds(immutable_releases("o/r", Body("{\"enabled\":true,\"enforced_by_owner\":false}"), Body("{\"tag_name\":\"0.13.2\",\"immutable\":true}"))) == ["PASS"]
expect kinds(immutable_releases("o/r", Body("{\"enabled\":true}"), absent)) == ["PASS"]
expect kinds(immutable_releases("o/r", Body("{\"enabled\":true}"), Body("{\"tag_name\":\"0.13.1\",\"immutable\":false}"))) == ["PASS", "NOTE"]
expect immutable_releases("o/r", Body("{\"enabled\":false,\"enforced_by_owner\":false}"), absent) == [fail("Releases", "immutable releases are not enabled", "gh api --method PUT repos/o/r/immutable-releases")]
expect kinds(immutable_releases("o/r", denied, absent)) == ["UNKNOWN"] and kinds(immutable_releases("o/r", absent, absent)) == ["UNKNOWN"]

restricted : Settings.Reply
restricted = Body("{\"name\":\"release\",\"protection_rules\":[{\"id\":1,\"type\":\"branch_policy\"}],\"deployment_branch_policy\":{\"protected_branches\":false,\"custom_branch_policies\":true}}")

policy_list : Str -> Settings.Reply
policy_list = |listed| Body("{\"total_count\":1,\"branch_policies\":[${listed}]}")

only_main : Settings.Reply
only_main = policy_list("{\"id\":1,\"name\":\"main\",\"type\":\"branch\"}")

expect kinds(environment("Releases", "o/r", "release", "main", restricted, only_main)) == ["PASS", "PASS"]
expect kinds(environment("Releases", "o/r", "release", "main", absent, denied)) == ["FAIL"]
expect kinds(environment("Releases", "o/r", "release", "main", Body("{\"name\":\"release\",\"protection_rules\":[],\"deployment_branch_policy\":null}"), denied)) == ["PASS", "FAIL"]
# "Protected branches only" has no list of policies to return.
expect kinds(environment("Releases", "o/r", "release", "main", restricted, absent)) == ["PASS", "FAIL"]
expect kinds(environment("Releases", "o/r", "release", "main", restricted, policy_list("{\"name\":\"main\",\"type\":\"branch\"},{\"name\":\"release/*\",\"type\":\"branch\"}"))) == ["PASS", "FAIL"]
expect kinds(environment("Releases", "o/r", "release", "main", restricted, policy_list("{\"name\":\"develop\",\"type\":\"branch\"}"))) == ["PASS", "FAIL"]
expect kinds(environment("Releases", "o/r", "release", "main", restricted, policy_list("{\"name\":\"main\",\"type\":\"tag\"}"))) == ["PASS", "FAIL"]
expect kinds(environment("Releases", "o/r", "release", "main", restricted, policy_list(""))) == ["PASS", "FAIL"]
expect kinds(environment("Releases", "o/r", "release", "main", restricted, denied)) == ["PASS", "UNKNOWN"]
expect kinds(environment("Pages", "o/r", "github-pages", "main", denied, denied)) == ["UNKNOWN"]

expect kinds(reviewer("o/r", Body("{\"protection_rules\":[{\"type\":\"required_reviewers\",\"reviewers\":[{\"type\":\"User\",\"reviewer\":{\"login\":\"x\"}}]},{\"type\":\"branch_policy\"}]}"))) == ["PASS"]
expect kinds(reviewer("o/r", restricted)) == ["FAIL"]
expect reviewer("o/r", absent) == [] and kinds(reviewer("o/r", denied)) == ["UNKNOWN"]

tag_ruleset : Str, Str, Str -> Ruleset
tag_ruleset = |enforcement, include, restrictions|
	ruleset(Body("{\"id\":3,\"name\":\"Protect release tags\",\"target\":\"tag\",\"enforcement\":\"${enforcement}\",\"conditions\":{\"ref_name\":{\"exclude\":[],\"include\":[\"${include}\"]}},\"rules\":[${restrictions}],\"bypass_actors\":[]}")) ?? unparsed

both = "{\"type\":\"deletion\"},{\"type\":\"update\"}"

expect tag_ruleset("active", "~ALL", both).name == "Protect release tags"
expect label(tag_rules("o/r", [admins_only, tag_ruleset("active", "~ALL", both)], [])) == "PASS"
expect label(tag_rules("o/r", [tag_ruleset("active", "~ALL", both)], ["gh api x answered 403"])) == "PASS"
expect label(tag_rules("o/r", [admins_only], [])) == "FAIL" and label(tag_rules("o/r", [], [])) == "FAIL"
expect label(tag_rules("o/r", [tag_ruleset("disabled", "~ALL", both)], [])) == "FAIL"
expect label(tag_rules("o/r", [tag_ruleset("active", "refs/tags/v*", both)], [])) == "FAIL"
expect label(tag_rules("o/r", [tag_ruleset("active", "~ALL", "{\"type\":\"deletion\"}")], [])) == "FAIL"
expect label(tag_rules("o/r", [tag_ruleset("active", "~ALL", "{\"type\":\"update\"}")], [])) == "FAIL"
expect label(tag_rules("o/r", [{ ..tag_ruleset("active", "~ALL", both), target: "branch" }], [])) == "FAIL"
expect label(tag_rules("o/r", [admins_only], ["gh api x answered 403"])) == "UNKNOWN"

# Pages

expect label(pages_source("o/r", Body("{\"status\":null,\"cname\":null,\"build_type\":\"workflow\",\"source\":{\"branch\":\"main\",\"path\":\"/\"}}"))) == "PASS"
expect label(pages_source("o/r", Body("{\"status\":\"built\",\"build_type\":\"legacy\"}"))) == "FAIL"
expect label(pages_source("o/r", absent)) == "FAIL" and label(pages_source("o/r", denied)) == "UNKNOWN" and label(pages_source("o/r", Body("{}"))) == "UNKNOWN"

# Dependencies

flat = "version: 2\nupdates:\n- package-ecosystem: github-actions\n  directory: /\n  schedule:\n    interval: weekly\n"

nested = "# Dependabot\nversion: 2\nupdates:\n  - package-ecosystem: \"npm\"\n    directory: /site\n    schedule:\n      interval: daily\n  - directory: /\n    package-ecosystem: 'github-actions' # shared actions\n    schedule:\n      interval: \"weekly\"\n      day: monday\n    groups:\n      github-actions:\n        patterns:\n          - \"*\"\n"

# The weekly schedule belongs to another ecosystem's entry.
crossed = "version: 2\nupdates:\n  - package-ecosystem: npm\n    directory: /\n    schedule:\n      interval: weekly\n  - package-ecosystem: github-actions\n    directory: /\n    schedule:\n      interval: monthly\n"

expect weekly_actions(flat) and weekly_actions(nested) and weekly_actions(Str.replace_each(nested, "\n", "\r\n"))
expect !weekly_actions(crossed) and !weekly_actions(Str.replace_each(flat, "weekly", "monthly")) and !weekly_actions("version: 2\nupdates: []\n") and !weekly_actions("")
expect !weekly_actions(Str.replace_each(flat, "- package-ecosystem: github-actions", "# - package-ecosystem: github-actions\n- package-ecosystem: cargo"))
expect label(dependabot("o/r", Body(flat))) == "PASS" and label(dependabot("o/r", Body(crossed))) == "FAIL"
expect label(dependabot("o/r", absent)) == "FAIL" and label(dependabot("o/r", denied)) == "UNKNOWN"

# Reporting

expect label(private_reporting("o/r", Body("{\"enabled\":true}"))) == "PASS"
expect private_reporting("o/r", Body("{\"enabled\":false}")) == fail("Reporting", "private vulnerability reporting is not enabled", "gh api --method PUT repos/o/r/private-vulnerability-reporting")
expect label(private_reporting("o/r", denied)) == "UNKNOWN" and label(private_reporting("o/r", absent)) == "UNKNOWN"

expect label(security_policy("o/r", Body("# Security"), absent)) == "PASS" and label(security_policy("o/r", absent, Body("# Security"))) == "PASS"
expect label(security_policy("o/r", absent, absent)) == "FAIL"
expect label(security_policy("o/r", denied, absent)) == "UNKNOWN" and label(security_policy("o/r", absent, denied)) == "UNKNOWN"

# The whole chapter

adopted : Settings.Answers
adopted = {
	repository: Body(repo),
	workflow: Body("{\"default_workflow_permissions\":\"read\",\"can_approve_pull_request_reviews\":false}"),
	actions: Body("{\"allowed_actions\":\"all\"}"),
	selected_actions: denied,
	rulesets: [
		Body("{\"id\":1,\"name\":\"Protect main\",\"target\":\"branch\",\"enforcement\":\"active\",\"conditions\":{\"ref_name\":{\"exclude\":[],\"include\":[\"~DEFAULT_BRANCH\"]}},\"rules\":[{\"type\":\"deletion\"}],\"bypass_actors\":[]}"),
		Body("{\"id\":3,\"name\":\"Protect release tags\",\"target\":\"tag\",\"enforcement\":\"active\",\"conditions\":{\"ref_name\":{\"exclude\":[],\"include\":[\"~ALL\"]}},\"rules\":[{\"type\":\"deletion\"},{\"type\":\"update\"}],\"bypass_actors\":[]}"),
	],
	branch_rules: good_rules,
	head_pulls: Body(pull),
	check_runs: runs("3"),
	immutable: Body("{\"enabled\":true}"),
	latest_release: absent,
	release_environment: restricted,
	release_policies: only_main,
	pages: Body("{\"build_type\":\"workflow\"}"),
	pages_environment: restricted,
	pages_policies: only_main,
	dependabot: Body(flat),
	security: Body("# Security"),
	security_in_github: absent,
	private_reporting: Body("{\"enabled\":true}"),
}

plain : Settings.Options
plain = { linker_inputs: Bool.False, reviewer_required: Bool.False, site: Bool.True }

expect Settings.checks("o/r", plain, adopted).all(|check| check.outcome == Pass) and Settings.checks("o/r", plain, adopted).len() == 18
expect unique(Settings.checks("o/r", plain, adopted).map(|check| check.heading), []) == ["Actions", "Branch rules", "Releases", "Pages", "Dependencies", "Reporting"]
expect Settings.passed(Settings.checks("o/r", plain, adopted))
# The per-repository answers add and remove checks.
expect kinds(Settings.checks("o/r", { ..plain, linker_inputs: Bool.True }, adopted)).contains("NOTE") and Settings.passed(Settings.checks("o/r", { ..plain, linker_inputs: Bool.True }, adopted))
expect kinds(Settings.checks("o/r", { ..plain, reviewer_required: Bool.True }, adopted)).contains("FAIL")
expect !Settings.checks("o/r", { ..plain, site: Bool.False }, adopted).any(|check| check.heading == "Pages")
expect Settings.checks("o/r", { ..plain, site: Bool.False }, { ..adopted, pages: absent, pages_environment: absent }).all(|check| check.outcome == Pass)
# A setting that cannot be read is never a pass.
expect Settings.checks("o/r", plain, { ..adopted, repository: denied }) == [unknown("Repository", "the repository", "gh api x answered 403")]
expect !Settings.passed(Settings.checks("o/r", plain, { ..adopted, repository: Body("{\"default_branch\":\"main\",\"permissions\":{\"admin\":false}}") }))
expect !Settings.passed(Settings.checks("o/r", plain, { ..adopted, private_reporting: denied })) and !Settings.passed(Settings.checks("o/r", plain, { ..adopted, immutable: Body("{\"enabled\":false}") }))

expect Settings.passed([]) and Settings.passed([pass("A", "a"), note("A", "b")]) and !Settings.passed([pass("A", "a"), unknown("A", "b", "c")]) and !Settings.passed([fail("A", "a", "b")])

expect Settings.report("o/r", [pass("Actions", "one"), fail("Releases", "two", "do this"), note("Actions", "three"), unknown("Releases", "four", "gh api x answered 403")], Bool.False) == "o/r\n\n== Actions\nPASS    one\nNOTE    three\n\n== Releases\nFAIL    two\nUNKNOWN four: gh api x answered 403\n\nFixes\n  two\n      do this\n\nSummary: o/r: 1 passed, 1 failed, 1 unknown, 1 noted"
expect Settings.report("o/r", [pass("Actions", "one")], Bool.True) == "o/r\n\n== Actions\n\u(001b)[32mPASS\u(001b)[0m    one\n\nSummary: o/r: 1 passed, 0 failed, 0 unknown, 0 noted"
