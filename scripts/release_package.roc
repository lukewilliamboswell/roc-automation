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
import src/AppHeader
import src/Compatibility
import src/Examples
import src/GitHub
import src/ReleasePackage

## Build and test what a Roc package or platform release publishes. The shared
## release workflow runs this utility; a maintainer runs the same commands locally.
##
##     release-package build VERSION          the bundles, examples archive, API docs archive and notes, into dist/
##     release-package build VERSION --platform-bundle DIR
##                                            the same, with the platform bundle the repository built into DIR
##     release-package check-version VERSION  roc bump against the latest release's bundles
##     release-package test-examples          the working tree's examples against tests/spec.json
##     release-package test-examples --update record what the examples print; writes tests/spec.json if it is missing
##     release-package test-archive           the examples archive in dist/, unchanged, against the bundles in dist/
##
## `roc bundle`, `roc docs`, `roc bump`, `roc check` and `roc build` do the work;
## this utility fixes the order, the names and the checks between them. A
## platform's bundle holds host outputs that only its repository knows how to
## build, so the repository's own bundle command writes it and this utility
## checks it, names it and releases it like any other bundle. It is
## built by `roc-stable` and published as a static executable in each tools
## release; a repository's Blueprint fetches it by digest and runs it as the
## `release-package` task, locally and in the shared workflow alike. The package
## compiler is a different one: the `roc` on PATH or `RP_COMPILER`, the latest
## nightly. `RP_REPOSITORY` names the GitHub repository as `owner/name`; it
## defaults to `GITHUB_REPOSITORY` or the `origin` remote. `RP_NAME` is the
## name in asset file names and defaults to the repository name. `RP_DIRECTORY`
## keeps the command logs; `RP_AUTOMATION` is recorded in the notes as the
## shared workflow revision.
main! = |args| {
	result = match args.map(OsStr.display) {
		["build", version] => build!(version, Err(Missing))
		["build", version, "--platform-bundle", directory] => build!(version, Ok(directory))
		["check-version", version] => check_version!(version)
		["test-examples"] => test_examples!(Compare)
		["test-examples", "--update"] => test_examples!(Update)
		["test-archive"] => test_archive!()
		_ => Err("usage: release-package build VERSION [--platform-bundle DIR] | check-version VERSION | test-examples [--update] | test-archive")
	}
	match result {
		Ok({}) => Ok({})
		Err(reason) => {
			Stderr.line!("release-package: ${Compatibility.console(reason)}")?
			Err(Exit(1))
		}
	}
}

# The paths every command agrees on. The repository's package lives at
# package/main.roc, or its platform at platform/main.roc; another bundle it
# releases lives at <dir>/package/main.roc.
# Examples live under examples/, as `<name>.roc` files or `<name>/main.roc`
# directories with their sibling modules and data files, with their
# expectations in tests/spec.json. Authored notes live under docs/releases/.
examples_root = Path.utf8("examples")

spec_path = Path.utf8("tests/spec.json")

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
			lines = pairs.map(
				|(key, value)|
					if value.contains("\n") "${key}<<RELEASE_PACKAGE_EOF\n${value}\nRELEASE_PACKAGE_EOF" else "${key}=${value}",
			)
			write!(path, "${before}${Str.join_with(lines, "\n")}\n")
		}
	}
}

read! = |path| Path.read_utf8!(path).map_err(|_| "cannot read ${Path.display(path)}")

write! = |path, text| Path.write_utf8!(path, text).map_err(|_| "cannot write ${Path.display(path)}")

mkdir! = |path| Path.create_all!(path).map_err(|_| "cannot create ${Path.display(path)}")

say! = |text| Stdout.line!(text).map_err(|_| "cannot write to stdout")

file_name = |path| Path.filename(path).map_ok(Path.display).map_err(|_| "${Path.display(path)} has no file name")

parent = |path|
	match Str.split_last(path, "/") {
		Ok({ before, .. }) => before
		Err(_) => "."
	}

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

