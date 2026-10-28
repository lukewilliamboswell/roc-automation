import GitHub

## Names, URLs and notes of what a Roc package release publishes. This module
## has no effects, so every rule here is covered by the `expect` tests below
## and shared by the release utility and its tests alike.
ReleasePackage := [].{

	## The repository a release belongs to: the prefix of its asset names and
	## the GitHub repository that publishes it, as `owner/name`.
	Package : { name : Str, repository : Str }

	## One bundle a release publishes. `root` is its package root, such as
	## `package/main.roc` or `tzdb/package/main.roc`; `infix` names it in file
	## names and notes, and is "" for the repository's own package.
	Bundle : { root : Str, infix : Str }

	## The version a pull request builds and tests its release under. It is
	## never published. Roc reads 0.0.0 in a package URL as "no version", so
	## the lowest usable version is 0.0.1.
	dry_run_version : Str
	dry_run_version = "0.0.1-dryrun"

	## The package root every repository has.
	primary_root : Str
	primary_root = "package/main.roc"

	## A package name usable in asset file names.
	valid_name : Str -> Bool
	valid_name = |name| GitHub.is_plain(name, "-_") and !name.starts_with("-") and GitHub.count(name) <= 100

	## Directory listings use native separators. Archive and scratch-copy paths
	## are relative forward-slash names on every host, including mixed input paths.
	relative_file : Str, Str, Bool -> Try(Str, [OutsideDirectory])
	relative_file = |directory, file, windows| {
		portable = |value| if windows value.replace_each("\\", "/") else value
		prefix = "${portable(directory).drop_suffix("/")}/"
		name = portable(file)
		if name.starts_with(prefix) Ok(name.drop_prefix(prefix)) else Err(OutsideDirectory)
	}

	## `x.y.z`, optionally with a `-prerelease` suffix of letters, digits,
	## dots and hyphens.
	valid_version : Str -> Bool
	valid_version = |value|
		match Str.split_first(value, "-") {
			Ok({ before, after }) => final_version(before) and GitHub.is_plain(after, ".-")
			Err(_) => final_version(value)
		}

	## Whether `version` can appear in a package URL.
	publishable_version : Str -> Bool
	publishable_version = |version| valid_version(version) and version != "0.0.0" and !version.starts_with("0.0.0-")

	## A final release, exactly `x.y.z`. Only final releases have API
	## documentation on the site and are "the latest release".
	final_version : Str -> Bool
	final_version = |value|
		match Str.split_on(value, ".") {
			[major, minor, patch] => [major, minor, patch].all(GitHub.is_digits)
			_ => Bool.False
		}

	## The `x.y.z` that `version` is a release or a prerelease of.
	base_version : Str -> Str
	base_version = |version|
		match Str.split_first(version, "-") {
			Ok({ before, .. }) => before
			Err(_) => version
		}

	## Where the authored notes of the release `version` live.
	notes_file : Str -> Str
	notes_file = |version| "docs/releases/${version}.adoc"

	## The repository-relative file an application at `app_path` names with
	## the header source `declared`, when that source is a path inside the
	## repository rather than a URL. `examples/greet/main.roc` declaring
	## `../../package/main.roc` resolves to `package/main.roc`.
	resolve_local : Str, Str -> Try(Str, [NotLocal])
	resolve_local = |app_path, declared| {
		if declared.contains("://") or declared.starts_with("/") or !declared.ends_with(".roc") {
			return Err(NotLocal)
		}
		directory = match Str.split_last(app_path, "/") {
			Ok({ before, .. }) => Str.split_on(before, "/")
			Err(_) => []
		}
		resolved = Str.split_on(declared, "/").fold(
			Ok(directory),
			|state, part|
				match state {
					Ok(parts) =>
						match part {
							".." => match parts {
								[.., _] => Ok(parts.drop_last(1))
								[] => Err(NotLocal)
							}
							"." | "" => Ok(parts)
							_ => Ok(parts.append(part))
						}
					Err(reason) => Err(reason)
				},
		)?
		Ok(Str.join_with(resolved, "/"))
	}

	## The bundle whose package root is `root`. The root is `main.roc` in a
	## directory; the directory `package` is the repository's own package, and
	## any other directory, with a trailing `/package` dropped, names the bundle
	## in file names and notes, so `tzdb/package/main.roc` is `tzdb`.
	bundle_for_root : Str -> Try(Bundle, [NotABundleRoot(Str)])
	bundle_for_root = |root| {
		directory = root.drop_suffix("/main.roc")
		if !root.ends_with("/main.roc") or directory == "" or !GitHub.is_plain(directory, "-_/") or directory.contains("//") {
			return Err(NotABundleRoot(root))
		}
		infix = if directory == "package" "" else Str.join_with(Str.split_on(directory.drop_suffix("/package"), "/"), "-")
		if infix != "" and (!GitHub.is_plain(infix, "-_") or infix.starts_with("-")) {
			return Err(NotABundleRoot(root))
		}
		Ok({ root, infix })
	}

	## The placeholder a bundle's URL has in notes: `{package-url}` for the
	## repository's own package, `{<infix>-url}` for another.
	placeholder : Bundle -> Str
	placeholder = |bundle| if bundle.infix == "" "{package-url}" else "{${bundle.infix}-url}"

	## The file name a bundle is published under. `roc bundle` writes
	## `<hash>.tar.zst`; a release with one bundle keeps that name. With several,
	## Roc would read two bare hashes under one version as one package, so each
	## file name carries the repository name and the bundle's infix.
	bundle_file : Package, Bundle, Str, Bool -> Str
	bundle_file = |pkg, bundle, created, single|
		if single and bundle.infix == "" created else if bundle.infix == "" "${pkg.name}-${created}" else "${pkg.name}-${bundle.infix}-${created}"

	## Whether `value` is a bundle file name: a content hash with `.tar.zst`,
	## with an optional `<name>-` or `<name>-<infix>-` prefix.
	valid_bundle_file : Str -> Bool
	valid_bundle_file = |value| value.ends_with(".tar.zst") and GitHub.is_plain(value.drop_suffix(".tar.zst"), "-_") and !value.starts_with("-")

	## Among a release's asset names, the file of `bundle`: the one whose
	## prefix before its hash is this bundle's, where a bare hash is the
	## repository's own package.
	published_bundle_file : Package, Bundle, List(Str) -> [One(Str), None, Several]
	published_bundle_file = |pkg, bundle, assets| {
		wanted = if bundle.infix == "" ["", pkg.name] else ["${pkg.name}-${bundle.infix}"]
		matching = assets.keep_if(
			|asset| valid_bundle_file(asset) and match Str.split_last(asset.drop_suffix(".tar.zst"), "-") {
				Ok({ before, .. }) => wanted.contains(before)
				Err(_) => wanted.contains("")
			},
		)
		match matching {
			[one] => One(one)
			[] => None
			_ => Several
		}
	}

	## The immutable URL of a bundle attached to the release `version`.
	bundle_url : Package, Str, Str -> Try(Str, [InvalidVersion(Str), InvalidBundleFile(Str)])
	bundle_url = |pkg, version, file|
		if !publishable_version(version) {
			Err(InvalidVersion(version))
		} else if !valid_bundle_file(file) {
			Err(InvalidBundleFile(file))
		} else {
			Ok("https://github.com/${pkg.repository}/releases/download/${version}/${file}")
		}

	## The single top-level directory of the examples archive.
	examples_root : Package, Str -> Str
	examples_root = |pkg, version| "${pkg.name}-examples-${version}"

	## The examples archive attached to the release `version`.
	examples_archive : Package, Str -> Str
	examples_archive = |pkg, version| "${examples_root(pkg, version)}.zip"

	## The API documentation archive attached to the release `version`. Its
	## entries live under a `version/` directory, so a site restores it in place.
	docs_archive : Package, Str -> Str
	docs_archive = |pkg, version| "${pkg.name}-docs-${version}.zip"

	## Whether `entry` may appear in the examples archive whose top-level
	## directory is `root`: the README, the expected-output specification, or a
	## file under `examples/` whose every path component is a plain name, so
	## that the archive unpacks the same way on every system.
	valid_examples_entry : Str, Str -> Bool
	valid_examples_entry = |root, entry|
		entry == "${root}/README.md" or entry == "${root}/spec.json" or (entry.starts_with("${root}/examples/") and safe_components(Str.split_on(entry.drop_prefix("${root}/examples/"), "/")))

	## The README at the top of the examples archive.
	examples_readme : Package, Str, List(Str) -> Str
	examples_readme = |pkg, version, urls|
		Str.join_with(
			[
				"# ${pkg.name} ${version} examples",
				"",
				"These are the ${pkg.name} examples as they were when ${version} was released.",
				"Each application depends on that release's ${if urls.len() == 1 "package" else "packages"}:",
				"",
			]
				.concat(urls.map(|url| "    ${url}"))
				.concat([
					"",
					"Install the Roc compiler named in the release notes, then run an example",
					"from the `examples` directory:",
					"",
					"    cd examples",
					"    roc <example>.roc",
					"",
					"`spec.json` records what each example printed when ${version} was released.",
					"",
					"Release notes: https://github.com/${pkg.repository}/releases/tag/${version}",
					"",
				]),
			"\n",
		)

	## The first line the authored notes of `version` must have.
	notes_title : Package, Str -> Str
	notes_title = |pkg, version| "= ${pkg.name} ${version}"

	## Render authored AsciiDoc notes as the Markdown body of a GitHub release,
	## with each bundle's placeholder replaced by its URL. The notes must open
	## with `title`, use every placeholder, and leave no other `{...-url}`
	## unresolved. Only what release notes use is converted: headings, source
	## blocks and links. Paragraphs, lists and `code` read the same in both.
	render_notes : Str, { title : Str, urls : List({ placeholder : Str, url : Str }) } -> Try(Str, [WrongTitle(Str), UnusedPlaceholder(Str), UnresolvedPlaceholder(Str), UnclosedBlock])
	render_notes = |source, { title, urls }|
		match Str.split_on(source, "\n") {
			[first, .. as body] if first == title => {
				for binding in urls {
					if !source.contains(binding.placeholder) {
						return Err(UnusedPlaceholder(binding.placeholder))
					}
				}
				replaced = body.map(|line| urls.fold(line, |text, binding| Str.replace_each(text, binding.placeholder, binding.url)))
				lines = convert(replaced, Prose, [])?
				match lines.keep_oks(unresolved) {
					[name, ..] => Err(UnresolvedPlaceholder(name))
					[] => Ok("${Str.join_with(lines, "\n").trim()}\n")
				}
			}
			[first, ..] => Err(WrongTitle(first))
			[] => Err(WrongTitle(""))
		}

	## The closing section of the notes: what the release was built and tested
	## with. No header pins the package compiler, so this is where a release
	## states it. `automation` names the shared workflow revision, or is "".
	built_with : { compiler : Str, automation : Str } -> Str
	built_with = |record|
		Str.join_with(
			[
				"## Built with",
				"",
				"- Package and examples built and tested with `roc` ${record.compiler}",
			]
				.concat(if record.automation == "" [] else ["- Released by the shared roc-automation workflow at `${record.automation}`"])
				.append(""),
			"\n",
		)

	## Order `x.y.z` versions from newest to oldest, comparing numbers.
	newest_first : List(Str) -> List(Str)
	newest_first = |versions|
		List.sort_with(versions.map(|version| (numbers(version), version)), |(left, _), (right, _)| compare_numbers(right, left))
			.map(|(_, version)| version)
}

