#!/usr/bin/env roc
app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
}

import cli.Cmd
import cli.Env
import cli.OsStr
import cli.Path
import cli.Stderr
import cli.Stdout
import src/AppHeader
import src/Compatibility
import src/Examples
import src/GitHub
import src/ReleasePackage

## Build and test what a pure Roc package release publishes. The shared release
## workflow runs this utility; a maintainer runs the same commands locally.
##
##     release-package build VERSION          the bundle, examples archive, API docs archive and notes, into dist/
##     release-package check-version VERSION  roc bump against the latest release's bundle
##     release-package test-examples          the working tree's examples against tests/spec.json
##     release-package test-examples --update rewrite tests/spec.json from what the examples print
##     release-package test-archive           the examples archive in dist/, unchanged, against the bundle in dist/
##
## `roc bundle`, `roc docs`, `roc bump`, `roc check` and `roc build` do the work;
## this utility fixes the order, the names and the checks between them. Unlike
## the other scripts here it names no compiler: the shared workflow builds it
## with the same nightly that tests the package, so it must compile on the
## latest nightly as well as on `roc-stable`. The package compiler is the `roc`
## on PATH or `RP_COMPILER`. `RP_REPOSITORY` names
## the GitHub repository as `owner/name`; it defaults to `GITHUB_REPOSITORY` or
## the `origin` remote. `RP_NAME` is the package name in asset names and defaults
## to the repository name. `RP_DIRECTORY` keeps the command logs; `RP_AUTOMATION`
## is recorded in the notes as the shared workflow revision.
main! = |args| {
	result = match args.map(OsStr.display) {
		["build", version] => build!(version)
		["check-version", version] => check_version!(version)
		["test-examples"] => test_examples!(Compare)
		["test-examples", "--update"] => test_examples!(Update)
		["test-archive"] => test_archive!()
		_ => Err("usage: release-package build VERSION | check-version VERSION | test-examples [--update] | test-archive")
	}
	match result {
		Ok({}) => Ok({})
		Err(reason) => {
			Stderr.line!("release-package: ${Compatibility.console(reason)}")?
			Err(Exit(1))
		}
	}
}

# The paths every command agrees on. A package lives at package/main.roc, its
# examples directly under examples/, their expectations in tests/spec.json and
# its authored notes under docs/releases/.
package_root = Path.utf8("package")

examples_root = Path.utf8("examples")

spec_path = Path.utf8("tests/spec.json")

dependency = "../package/main.roc"

Settings : { pkg : ReleasePackage.Package, compiler : Str, output : Path, directory : Path, automation : Str }

settings! : () => Try(Settings, Str)
settings! = || {
	repository = match optional!("RP_REPOSITORY", optional!("GITHUB_REPOSITORY", "")?)? {
		"" => origin_repository!()?
		value => value
	}
	if !GitHub.valid_repository(repository) {
		return Err("RP_REPOSITORY must be owner/name, got ${repository}")
	}
	name = optional!("RP_NAME", Str.split_on(repository, "/").last() ?? "")?
	if !ReleasePackage.valid_name(name) {
		return Err("RP_NAME must be a package name usable in file names, got ${name}")
	}
	directory = match optional!("RP_DIRECTORY", "")? {
		"" => Env.create_temp_dir_in!(Path.utf8(optional!("RUNNER_TEMP", optional!("TMPDIR", "/tmp")?)?), "release-package-").map_err(|_| "cannot create a directory for command logs")?
		given => {
			given_path = Path.utf8(given)
			mkdir!(given_path)?
			given_path
		}
	}
	Ok({ pkg: { name, repository }, compiler: optional!("RP_COMPILER", "roc")?, output: Path.utf8(optional!("RP_OUTPUT", "dist")?), directory, automation: optional!("RP_AUTOMATION", "")? })
}

optional! = |name, fallback| {
	match Env.var_str!(OsStr.from_str(name)) {
		Ok("") => Ok(fallback)
		Ok(value) => Ok(value)
		Err(VarNotFound(_)) => Ok(fallback)
		Err(_) => Err("${name} is not UTF-8")
	}
}

