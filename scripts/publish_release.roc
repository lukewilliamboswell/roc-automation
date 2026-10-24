#!/usr/bin/env roc-stable
app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
	docs: "../tools/roc-docs/release.roc",
	roc: "nightly-2026-10-09-258ab27",
}

import cli.Cmd
import cli.Env
import cli.OsStr
import cli.Path
import cli.Stderr
import cli.Stdout
import docs.BuildFiles
import docs.Sha256
import src/LifecycleAdmission
import src/GitHub
import src/PublishRelease

## Publish one release from files an earlier job built and tested. This is
## what `actions/publish-release` runs.
##
##     blueprint run publish-release
##
## Everything it is told arrives in the environment: the action's inputs as
## `INPUT_*`, the token as `GH_TOKEN`, and the runner's `GITHUB_*` variables
## and `RUNNER_TEMP`. Asset globs and the notes file are resolved against
## `GITHUB_WORKSPACE`, not against the directory this script runs in.
##
## What it decides is in `src/PublishRelease.roc`. This file carries each step
## that module asks for to the disk or to `gh`, and writes the outputs.
main! = |_args| {
	inputs = {
		version: env!("INPUT_VERSION")?,
		prerelease: env!("INPUT_PRERELEASE")?,
		notes_file: env!("INPUT_NOTES_FILE")?,
		assets: env!("INPUT_ASSETS")?,
		tag: env!("INPUT_TAG")?,
		target: env!("INPUT_TARGET")?,
		repository: env!("GITHUB_REPOSITORY")?,
		token: env!("GH_TOKEN")?,
		workspace: env!("GITHUB_WORKSPACE")?,
	}
	# `gather!` can only look at the disk, so every local check is over before the first request.
	made = match gather!(PublishRelease.collect(inputs)) {
		Ok(checked) => checked
		Err(reason) => return fail!(reason)
	}
	# Lifecycle callers require an admission result freshly reconstructed by
	# pinned trusted code in this same job. It is inert and rechecked here;
	# a candidate-produced report never substitutes for that controller step.
	admission = env!("INPUT_ADMISSION_FILE")?
	required = env!("INPUT_REQUIRE_ADMISSION")?
	if required != "" and required != "false" and required != "true" {
		return fail!("require-admission must be exactly true or false")
	}
	if admission == "" and required == "true" {
		return fail!("lifecycle publication requires the trusted admission report")
	}
	if admission != "" {
		checked = check_admission!(admission, made)
		match checked {
			Ok(_) => {}
			Err(reason) => return fail!(reason)
		}
	}
	output_file = match env!("GITHUB_OUTPUT")? {
		"" => return fail!("GITHUB_OUTPUT is not set, so the outputs cannot be written")
		path => Path.utf8(path)
	}
	work = match env!("RUNNER_TEMP")? {
		"" => return fail!("RUNNER_TEMP is not set, so there is nowhere to verify the downloads")
		path => Env.create_temp_dir_in!(Path.utf8(path), "roc-automation-publish-release-")?
	}
	# A new, empty directory: nothing in it can be mistaken for a download.
	downloads = Path.join(work, "downloaded")
	Path.create_dir!(downloads)?
	listing = Path.join(work, "sha256sums.txt")
	Path.write_utf8!(listing, PublishRelease.digest_listing(made))?
	append!(output_file, "digests=${Path.display(listing)}\n")?

	url = match drive!(made, downloads, PublishRelease.publish(made)) {
		Ok(published) => published
		Err(reason) => return fail!(reason)
	}
	append!(output_file, "release-url=${url}\n")?
	match env!("GITHUB_STEP_SUMMARY")? {
		"" => {}
		path => append!(Path.utf8(path), PublishRelease.summary(made, url))?
	}
	Stdout.line!("Published ${url}")?
	Stdout.write!(PublishRelease.digest_listing(made))
}

check_admission! : Str, PublishRelease.Plan => Try({}, Str)
check_admission! = |path, made| {
	file = Path.utf8(path)
	if Path.type!(file).map_err(|_| "cannot inspect admission report")? != IsFile {
		return Err("admission report must be a regular file")
	}
	text = Str.from_utf8(BuildFiles.read!(file, 1048576)?).map_err(|_| "admission report must be UTF-8")?
	report = LifecycleAdmission.parse_report(text)?
	LifecycleAdmission.publication(report, made.repository, made.target, env!("GITHUB_RUN_ID").map_err(|_| "cannot read run identity")?, made.version, Sha256.hex(made.notes.to_utf8()), made.assets.map(|asset| { path: asset.name, size: asset.size, sha256: asset.sha256 }))
}

# Look at each thing the local checks ask about, in the order they ask.
gather! : PublishRelease.Collecting => Try(PublishRelease.Plan, Str)
gather! = |next|
	match next {
		Planned(made) => Ok(made)
		Rejected(reason) => Err(reason)
		Look(request, state) => gather!(PublishRelease.found(state, look!(request)))
	}