# A path component the archive helper and every filesystem accept: plain
# characters, not `.` or `..`, not ending in a dot, not a Windows device name.
safe_components = |parts|
	!parts.is_empty() and parts.all(
		|part| {
			stem = Str.join_with(Str.split_on(part, ".").take_first(1), "")
			upper = Str.from_utf8_lossy(Str.to_utf8(stem).map(|byte| if byte >= 'a' and byte <= 'z' byte - 32 else byte))
			GitHub.is_plain(part, "-._") and part != "." and part != ".." and !part.ends_with(".") and !["CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"].contains(upper)
		},
	)

convert : List(Str), [Prose, Block], List(Str) -> Try(List(Str), [UnclosedBlock])
convert = |lines, state, out|
	match (state, lines) {
		(Prose, []) => Ok(out)
		(Block, []) => Err(UnclosedBlock)
		(Block, ["----", .. as rest]) => convert(rest, Prose, out.append("```"))
		(Block, [line, .. as rest]) => convert(rest, Block, out.append(line))
		(Prose, [declared, "----", .. as rest]) if declared.starts_with("[source") => convert(rest, Block, out.append("```${language(declared)}"))
		(Prose, ["----", .. as rest]) => convert(rest, Block, out.append("```"))
		(Prose, [line, .. as rest]) => convert(rest, Prose, out.append(links(heading(line))))
	}