# `owner/name` from the origin remote, for a maintainer's own checkout.
origin_repository! = || {
	remote = capture!(["git", "config", "--get", "remote.origin.url"], Path.utf8("."), "cannot read the origin remote; set RP_REPOSITORY")?.trim().drop_suffix(".git")
	match Str.split_on(remote.replace_each(":", "/"), "/") {
		[.., owner, name] if GitHub.valid_repository("${owner}/${name}") => Ok("${owner}/${name}")
		_ => Err("the origin remote does not name a GitHub repository; set RP_REPOSITORY")
	}
}

## The next thing GitHub Actions reads from this utility, when it is running there.
output! = |pairs| {
	match optional!("GITHUB_OUTPUT", "")? {
		"" => Ok({})
		file => {
			path = Path.utf8(file)
			before = Path.read_utf8!(path) ?? ""
			write!(path, "${before}${Str.join_with(pairs.map(|(key, value)| "${key}=${value}"), "\n")}\n")
		}
	}
}

read! = |path| Path.read_utf8!(path).map_err(|_| "cannot read ${Path.display(path)}")

write! = |path, text| Path.write_utf8!(path, text).map_err(|_| "cannot write ${Path.display(path)}")

mkdir! = |path| Path.create_all!(path).map_err(|_| "cannot create ${Path.display(path)}")

say! = |text| Stdout.line!(text).map_err(|_| "cannot write to stdout")

file_name = |path| Path.filename(path).map_ok(Path.display).map_err(|_| "${Path.display(path)} has no file name")

## Run a program with captured output, returning its standard output as text.
## Its standard error is shown only when it fails.
capture! = |argv, cwd, problem| {
	match argv {
		[program, .. as arguments] => {
			ran = Cmd.new(OsStr.from_str(program)).args(arguments.map(OsStr.from_str)).cwd(cwd).stdout(Capture).stderr(Capture).timeout_ms(600_000).output_limit(50_000_000).manage_tree(Bool.True).run!().map_err(|_| "${problem}: ${program} did not complete")?
			if ran.status != Exited(0) {
				Stderr.write_bytes!(ran.stderr_bytes).map_err(|_| "cannot write to stderr")?
				return Err("${problem}: ${Str.join_with(argv, " ")} failed")
			}
			Str.from_utf8(ran.stdout_bytes).map_err(|_| "${problem}: ${program} printed invalid UTF-8")
		}
		[] => Err("empty command")
	}
}

## Run a program with inherited output, printing it first.
run! = |argv, cwd, problem| {
	say!("+ ${Str.join_with(argv, " ")}")?
	match argv {
		[program, .. as arguments] => Cmd.new(OsStr.from_str(program)).args(arguments.map(OsStr.from_str)).cwd(cwd).exec_cmd!().map_err(|_| "${problem}: ${Str.join_with(argv, " ")} failed")
		[] => Err("empty command")
	}
}

## The `.roc` files directly inside `directory`, by name, in byte order.
direct_roc_files! = |directory| {
	entries = Path.list!(directory).map_err(|_| "cannot list ${Path.display(directory)}")?
	names = entries.map_try(file_name)?
	Ok(GitHub.sort(names.keep_if(|name| name.ends_with(".roc"))))
}

## Every regular file beneath `directory`, relative to it, in byte order. A
## symbolic link or special file is an error, so nothing outside the tree is
## ever read or archived.
all_files! = |directory| {
	prefix = "${Path.display(directory)}/"
	found = walk!(Path.list!(directory).map_err(|_| "cannot list ${Path.display(directory)}")?, [])?
	Ok(GitHub.sort(found.map(|path| Path.display(path).drop_prefix(prefix))))
}

walk! = |entries, found|
	match entries {
		[path, .. as rest] =>
			match Path.type!(path).map_err(|_| "cannot inspect ${Path.display(path)}")? {
				IsDir => walk!(rest, walk!(Path.list!(path).map_err(|_| "cannot list ${Path.display(path)}")?, found)?)
				IsFile => walk!(rest, found.append(path))
				_ => Err("${Path.display(path)} is not a regular file or directory")
			}
		[] => Ok(found)
	}

## A fresh directory under the command log directory, where staged files can
## be inspected after a failure.
temp! = |settings, label| Env.create_temp_dir_in!(settings.directory, "${label}-").map_err(|_| "cannot create a temporary directory under ${Path.display(settings.directory)}")