## Every regular file beneath `directory`, relative to it, in byte order. A
## symbolic link or special file is an error, so nothing outside the tree is
## ever read or archived.
all_files! = |directory| {
	found = walk!(Path.list!(directory).map_err(|_| "cannot list ${Path.display(directory)}")?, [])?
	windows = match Env.platform!().os {
		WINDOWS => Bool.True
		_ => Bool.False
	}
	relative = found.map_try(|path| ReleasePackage.relative_file(Path.display(directory), Path.display(path), windows).map_err(|_| "${Path.display(path)} is outside ${Path.display(directory)}"))?
	Ok(GitHub.sort(relative))
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

## The regular files Git tracks under `directory`, by path. Build outputs are
## not tracked, so they are never bundled or archived; a symbolic link is refused.
tracked! = |directory| {
	listing = capture!(["git", "ls-files", "--", directory], Path.utf8("."), "cannot list the tracked files under ${directory}")?
	paths = GitHub.sort(Str.split_on(listing, "\n").keep_if(|line| line != ""))
	for path in paths {
		match Path.type!(Path.utf8(path)).map_err(|_| "cannot inspect ${path}")? {
			IsFile => {}
			_ => return Err("${path} is tracked but is not a regular file")
		}
	}
	Ok(paths)
}

## A fresh directory under the command log directory, where staged files can
## be inspected after a failure.
temp! = |settings, label| Env.create_temp_dir_in!(settings.directory, "${label}-").map_err(|_| "cannot create a temporary directory under ${Path.display(settings.directory)}")

## The names of the entries directly inside `directory`.
direct_names! = |directory| {
	entries = if Path.is_dir!(directory) ?? Bool.False Path.list!(directory).map_err(|_| "cannot list ${Path.display(directory)}")? else []
	entries.map_try(file_name)
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

## What reads a ZIP here. Windows has no unzip, and the tar of Git Bash
## cannot read a ZIP, so there the system's own tar.exe does, by its full path.
zip_reader! = || {
	match Env.platform!().os {
		WINDOWS => Ok(SystemTar("${optional!("SystemRoot", "C:\\Windows")?}\\System32\\tar.exe"))
		_ => Ok(Unzip)
	}
}

## The entry names of a ZIP, without unpacking anything.
zip_entries! = |archive| {
	# -Z1 lists entry names, one per line.
	argv = match zip_reader!()? {
		SystemTar(tar) => [tar, "-tf", Path.display(archive)]
		Unzip => ["unzip", "-Z1", Path.display(archive)]
	}
	listing = capture!(argv, Path.utf8("."), "cannot list ${Path.display(archive)}")?
	Ok(Str.split_on(listing, "\n").map(|line| line.drop_suffix("\r")).keep_if(|line| line != ""))
}

## Unpack a ZIP into the existing directory `destination`.
unzip! = |archive, destination| {
	argv = match zip_reader!()? {
		SystemTar(tar) => [tar, "-xf", Path.display(archive), "-C", Path.display(destination)]
		Unzip => ["unzip", "-q", Path.display(archive), "-d", Path.display(destination)]
	}
	_ = capture!(argv, Path.utf8("."), "cannot unpack ${Path.display(archive)}")?
	Ok({})
}

## The one bundle in `directory`, by file name. A bundle command that writes
## none, or several, is not one this utility can name.
single_bundle! = |directory, what| {
	match direct_names!(directory)?.keep_if(|name| name.ends_with(".tar.zst")) {
		[one] => Ok(one)
		[] => Err("${what} holds no .tar.zst bundle")
		several => Err("${what} holds ${several.len().to_str()} bundles, ${Str.inspect(several)}; it must hold exactly one")
	}
}

## The member names of a bundle, in byte order.
bundle_members! = |bundle| Ok(GitHub.sort(Str.split_on(capture!(["tar", "-tf", Path.display(bundle)], Path.utf8("."), "cannot list ${Path.display(bundle)}")?, "\n").map(|line| line.drop_suffix("\r")).keep_if(|line| line != "")))

## The applications under examples/, by path: each `.roc` file directly inside
## it that has an `app` header, and the `main.roc` of each directory inside it.
## A `.roc` file without an `app` header is a module the examples share.
example_apps! = || {
	entries = Path.list!(examples_root).map_err(|_| "cannot list ${Path.display(examples_root)}")?
	var $apps = []
	for entry in entries {
		name = file_name(entry)?
		match Path.type!(entry).map_err(|_| "cannot inspect ${Path.display(entry)}")? {
			IsFile if name.ends_with(".roc") and AppHeader.is_app(read!(entry)?) => {
				$apps = $apps.append("examples/${name}")
			}
			IsDir if Path.is_file!(Path.join(entry, "main.roc")) ?? Bool.False => {
				$apps = $apps.append("examples/${name}/main.roc")
			}
			IsFile | IsDir => {}
			_ => return Err("${Path.display(entry)} is not a regular file or directory")
		}
	}
	Ok(GitHub.sort($apps))
}

## The working tree's suite, which must describe exactly the applications present.
working_suite! = || {
	suite = Compatibility.working_tree_suite(read!(spec_path)?)?
	present = example_apps!()?
	listed = suite.apps.map(|example| example.path)
	match (present.keep_if(|path| !listed.contains(path)), listed.keep_if(|path| !present.contains(path))) {
		([], []) => Ok(suite)
		(missing, extra) => Err("${Path.display(spec_path)} does not describe the applications present: unlisted ${Str.inspect(missing)}, absent ${Str.inspect(extra)}")
	}
}

## A suite for a repository that has none yet: every application with one
## case that runs it without arguments, for `--update` to record.
initial_suite! = || {
	apps = example_apps!()?
	if apps.is_empty() {
		return Err("${Path.display(examples_root)} holds no application")
	}
	suite : Compatibility.Suite
	suite = {
		schema_version: 1,
		release: Compatibility.working_tree,
		dependencies: [],
		apps: apps.map(|path| { path, target: Err(Missing), run: Err(Missing), build_args: Err(Missing), cases: [{ name: "default", args: [], stdin: "", stdout: "", stderr: "", stderr_match: Err(Missing), exit_code: 0, skip: Err(Missing) }] }),
	}
	Ok(suite)
}

## The header fields of the application at `path` that name a package or
## the platform of this repository by relative path, each with the bundle it
## resolves to. A platform this repository releases is the one at
## platform/main.roc, and only a `platform` field may name it.
local_dependencies! = |path| {
	source = read!(Path.utf8(path))?
	fields = AppHeader.fields(source).map_err(|_| "${path} has a header this utility cannot read")?
	if fields.any(|field| field.name == "roc") {
		return Err("${path} must not pin a compiler; the release notes record the compiler")
	}
	var $found = []
	for field in fields {
		match ReleasePackage.resolve_local(path, field.source) {
			Ok(resolved) if field.is_platform and resolved != ReleasePackage.platform_root => return Err("${path} names the platform ${field.source}, which resolves to ${resolved}; a platform this repository releases lives at ${ReleasePackage.platform_root}")
			Ok(resolved) if !field.is_platform and resolved == ReleasePackage.platform_root => return Err("${path} depends on ${field.source} as a package; name the repository's platform with `${field.name}: platform \"${field.source}\"`")
			Ok(resolved) =>
				match ReleasePackage.bundle_for_root(resolved) {
					Ok(bundle) => {
						$found = $found.append({ name: field.name, declared: field.source, bundle })
					}
					Err(_) => return Err("${path} depends on ${field.source}, which is not a package root such as package/main.roc")
				}
			Err(NotLocal) => {}
		}
	}
	if $found.is_empty() {
		return Err("${path} must depend on the repository's package or platform by relative path, such as ../package/main.roc or ../../platform/main.roc")
	}
	Ok({ source, dependencies: $found })
}

## The bundles the examples depend on, the repository's own platform or
## package first and every other one in root order. What no example exercises
## is not released.
bundles! = |suite| {
	var $bundles = []
	for example in suite.apps {
		found = local_dependencies!(example.path)?
		for dependency in found.dependencies {
			if !$bundles.contains(dependency.bundle) {
				$bundles = $bundles.append(dependency.bundle)
			}
		}
	}
	match ReleasePackage.release_bundles($bundles) {
		Ok(ordered) => Ok(ordered)
		Err(NoPrimary(root)) => Err("no example depends on ${root}, so it would be released untested")
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

build! = |version, platform_bundle| {
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
	suite = working_suite!()?
	bundles = bundles!(suite)?
	single = bundles.len() == 1
	match (bundles.any(|bundle| bundle.kind == Platform), platform_bundle) {
		(Bool.True, Err(Missing)) => return Err("the examples depend on ${ReleasePackage.platform_root}, whose bundle this repository builds: run its bundle command with --output-dir DIR, then build ${version} --platform-bundle DIR")
		(Bool.False, Ok(_)) => return Err("--platform-bundle is given, but no example depends on ${ReleasePackage.platform_root}")
		_ => {}
	}

	# The bundles. roc bundle takes a package root first, then every other
	# tracked file in its directory, modules and data alike, and what it
	# writes must hold exactly those. The repository's bundle command writes
	# the platform's, which must hold at least every tracked module beside its
	# host outputs. Each comes from a directory holding only it.
	say!("\n== ${if single "Bundle" else "Bundles"}")?
	built = bundles.map_try!(
		|bundle| {
			directory = parent(bundle.root)
			tracked_files = tracked!(directory)?
			if !tracked_files.contains(bundle.root) {
				return Err("${bundle.root} is not tracked by Git")
			}
			made = match (bundle.kind, platform_bundle) {
				(Platform, Ok(given)) => {
					from = Path.utf8(given)
					created = single_bundle!(from, "--platform-bundle ${given}")?
					members = bundle_members!(Path.join(from, created))?
					match ReleasePackage.missing_platform_members(tracked_files, members) {
						[] => {}
						missing => return Err("the platform bundle ${created} does not hold the tracked modules ${Str.inspect(missing)}")
					}
					{ from, created, count: members.len() }
				}
				_ => {
					files = tracked_files.keep_if(|path| parent(path) == directory).map(|path| path.drop_prefix("${directory}/"))
					fresh = Path.absolute!(temp!(settings, "bundle")?).map_err(|_| "cannot resolve a temporary directory")?
					_ = capture!([compiler, "bundle", "main.roc"].concat(files.keep_if(|name| name != "main.roc")).concat(["--output-dir", Path.display(fresh)]), Path.utf8(directory), "roc bundle failed for ${bundle.root}")?
					created = single_bundle!(fresh, "the output directory of roc bundle")?
					members = bundle_members!(Path.join(fresh, created))?
					if members != files {
						return Err("the bundle of ${bundle.root} holds ${Str.inspect(members)}, not the tracked files ${Str.inspect(files)}")
					}
					{ from: fresh, created, count: files.len() }
				}
			}
			name = ReleasePackage.bundle_file(pkg, bundle, made.created, single)
			Path.copy!(Path.join(made.from, made.created), Path.join(output, name)).map_err(|_| "cannot copy ${made.created} to ${Path.display(Path.join(output, name))}")?
			url = ReleasePackage.bundle_url(pkg, version, name).map_err(|_| "cannot form the bundle URL for ${name}")?
			say!("BUNDLE ${Path.display(Path.join(output, name))} from ${bundle.root} with ${made.count.to_str()} files\nURL    ${url}")?
			Ok({ bundle, file: Path.display(Path.join(output, name)), url })
		},
	)?
	url_of = |bundle| (built.keep_if(|b| b.bundle.root == bundle.root).first().map_ok(|b| b.url)) ?? ""
	urls = built.map(|b| b.url)
	primary_root = (bundles.first().map_ok(|bundle| bundle.root)) ?? ReleasePackage.package_root

	# The examples archive: every tracked file under examples/, with each
	# application's dependencies on this repository's packages replaced by
	# their release URLs, and a dependency on its platform by the platform's.
	# Sibling modules and data files are copied as they are.
	say!("\n== Examples archive")?
	root = ReleasePackage.examples_root(pkg, version)
	examples_archive = Path.join(absolute_output, ReleasePackage.examples_archive(pkg, version))
	staging = temp!(settings, "examples")?
	stage = Path.join(staging, root)
	apps = suite.apps.map(|example| example.path)
	tracked_examples = tracked!("examples")?
	for application in apps {
		if !tracked_examples.contains(application) {
			return Err("${application} is not tracked by Git, so it cannot be released")
		}
	}
	var $entries = ["${root}/README.md", "${root}/spec.json"]
	for path in tracked_examples {
		destination = Path.join(stage, path)
		mkdir!(Path.join(stage, parent(path)))?
		if apps.contains(path) {
			found = local_dependencies!(path)?
			archived = found.dependencies.fold(
				Ok(found.source),
				|state, dependency|
					match state {
						Ok(text) => AppHeader.with_dependency(text, dependency.name, dependency.declared, url_of(dependency.bundle)).map_err(|_| "${path} must be formatted by roc fmt so that its dependency ${dependency.name} can be replaced")
						Err(reason) => Err(reason)
					},
			)?
			if !Compatibility.app_source(archived, urls) {
				return Err("${path} does not depend on a release bundle after replacement")
			}
			write!(destination, archived)?
		} else {
			Path.copy!(Path.utf8(path), destination).map_err(|_| "cannot copy ${path}")?
		}
		$entries = $entries.append("${root}/${path}")
	}
	write!(Path.join(stage, "README.md"), ReleasePackage.examples_readme(pkg, version, urls))?
	write!(Path.join(stage, "spec.json"), Compatibility.published(suite, version, urls))?
	zip!(staging, $entries, examples_archive)?
	say!("ARCHIVE ${Path.display(examples_archive)} with ${apps.len().to_str()} applications in ${tracked_examples.len().to_str()} files")?

	# The API documentation archive, under a directory named after the version.
	say!("\n== API documentation archive")?
	docs_archive = Path.join(absolute_output, ReleasePackage.docs_archive(pkg, version))
	docs_staging = temp!(settings, "docs")?
	generated = Path.join(docs_staging, version)
	run!([compiler, "docs", primary_root, "--output=${Path.display(generated)}"], Path.utf8("."), "roc docs failed")?
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
	placeholders = built.map(|b| { placeholder: ReleasePackage.placeholder(b.bundle), url: b.url })
	body = match ReleasePackage.render_notes(read!(notes_path)?, { title: ReleasePackage.notes_title(pkg, noted), urls: placeholders }) {
		Ok(rendered) => rendered
		Err(WrongTitle(found)) => return Err("${Path.display(notes_path)} must open with \"${ReleasePackage.notes_title(pkg, noted)}\", not \"${found}\"")
		Err(UnusedPlaceholder(name)) => return Err("${Path.display(notes_path)} never uses ${name}, so it does not say how to install that package")
		Err(UnresolvedPlaceholder(name)) => return Err("${Path.display(notes_path)} uses ${name}, which this release has no URL for")
		Err(UnclosedBlock) => return Err("${Path.display(notes_path)} opens a ---- block that it never closes")
	}
	notes = Path.join(output, "release-notes.md")
	write!(notes, "${body}\n${ReleasePackage.built_with({ compiler: compiler_version!(compiler)?, automation: settings.automation })}")?
	say!("NOTES ${Path.display(notes)} from ${Path.display(notes_path)}")?
	primary = built.first().map_ok(|b| { file: b.file, url: b.url }) ?? { file: "", url: "" }
	output!([
		("bundle", primary.file),
		("url", primary.url),
		("bundles", Str.join_with(built.map(|b| b.file), "\n")),
		("urls", Str.join_with(urls, "\n")),
		("examples", Path.display(Path.join(output, ReleasePackage.examples_archive(pkg, version)))),
		("docs", Path.display(Path.join(output, ReleasePackage.docs_archive(pkg, version)))),
		("notes", Path.display(notes)),
	])
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

## `roc bump` compares each package with the latest release's bundle of it and
## fails when the version understates the change, so a breaking change cannot
## go out as a patch. A dry run has no version to hold to that: it reports the
## bump the change needs, and a comparison that cannot be made is not an error.
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
	for bundle in bundles!(working_suite!()?)? {
		match ReleasePackage.published_bundle_file(pkg, bundle, found.assets) {
			None => say!("NOTE release ${found.tag} has no bundle of ${bundle.root}, so ${version} is its first")?
			Several => return Err("release ${found.tag} has several bundles that could be ${bundle.root}")
			One(file) => {
				previous = ReleasePackage.bundle_url(pkg, found.tag, file).map_err(|_| "release ${found.tag} is not a version roc bump can compare with")?
				compare = [settings.compiler, "bump", "--old", previous]
				if dry_run {
					match run!(compare.append(bundle.root), Path.utf8("."), "roc bump") {
						Ok({}) => {}
						Err(_) => say!("NOTE roc bump could not compare ${bundle.root} with ${found.tag}; a release would stop here")?
					}
				} else {
					run!(compare.concat(["--expect", ReleasePackage.base_version(version), bundle.root]), Path.utf8("."), "the version understates the change to ${bundle.root} since ${found.tag}, or roc bump could not compare")?
				}
			}
		}
	}
	Ok({})
}

## The working tree's examples, against the packages by path, compared with
## or recorded into tests/spec.json. Recording with no specification yet
## starts from one default case per application.
test_examples! = |mode| {
	settings = settings!()?
	suite = match (mode, Path.is_file!(spec_path) ?? Bool.False) {
		(Update, Bool.False) => {
			say!("NOTE ${Path.display(spec_path)} does not exist; recording one default case per application")?
			initial_suite!()?
		}
		_ => working_suite!()?
	}
	# The maintainer's own package cache is fine here: the dependencies are paths.
	cache = Path.utf8(optional!("XDG_CACHE_HOME", "${optional!("HOME", "/tmp")?}/.cache")?)
	# The examples and the packages they depend on run in a scratch copy with
	# the repository's layout, so the relative dependencies resolve as written
	# and an example that writes files beside itself leaves the tree as it was.
	stage = temp!(settings, "working-tree")?
	found = suite.apps.map_try!(|example| Ok(local_dependencies!(example.path)?.dependencies))?
	declared = Compatibility.unique(found.join_map(|list| list.map(|dependency| dependency.declared)))
	package_directories = Compatibility.unique(found.join_map(|list| list.map(|dependency| parent(dependency.bundle.root))))
	for directory in package_directories.append(Path.display(examples_root)) {
		for file in all_files!(Path.utf8(directory))? {
			mkdir!(Path.join(stage, parent("${directory}/${file}")))?
			Path.copy!(Path.utf8("${directory}/${file}"), Path.join(stage, "${directory}/${file}")).map_err(|_| "cannot copy ${directory}/${file}")?
		}
	}
	ran = Examples.execute!({ compiler: settings.compiler, root: stage, suite, dependencies: declared, setup: { directory: settings.directory, cache, environment: [], environment_text: "[]" }, case_timeout_ms: 10_000, source: Direct, mode, first_number: 1 })?
	match mode {
		Update => {
			rendered = Compatibility.render(ran.updated)
			if (Path.read_utf8!(spec_path) ?? "") == rendered {
				say!("UNCHANGED ${Path.display(spec_path)}")?
			} else {
				mkdir!(Path.utf8(parent(Path.display(spec_path))))?
				write!(spec_path, rendered)?
				say!("UPDATED ${Path.display(spec_path)}")?
			}
		}
		Compare => {}
	}
	verdict!(ran, "Examples against the working tree")
}

## The archive in dist/, exactly as it would be published, against the bundles
## in dist/, served from a loopback address in place of their release URLs.
test_archive! = || {
	settings = settings!()?
	pkg = settings.pkg
	names = direct_names!(settings.output)?
	bundle_files = names.keep_if(|name| name.ends_with(".tar.zst"))
	if bundle_files.is_empty() {
		return Err("no bundle in ${Path.display(settings.output)}")
	}
	archive_name = match names.keep_if(|name| name.starts_with("${pkg.name}-examples-") and name.ends_with(".zip")) {
		[one] => one
		[] => return Err("no examples archive in ${Path.display(settings.output)}")
		_ => return Err("several examples archives in ${Path.display(settings.output)}; start from an empty directory")
	}
	archive = Path.join(settings.output, archive_name)
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
	for required in ["${root}/spec.json", "${root}/README.md"] {
		if !entries.contains(required) {
			return Err("${archive_name} holds no ${required.drop_prefix("${root}/")}")
		}
	}
	served = bundle_files.map_try(
		|file| {
			url = ReleasePackage.bundle_url(pkg, version, file).map_err(|_| "${file} is not a release bundle")?
			Ok({ url, bundle: Path.join(settings.output, file) })
		},
	)?
	temporary = temp!(settings, "archive")?
	unpacked = Path.join(temporary, "unpacked")
	mkdir!(unpacked)?
	unzip!(archive, unpacked)?
	suite_root = Path.join(unpacked, root)
	suite = Compatibility.published_suite(read!(Path.join(suite_root, "spec.json"))?, pkg.repository, version)?
	urls = served.map(|s| s.url)
	if GitHub.sort(suite.dependencies) != GitHub.sort(urls) {
		return Err("the archived examples depend on ${Str.inspect(suite.dependencies)}, not on the bundles in ${Path.display(settings.output)}: ${Str.inspect(urls)}")
	}
	for example in suite.apps {
		if !entries.contains("${root}/${example.path}") {
			return Err("${archive_name} holds no ${example.path}, which its specification names")
		}
	}
	# An empty package cache makes Roc fetch the bundles from the loopback
	# server, as it would fetch the published bundles for a first user.
	cache = Path.join(temporary, "cache")
	mkdir!(cache)?
	for s in served {
		say!("SERVE ${Path.display(s.bundle)} in place of ${s.url}")?
	}
	ran = Examples.execute!({ compiler: settings.compiler, root: suite_root, suite, dependencies: urls, setup: { directory: settings.directory, cache, environment: [], environment_text: "[]" }, case_timeout_ms: 10_000, source: Served(served), mode: Compare, first_number: 1 })?
	verdict!(ran, "Archived examples against the bundles")
}