# `[source,roc]` names its language second.
language = |declared|
	match Str.split_on(declared.drop_prefix("[").drop_suffix("]"), ",") {
		[_, name, ..] => name.trim()
		_ => ""
	}

# Release notes are a chapter of the manual, so their sections start at `==`.
heading = |line|
	if line.starts_with("=== ") {
		"### ${line.drop_prefix("=== ")}"
	} else if line.starts_with("== ") {
		"## ${line.drop_prefix("== ")}"
	} else {
		line
	}

# AsciiDoc writes a link as `https://example.com[text]`.
links = |line|
	match Str.split_first(line, "https://") {
		Ok({ before, after }) =>
			match Str.split_first(after, "[") {
				Ok({ before: address, after: rest }) if !address.contains(" ") =>
					match Str.split_first(rest, "]") {
						Ok({ before: text, after: remainder }) => "${before}[${text}](https://${address})${links(remainder)}"
						Err(_) => line
					}
				_ => "${before}https://${links(after)}"
			}
		Err(_) => line
	}

# The name of a `{...-url}` placeholder still in `line`.
unresolved = |line|
	match Str.split_first(line, "-url}") {
		Ok({ before, .. }) =>
			match Str.split_last(before, "{") {
				Ok({ after, .. }) if !after.contains(" ") => Ok("{${after}-url}")
				_ => Err(Resolved)
			}
		Err(_) => Err(Resolved)
	}