## The one file in `directory` that `matches`, by name.
only! = |directory, matches, what| {
	entries = if Path.is_dir!(directory) ?? Bool.False Path.list!(directory).map_err(|_| "cannot list ${Path.display(directory)}")? else []
	names = entries.map_try(file_name)?
	match names.keep_if(matches) {
		[one] => Ok(Path.join(directory, one))
		[] => Err("no ${what} in ${Path.display(directory)}")
		_ => Err("several ${what}s in ${Path.display(directory)}; start from an empty directory")
	}
}

## Add `entries`, relative to `stage`, to the ZIP `archive`, with fixed times
## and modes so that packing the same files twice gives the same bytes.
zip! = |stage, entries, archive| {
	if Path.exists!(archive) ?? Bool.False {
		return Err("${Path.display(archive)} exists already; start from an empty output directory")
	}
	staged = entries.map(|entry| Path.display(Path.join(stage, entry)))
	_ = capture!(["chmod", "0644"].concat(staged), Path.utf8("."), "cannot fix archive modes")?
	_ = capture!(["env", "TZ=UTC", "touch", "-t", "198001010000"].concat(staged), Path.utf8("."), "cannot fix archive times")?
	# -X leaves out owner and timestamp extras, -D directory entries; -@ reads
	# the entries to add from standard input, which Cmd supplies as bytes.
	ran = Cmd.new("env").args(["TZ=UTC", "zip", "-q", "-X", "-D", Path.display(archive), "-@"].map(OsStr.from_str)).cwd(stage).stdin(Bytes(Str.to_utf8(Str.join_with(entries, "\n")))).stdout(Capture).stderr(Capture).timeout_ms(600_000).output_limit(10_000_000).run!().map_err(|_| "zip did not complete")?
	if ran.status != Exited(0) {
		Stderr.write_bytes!(ran.stderr_bytes).map_err(|_| "cannot write to stderr")?
		return Err("zip failed for ${Path.display(archive)}")
	}
	Ok({})
}

## The entry names of a ZIP, without unpacking anything.
zip_entries! = |archive| {
	# -Z1 lists entry names, one per line.
	listing = capture!(["unzip", "-Z1", Path.display(archive)], Path.utf8("."), "cannot list ${Path.display(archive)}")?
	Ok(Str.split_on(listing, "\n").map(|line| line.drop_suffix("\r")).keep_if(|line| line != ""))
}

## The working tree's suite, which must describe exactly the examples present.
working_suite! = || {
	suite = Compatibility.working_tree_suite(read!(spec_path)?)?
	present = direct_roc_files!(examples_root)?.map(|name| "examples/${name}")
	listed = suite.apps.map(|example| example.path)
	match (present.keep_if(|path| !listed.contains(path)), listed.keep_if(|path| !present.contains(path))) {
		([], []) => Ok(suite)
		(missing, extra) => Err("${Path.display(spec_path)} does not describe the examples present: unlisted ${Str.inspect(missing)}, absent ${Str.inspect(extra)}")
	}
}

## The compiler's release, as `roc version` prints it.
compiler_version! = |compiler| Ok(capture!([compiler, "version"], Path.utf8("."), "cannot run the package compiler")?.trim().drop_prefix("Roc compiler version "))

## Fail unless the suite ran completely and every case passed. Skips are
## declared in the suite and never pass silently, exactly as in the nightly.
verdict! = |ran, what| {
	for failure in ran.failures {
		say!("FAIL ${Compatibility.console(failure)}")?
	}
	say!("\n${what}: ${ran.tested.to_str()} tested, ${ran.failed.to_str()} failed, ${ran.skipped.to_str()} skipped")?
	match ran.interrupted {
		Timeout(number) => Err("command ${number.to_str()} timed out; inspect its logs")
		OutputLimit(number) => Err("command ${number.to_str()} exceeded the output limit; inspect its logs")
		None =>
			match Compatibility.classify(ran.tested, ran.failed, ran.skipped) {
				"compatible" => Ok({})
				"incompatible" => Err("${ran.failed.to_str()} steps failed")
				_ => Err("no case ran, or a case was skipped; a skip is declared so it is never a silent pass")
			}
	}
}