# The runner's disk, read and never written.
look! : PublishRelease.Local => PublishRelease.Found
look! = |request|
	match request {
		ReadNotes(path) =>
			match Path.type!(Path.utf8(path)) {
				Ok(IsFile) =>
					match Path.read_bytes!(Path.utf8(path)) {
						Ok(bytes) =>
							match Str.from_utf8(bytes) {
								Ok(text) => Notes(Text(text))
								Err(_) => Notes(NotText)
							}
						Err(_) => Unreadable("cannot read ${path}")
					}
				Ok(IsSymLink) => Notes(SymLink)
				Ok(_) => Notes(NotRegular)
				Err(PathErr(NotFound, _)) => Notes(Absent)
				Err(_) => Unreadable("cannot read ${path}")
			}
		ListDirectory(path) =>
			match Path.list!(Path.utf8(path)) {
				Ok(entries) => Names(entries.map(|entry| Path.display(entry).drop_prefix("${path.drop_suffix("/")}/")))
				Err(PathErr(NotFound, _)) => Names([])
				Err(PathErr(NotADirectory, _)) => Names([])
				Err(_) => Unreadable("cannot list ${path}")
			}
		Inspect(path) =>
			match Path.type!(Path.utf8(path)) {
				Ok(IsFile) =>
					match Path.size_in_bytes!(Path.utf8(path)) {
						Ok(size) => Kind(File(size))
						Err(_) => Unreadable("cannot read the size of ${path}")
					}
				Ok(IsSymLink) => Kind(SymLink)
				Ok(_) => Kind(Other)
				Err(PathErr(NotFound, _)) => Kind(Absent)
				Err(_) => Unreadable("cannot read ${path}")
			}
		Hash(path) =>
			match sha256sum!(path) {
				Ok(printed) => Digest(printed)
				Err(_) => Unreadable("sha256sum could not read ${path}")
			}
	}

# What `sha256sum` prints for one file.
sha256sum! : Str => Try(Str, [NotHashed])
sha256sum! = |path|
	match Cmd.new("sha256sum").args(["--", OsStr.from_str(path)]).stdout(Capture).stderr(Capture).run!() {
		Ok(output) if output.status == Exited(0) => Ok(Str.from_utf8_lossy(output.stdout_bytes))
		_ => Err(NotHashed)
	}

# Make each request the plan asks for, in the order it asks, until it ends.
drive! : PublishRelease.Plan, Path, PublishRelease.Publishing => Try(Str, Str)
drive! = |made, downloads, next|
	match next {
		Published(url) => Ok(url)
		Stopped(reason) => Err(reason)
		Ask(request, stage) => drive!(made, downloads, PublishRelease.answered(made, stage, ask!(made, downloads, request)))
	}

# The only place a request leaves this script. `gh` takes the token from
# `GH_TOKEN`, which it inherits; no argument or input carries it.
ask! : PublishRelease.Plan, Path, PublishRelease.Remote => PublishRelease.Reply
ask! = |made, downloads, request| {
	call = PublishRelease.call(made, request)
	allowed = PublishRelease.budget(request)
	ran = Cmd.new("gh")
		.args(GitHub.args(call).map(OsStr.from_str))
		.stdin(Bytes(GitHub.stdin(call)))
		.stdout(Capture)
		.stderr(Capture)
		.timeout_ms(allowed.timeout_ms)
		.output_limit(allowed.capture)
		.run!()
	match (request, ran) {
		(Download({ name, .. }), Ok(output)) if output.status == Exited(0) => keep!(Path.join(downloads, name), output.stdout_bytes)
		# More came back than the tested file holds. One byte too many is as wrong as any number.
		(Download({ limit, .. }), Err(OutputLimit(_))) => Downloaded({ size: limit + 1, digest: "" })
		(_, Ok(output)) =>
			PublishRelease.reply(
				made,
				request,
				{ succeeded: output.status == Exited(0), stdout: Str.from_utf8_lossy(output.stdout_bytes), stderr: Str.from_utf8_lossy(output.stderr_bytes) },
			)
		(_, Err(Timeout(_))) => Failed(GitHub.not_run(call, "no answer within ${(allowed.timeout_ms // 1000).to_str()} seconds"))
		(_, Err(OutputLimit(_))) => Failed(GitHub.not_run(call, "the answer was larger than expected"))
		(_, Err(_)) => Failed(GitHub.not_run(call, "gh could not be run"))
	}
}

# Write what was downloaded to a file that does not exist yet, and report the
# size and digest of that file, not of the bytes in memory.
keep! : Path, List(U8) => PublishRelease.Reply
keep! = |path, bytes| {
	shown = Path.display(path)
	match Path.type!(path) {
		Err(PathErr(NotFound, _)) => {}
		_ => return Failed("${shown} already exists, so the download was not written")
	}
	match Path.write_bytes!(path, bytes) {
		Ok({}) => {}
		Err(_) => return Failed("cannot write ${shown}")
	}
	match (Path.size_in_bytes!(path), sha256sum!(shown)) {
		(Ok(size), Ok(digest)) => Downloaded({ size, digest })
		_ => Failed("cannot read back ${shown}")
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
	Stderr.line!("::error::${GitHub.workflow_command_text(GitHub.single_line(reason))}")?
	Err(Exit(1))
}