numbers = |version| Str.split_on(version, ".").map(|part| U64.from_str(part).ok_or(0))

compare_numbers = |left, right|
	match (left, right) {
		([a, .. as left_rest], [b, .. as right_rest]) =>
			if a < b Before else if a > b After else compare_numbers(left_rest, right_rest)
		([], []) => Same
		([], _) => Before
		(_, []) => After
	}

pkg : ReleasePackage.Package
pkg = { name: "roc-ansi", repository: "lukewilliamboswell/roc-ansi" }

main_bundle : ReleasePackage.Bundle
main_bundle = { root: "package/main.roc", infix: "" }

tzdb : ReleasePackage.Bundle
tzdb = { root: "tzdb/package/main.roc", infix: "tzdb" }

url = "https://github.com/lukewilliamboswell/roc-ansi/releases/download/0.14.0/Abc123.tar.zst"

expect ReleasePackage.valid_name("roc-ansi") and ReleasePackage.valid_name("basic_cli") and !ReleasePackage.valid_name("-x") and !ReleasePackage.valid_name("a/b") and !ReleasePackage.valid_name("")
expect ReleasePackage.valid_version("0.14.0") and ReleasePackage.valid_version("1.0.0-rc.1") and ReleasePackage.valid_version(ReleasePackage.dry_run_version)
expect !ReleasePackage.valid_version("0.14") and !ReleasePackage.valid_version("v0.14.0") and !ReleasePackage.valid_version("0.14.0-") and !ReleasePackage.valid_version("0.14.0/x") and !ReleasePackage.valid_version("latest")
expect ReleasePackage.publishable_version("0.14.0") and ReleasePackage.publishable_version(ReleasePackage.dry_run_version) and !ReleasePackage.publishable_version("0.0.0") and !ReleasePackage.publishable_version("0.0.0-rc1")
expect ReleasePackage.final_version("0.14.0") and !ReleasePackage.final_version("1.0.0-rc.1") and !ReleasePackage.final_version(ReleasePackage.dry_run_version)
expect ReleasePackage.base_version("0.14.0") == "0.14.0" and ReleasePackage.base_version("1.0.0-rc.1") == "1.0.0"
expect ReleasePackage.notes_file("0.14.0") == "docs/releases/0.14.0.adoc"