build! = |version| {
	settings = settings!()?
	pkg = settings.pkg
	if !ReleasePackage.publishable_version(version) {
		return Err("${version} is not a version that can be released")
	}
	dry_run = version == ReleasePackage.dry_run_version
	compiler = settings.compiler
	output = settings.output
	mkdir!(output)?
	absolute_output = Path.absolute!(output).map_err(|_| "cannot resolve ${Path.display(output)}")?
	existing = direct_names!(output)?
	if existing.any(|name| name.ends_with(".tar.zst") or name.ends_with(".zip") or name == "release-notes.md") {
		return Err("${Path.display(output)} already holds release files; start from an empty directory")
	}

	# The bundle: roc bundle takes the entry point first, then every other module.
	say!("\n== Bundle")?
	modules = direct_roc_files!(package_root)?
	if !modules.contains("main.roc") {
		return Err("package/main.roc does not exist")
	}
	report = capture!([compiler, "bundle", "main.roc"].concat(modules.keep_if(|name| name != "main.roc")).concat(["--output-dir", Path.display(absolute_output)]), package_root, "roc bundle failed")?
	created = match Str.split_on(report, "\n").keep_oks(|line| if line.starts_with("Created: ") Ok(line.drop_prefix("Created: ").trim()) else Err(NotCreated)) {
		[one] => file_name(Path.utf8(one))?
		_ => return Err("could not find the bundle path in the output of roc bundle:\n${report}")
	}
	bundle = Path.join(output, created)
	if !(Path.is_file!(bundle) ?? Bool.False) or !ReleasePackage.valid_bundle_file(created) {
		return Err("roc bundle did not create ${Path.display(bundle)}")
	}
	# What the bundle holds is exactly the package's modules, nothing else.
	members = GitHub.sort(Str.split_on(capture!(["tar", "-tf", Path.display(bundle)], Path.utf8("."), "cannot list the bundle")?, "\n").keep_if(|line| line != ""))
	if members != modules {
		return Err("the bundle holds ${Str.inspect(members)}, not the package modules ${Str.inspect(modules)}")
	}
	url = ReleasePackage.bundle_url(pkg, version, created).map_err(|_| "cannot form the bundle URL")?
	say!("BUNDLE ${Path.display(bundle)} with ${modules.len().to_str()} modules\nURL    ${url}")?

	# The examples archive: the working tree's examples, depending on that URL.
	say!("\n== Examples archive")?
	suite = working_suite!()?
	root = ReleasePackage.examples_root(pkg, version)
	examples_archive = Path.join(absolute_output, ReleasePackage.examples_archive(pkg, version))
	staging = temp!(settings, "examples")?
	stage = Path.join(staging, root)
	mkdir!(Path.join(stage, "examples"))?
	var $entries = ["${root}/README.md", "${root}/spec.json"]
	for example in suite.apps {
		source = read!(Path.utf8(example.path))?
		shorthand = match AppHeader.shorthand_of(source, dependency) {
			Ok(name) => name
			Err(_) => return Err("${example.path} must depend on the package as ${dependency}, exactly once")
		}
		if AppHeader.has_roc_pin(source) {
			return Err("${example.path} must not pin a compiler; the release notes record the compiler")
		}
		archived = AppHeader.with_dependency(source, shorthand, dependency, url).map_err(|_| "${example.path} must be formatted by roc fmt so that its dependency can be replaced")?
		if !Compatibility.app_source(archived, [url]) {
			return Err("${example.path} does not depend on the release bundle after replacement")
		}
		write!(Path.join(stage, example.path), archived)?
		$entries = $entries.append("${root}/${example.path}")
	}
	write!(Path.join(stage, "README.md"), ReleasePackage.examples_readme(pkg, version, url))?
	write!(Path.join(stage, "spec.json"), Compatibility.published(suite, version, [url]))?
	zip!(staging, $entries, examples_archive)?
	say!("ARCHIVE ${Path.display(examples_archive)} with ${suite.apps.len().to_str()} examples")?

	# The API documentation archive, under a directory named after the version.
	say!("\n== API documentation archive")?
	docs_archive = Path.join(absolute_output, ReleasePackage.docs_archive(pkg, version))
	docs_staging = temp!(settings, "docs")?
	generated = Path.join(docs_staging, version)
	run!([compiler, "docs", "package/main.roc", "--output=${Path.display(generated)}"], Path.utf8("."), "roc docs failed")?
	if !(Path.is_file!(Path.join(generated, "index.html")) ?? Bool.False) {
		return Err("roc docs wrote no index.html")
	}
	files = all_files!(generated)?
	zip!(docs_staging, files.map(|file| "${version}/${file}"), docs_archive)?
	say!("ARCHIVE ${Path.display(docs_archive)}")?

	# The notes: authored for this version, or the newest ones on a dry run.
	say!("\n== Release notes")?
	noted = if dry_run newest_notes!()? else version
	notes_path = Path.utf8(ReleasePackage.notes_file(noted))
	if !(Path.is_file!(notes_path) ?? Bool.False) {
		return Err("${Path.display(notes_path)} does not exist: write the notes for ${version} and merge them before releasing it")
	}
	body = match ReleasePackage.render_notes(read!(notes_path)?, { title: ReleasePackage.notes_title(pkg, noted), package_url: url }) {
		Ok(rendered) => rendered
		Err(WrongTitle(found)) => return Err("${Path.display(notes_path)} must open with \"${ReleasePackage.notes_title(pkg, noted)}\", not \"${found}\"")
		Err(UnusedPlaceholder) => return Err("${Path.display(notes_path)} never uses ${ReleasePackage.placeholder}, so it does not say how to install the release")
		Err(UnresolvedPlaceholder(name)) => return Err("${Path.display(notes_path)} uses ${name}, which this release has no URL for")
		Err(UnclosedBlock) => return Err("${Path.display(notes_path)} opens a ---- block that it never closes")
	}
	notes = Path.join(output, "release-notes.md")
	write!(notes, "${body}\n${ReleasePackage.built_with({ compiler: compiler_version!(compiler)?, automation: settings.automation })}")?
	say!("NOTES ${Path.display(notes)} from ${Path.display(notes_path)}")?
	output!([("bundle", Path.display(bundle)), ("url", url), ("examples", Path.display(Path.join(output, ReleasePackage.examples_archive(pkg, version)))), ("docs", Path.display(Path.join(output, ReleasePackage.docs_archive(pkg, version)))), ("notes", Path.display(notes))])
}