expect ReleasePackage.resolve_local("examples/colors.roc", "../package/main.roc") == Ok("package/main.roc")
expect ReleasePackage.resolve_local("examples/greet/main.roc", "../../package/main.roc") == Ok("package/main.roc")
expect ReleasePackage.resolve_local("examples/greet/main.roc", "../../tzdb/package/main.roc") == Ok("tzdb/package/main.roc")
expect ReleasePackage.resolve_local("examples/greet/main.roc", "./Names.roc") == Ok("examples/greet/Names.roc")
expect ReleasePackage.resolve_local("examples/colors.roc", "../../package/main.roc") == Err(NotLocal)
expect ReleasePackage.resolve_local("examples/colors.roc", url) == Err(NotLocal)
expect ReleasePackage.resolve_local("examples/colors.roc", "/package/main.roc") == Err(NotLocal)
expect ReleasePackage.bundle_for_root("package/main.roc") == Ok(main_bundle)
expect ReleasePackage.bundle_for_root("tzdb/package/main.roc") == Ok(tzdb)
expect ReleasePackage.bundle_for_root("extras/main.roc") == Ok({ root: "extras/main.roc", infix: "extras" })
expect ReleasePackage.bundle_for_root("examples/greet/Names.roc").is_err() and ReleasePackage.bundle_for_root("main.roc").is_err() and ReleasePackage.bundle_for_root("a//package/main.roc").is_err()
expect ReleasePackage.placeholder(main_bundle) == "{package-url}" and ReleasePackage.placeholder(tzdb) == "{tzdb-url}"
expect ReleasePackage.bundle_file(pkg, main_bundle, "Abc123.tar.zst", Bool.True) == "Abc123.tar.zst"
expect ReleasePackage.bundle_file(pkg, main_bundle, "Abc123.tar.zst", Bool.False) == "roc-ansi-Abc123.tar.zst"
expect ReleasePackage.bundle_file(pkg, tzdb, "Xyz.tar.zst", Bool.False) == "roc-ansi-tzdb-Xyz.tar.zst"
expect ReleasePackage.valid_bundle_file("Abc123.tar.zst") and ReleasePackage.valid_bundle_file("roc-ansi-tzdb-Abc123.tar.zst") and !ReleasePackage.valid_bundle_file("../Abc123.tar.zst") and !ReleasePackage.valid_bundle_file(".tar.zst") and !ReleasePackage.valid_bundle_file("Abc123.tar.gz")
expect ReleasePackage.published_bundle_file(pkg, main_bundle, ["Abc123.tar.zst", "roc-ansi-examples-0.14.0.zip"]) == One("Abc123.tar.zst")
expect ReleasePackage.published_bundle_file(pkg, main_bundle, ["roc-ansi-Abc.tar.zst", "roc-ansi-tzdb-Xyz.tar.zst"]) == One("roc-ansi-Abc.tar.zst")
expect ReleasePackage.published_bundle_file(pkg, tzdb, ["roc-ansi-Abc.tar.zst", "roc-ansi-tzdb-Xyz.tar.zst"]) == One("roc-ansi-tzdb-Xyz.tar.zst")
expect ReleasePackage.published_bundle_file(pkg, tzdb, ["roc-ansi-Abc.tar.zst"]) == None
expect ReleasePackage.published_bundle_file(pkg, main_bundle, ["Abc.tar.zst", "Def.tar.zst"]) == Several
expect ReleasePackage.bundle_url(pkg, "0.14.0", "Abc123.tar.zst") == Ok(url)
expect ReleasePackage.bundle_url(pkg, "latest", "Abc123.tar.zst") == Err(InvalidVersion("latest"))
expect ReleasePackage.bundle_url(pkg, "0.0.0", "Abc123.tar.zst") == Err(InvalidVersion("0.0.0"))
expect ReleasePackage.bundle_url(pkg, "0.14.0", "a/b.tar.zst") == Err(InvalidBundleFile("a/b.tar.zst"))
expect ReleasePackage.examples_root(pkg, "0.14.0") == "roc-ansi-examples-0.14.0" and ReleasePackage.examples_archive(pkg, "0.14.0") == "roc-ansi-examples-0.14.0.zip"
expect ReleasePackage.docs_archive(pkg, "0.14.0") == "roc-ansi-docs-0.14.0.zip"
expect ReleasePackage.valid_examples_entry("roc-ansi-examples-0.14.0", "roc-ansi-examples-0.14.0/README.md")
expect ReleasePackage.valid_examples_entry("roc-ansi-examples-0.14.0", "roc-ansi-examples-0.14.0/spec.json") and ReleasePackage.valid_examples_entry("roc-ansi-examples-0.14.0", "roc-ansi-examples-0.14.0/examples/spec.json")
expect ReleasePackage.valid_examples_entry("roc-ansi-examples-0.14.0", "roc-ansi-examples-0.14.0/examples/menu.roc")
expect ReleasePackage.valid_examples_entry("roc-ansi-examples-0.14.0", "roc-ansi-examples-0.14.0/examples/greet/main.roc")
expect ReleasePackage.valid_examples_entry("roc-ansi-examples-0.14.0", "roc-ansi-examples-0.14.0/examples/greet/data/names.txt")
expect !ReleasePackage.valid_examples_entry("roc-ansi-examples-0.14.0", "roc-ansi-examples-0.14.0/examples/../../escape.roc")
expect !ReleasePackage.valid_examples_entry("roc-ansi-examples-0.14.0", "roc-ansi-examples-0.14.0/examples/")
expect !ReleasePackage.valid_examples_entry("roc-ansi-examples-0.14.0", "roc-ansi-examples-0.14.0/examples/greet/CON.txt")
expect !ReleasePackage.valid_examples_entry("roc-ansi-examples-0.14.0", "roc-ansi-examples-0.14.0/examples/trailing.")
expect !ReleasePackage.valid_examples_entry("roc-ansi-examples-0.14.0", "roc-ansi-examples-0.13.0/examples/colors.roc")
expect !ReleasePackage.valid_examples_entry("roc-ansi-examples-0.14.0", "/etc/passwd")
expect !ReleasePackage.valid_examples_entry("roc-ansi-examples-0.14.0", "roc-ansi-examples-0.14.0/examples/a b.roc")
expect ReleasePackage.examples_readme(pkg, "0.14.0", [url]).contains("release's package:\n\n    ${url}\n") and ReleasePackage.examples_readme(pkg, "0.14.0", [url]).ends_with("/releases/tag/0.14.0\n")
expect ReleasePackage.examples_readme(pkg, "0.14.0", [url, "https://example.com/z.tar.zst"]).contains("packages:\n\n    ${url}\n    https://example.com/z.tar.zst\n")
expect ReleasePackage.notes_title(pkg, "0.14.0") == "= roc-ansi 0.14.0"
expect ReleasePackage.newest_first(["0.9.0", "0.13.1", "0.10.0", "0.13.0"]) == ["0.13.1", "0.13.0", "0.10.0", "0.9.0"]