direct_names! = |directory| {
	entries = Path.list!(directory).map_err(|_| "cannot list ${Path.display(directory)}")?
	entries.map_try(file_name)
}

# The newest version that has authored notes, for a dry run to render.
newest_notes! = || {
	names = direct_names!(Path.utf8("docs/releases"))?
	versions = names.keep_if(|name| name.ends_with(".adoc")).map(|name| name.drop_suffix(".adoc")).keep_if(ReleasePackage.valid_version)
	match ReleasePackage.newest_first(versions) {
		[newest, ..] => Ok(newest)
		[] => Err("docs/releases holds no release notes")
	}
}

## `roc bump` compares the package with the latest release's bundle and fails
## when the version understates the change, so a breaking change cannot go out
## as a patch. A dry run has no version to hold to that: it reports the bump
## the change needs, and a comparison that cannot be made is not an error.
check_version! = |version| {
	settings = settings!()?
	pkg = settings.pkg
	if !ReleasePackage.publishable_version(version) {
		return Err("${version} is not a version that can be released")
	}
	dry_run = version == ReleasePackage.dry_run_version
	if !dry_run and !(Path.is_file!(Path.utf8(ReleasePackage.notes_file(version))) ?? Bool.False) {
		return Err("${ReleasePackage.notes_file(version)} does not exist: write the notes for ${version} and merge them before releasing it")
	}
	# Without a tag, gh selects the latest published release that is not a
	# draft or a prerelease. A repository without one is making its first release.
	listing = Cmd.new("gh").args(["release", "view", "--repo", pkg.repository, "--json", "tagName,assets", "--jq", "{tag: .tagName, assets: [.assets[].name]}"].map(OsStr.from_str)).stdout(Capture).stderr(Capture).timeout_ms(60_000).output_limit(2_000_000).run!().map_err(|_| "gh did not complete")?
	if listing.status != Exited(0) {
		if Str.from_utf8_lossy(listing.stderr_bytes).contains("release not found") {
			return say!("NOTE ${pkg.repository} has no published release yet, so ${version} is its first; roc bump has nothing to compare with")
		}
		Stderr.write_bytes!(listing.stderr_bytes).map_err(|_| "cannot write to stderr")?
		return Err("cannot read the latest release of ${pkg.repository}")
	}
	latest : Try({ tag : Str, assets : List(Str) }, _)
	latest = Json.parse(Str.from_utf8_lossy(listing.stdout_bytes))
	found = latest.map_err(|_| "unreadable release listing")?
	previous = match found.assets.keep_if(ReleasePackage.valid_bundle_file) {
		[bundle_file] => ReleasePackage.bundle_url(pkg, found.tag, bundle_file).map_err(|_| "release ${found.tag} is not a version roc bump can compare with")?
		_ => return Err("release ${found.tag} does not have exactly one bundle attached")
	}
	compare = [settings.compiler, "bump", "--old", previous]
	if dry_run {
		match run!(compare.append("package/main.roc"), Path.utf8("."), "roc bump") {
			Ok({}) => Ok({})
			Err(_) => say!("NOTE roc bump could not compare with ${found.tag}; a release would stop here")
		}
	} else {
		run!(compare.concat(["--expect", ReleasePackage.base_version(version), "package/main.roc"]), Path.utf8("."), "the version understates the change since ${found.tag}, or roc bump could not compare")
	}
}