notes = "= roc-ansi 0.14.0\n\nAdds streaming.\n\n== Use this release\n\n[source,roc]\n----\napp [main!] {\n\tansi: \"{package-url}\",\n}\n----\n\n=== Thanks\n\n* See https://example.com/a[the issue] and https://example.com/b[the fix].\n* Plain https://example.com stays.\n"

release = { title: "= roc-ansi 0.14.0", urls: [{ placeholder: "{package-url}", url: "https://example.com/Abc.tar.zst" }] }

expect ReleasePackage.render_notes(notes, release) == Ok("Adds streaming.\n\n## Use this release\n\n```roc\napp [main!] {\n\tansi: \"https://example.com/Abc.tar.zst\",\n}\n```\n\n### Thanks\n\n* See [the issue](https://example.com/a) and [the fix](https://example.com/b).\n* Plain https://example.com stays.\n")
expect ReleasePackage.render_notes(notes, { ..release, title: "= roc-ansi 0.15.0" }) == Err(WrongTitle("= roc-ansi 0.14.0"))
expect ReleasePackage.render_notes("= roc-ansi 0.14.0\n\nNothing to install.\n", release) == Err(UnusedPlaceholder("{package-url}"))
expect ReleasePackage.render_notes("= roc-ansi 0.14.0\n\n{package-url} and {tzdb-url}\n", release) == Err(UnresolvedPlaceholder("{tzdb-url}"))
expect ReleasePackage.render_notes("= roc-ansi 0.14.0\n\n{package-url} and {tzdb-url}\n", { ..release, urls: release.urls.append({ placeholder: "{tzdb-url}", url: "https://example.com/Z.tar.zst" }) }) == Ok("https://example.com/Abc.tar.zst and https://example.com/Z.tar.zst\n")
expect ReleasePackage.render_notes("= roc-ansi 0.14.0\n\n{package-url}\n", { ..release, urls: release.urls.append({ placeholder: "{tzdb-url}", url: "https://example.com/Z.tar.zst" }) }) == Err(UnusedPlaceholder("{tzdb-url}"))
expect ReleasePackage.render_notes("= roc-ansi 0.14.0\n\n{package-url}\n\n----\nnever closed\n", release) == Err(UnclosedBlock)
expect ReleasePackage.render_notes("= roc-ansi 0.14.0\n\n----\n{package-url}\n----\n", release) == Ok("```\nhttps://example.com/Abc.tar.zst\n```\n")
expect ReleasePackage.render_notes("", release) == Err(WrongTitle(""))
expect ReleasePackage.built_with({ compiler: "nightly-2", automation: "" }) == "## Built with\n\n- Package and examples built and tested with `roc` nightly-2\n"
expect ReleasePackage.built_with({ compiler: "nightly-2", automation: "lukewilliamboswell/roc-automation@abc" }).contains("workflow at `lukewilliamboswell/roc-automation@abc`\n")

expect ReleasePackage.relative_file("package", "package/main.roc", Bool.False) == Ok("main.roc")
expect ReleasePackage.relative_file("package", "package\\main.roc", Bool.True) == Ok("main.roc")
expect ReleasePackage.relative_file("examples/nested", "examples\\nested\\data\\seed", Bool.True) == Ok("data/seed")
expect ReleasePackage.relative_file("D:\\suite/package", "D:\\suite\\package\\main.roc", Bool.True) == Ok("main.roc")
expect ReleasePackage.relative_file("package", "other/main.roc", Bool.True) == Err(OutsideDirectory)
expect ReleasePackage.relative_file("package", "package\\main.roc", Bool.False) == Err(OutsideDirectory)