## The working tree's examples, against the package by path, compared with
## or recorded into tests/spec.json.
test_examples! = |mode| {
	settings = settings!()?
	suite = working_suite!()?
	# The maintainer's own package cache is fine here: the dependency is a path.
	cache = Path.utf8(optional!("XDG_CACHE_HOME", "${optional!("HOME", "/tmp")?}/.cache")?)
	ran = Examples.execute!({ compiler: settings.compiler, root: Path.utf8("."), suite, dependencies: [dependency], setup: { directory: settings.directory, cache, environment: [], environment_text: "[]" }, case_timeout_ms: 10_000, source: Direct, mode, first_number: 1 })?
	match mode {
		Update => {
			rendered = Compatibility.render(ran.updated)
			if read!(spec_path)? == rendered {
				say!("UNCHANGED ${Path.display(spec_path)}")?
			} else {
				write!(spec_path, rendered)?
				say!("UPDATED ${Path.display(spec_path)}")?
			}
		}
		Compare => {}
	}
	verdict!(ran, "Examples against the working tree")
}

## The archive in dist/, exactly as it would be published, against the bundle
## in dist/, served from a loopback address in place of its release URL.
test_archive! = || {
	settings = settings!()?
	pkg = settings.pkg
	bundle = only!(settings.output, |name| name.ends_with(".tar.zst"), "bundle")?
	archive = only!(settings.output, |name| name.starts_with("${pkg.name}-examples-") and name.ends_with(".zip"), "examples archive")?
	archive_name = file_name(archive)?
	root = archive_name.drop_suffix(".zip")
	version = root.drop_prefix("${pkg.name}-examples-")
	if !ReleasePackage.publishable_version(version) or ReleasePackage.examples_archive(pkg, version) != archive_name {
		return Err("${archive_name} is not an examples archive of ${pkg.name}")
	}
	entries = zip_entries!(archive)?
	match entries.keep_if(|entry| !ReleasePackage.valid_examples_entry(root, entry)) {
		[] => {}
		[entry, ..] => return Err("unexpected entry in the examples archive: ${entry}")
	}
	if !entries.contains("${root}/spec.json") {
		return Err("${archive_name} holds no spec.json, so there is nothing to compare its examples with")
	}
	url = ReleasePackage.bundle_url(pkg, version, file_name(bundle)?).map_err(|_| "${Path.display(bundle)} is not a release bundle")?
	temporary = temp!(settings, "archive")?
	unpacked = Path.join(temporary, "unpacked")
	mkdir!(unpacked)?
	_ = capture!(["unzip", "-q", Path.display(archive), "-d", Path.display(unpacked)], Path.utf8("."), "cannot unpack ${archive_name}")?
	suite_root = Path.join(unpacked, root)
	suite = Compatibility.published_suite(read!(Path.join(suite_root, "spec.json"))?, pkg.repository, version)?
	if suite.dependencies != [url] {
		return Err("the archived examples depend on ${Str.inspect(suite.dependencies)}, not on ${Path.display(bundle)} at ${url}")
	}
	# An empty package cache makes Roc fetch the bundle from the loopback
	# server, as it would fetch the published bundle for a first user.
	cache = Path.join(temporary, "cache")
	mkdir!(cache)?
	say!("SERVE ${Path.display(bundle)} in place of ${url}")?
	ran = Examples.execute!({ compiler: settings.compiler, root: suite_root, suite, dependencies: [url], setup: { directory: settings.directory, cache, environment: [], environment_text: "[]" }, case_timeout_ms: 10_000, source: Served({ url, bundle }), mode: Compare, first_number: 1 })?
	verdict!(ran, "Archived examples against the bundle")
}
