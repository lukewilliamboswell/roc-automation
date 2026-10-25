import GitHub

## Names, URLs and notes of what a pure Roc package release publishes. This
## module has no effects, so every rule here is covered by the `expect` tests
## below and shared by the release utility and its tests alike.
ReleasePackage := [].{

	## The package a release belongs to: the prefix of its asset names and the
	## GitHub repository that publishes it, as `owner/name`.
	Package : { name : Str, repository : Str }

	## The version a pull request builds and tests its release under. It is
	## never published. Roc reads 0.0.0 in a package URL as "no version", so
	## the lowest usable version is 0.0.1.
	dry_run_version : Str
	dry_run_version = "0.0.1-dryrun"

	## What stands for the release's bundle URL in authored notes and in the
	## manual, because the URL holds a hash nobody can write by hand.
	placeholder : Str
	placeholder = "{package-url}"

	## A package name usable in asset file names.
	valid_name : Str -> Bool
	valid_name = |name| GitHub.is_plain(name, "-_") and !name.starts_with("-") and GitHub.count(name) <= 100

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

	## Whether `value` is the file name `roc bundle` gives a package archive:
	## its content hash and the `.tar.zst` extension, nothing else.
	valid_bundle_file : Str -> Bool
	valid_bundle_file = |value| value.ends_with(".tar.zst") and GitHub.is_plain(value.drop_suffix(".tar.zst"), "")

	## The immutable URL of a bundle attached to the release `version`.
	bundle_url : Package, Str, Str -> Try(Str, [InvalidVersion(Str), InvalidBundleFile(Str)])
	bundle_url = |pkg, version, bundle_file|
		if !publishable_version(version) {
			Err(InvalidVersion(version))
		} else if !valid_bundle_file(bundle_file) {
			Err(InvalidBundleFile(bundle_file))
		} else {
			Ok("https://github.com/${pkg.repository}/releases/download/${version}/${bundle_file}")
		}

	## The release version a bundle URL belongs to, if it is one of this
	## package's immutable release bundle URLs.
	version_of_bundle_url : Package, Str -> Try(Str, [NotAReleaseBundle(Str)])
	version_of_bundle_url = |pkg, url|
		match Str.split_on(url.drop_prefix("https://github.com/${pkg.repository}/releases/download/"), "/") {
			[version, bundle_file] if bundle_url(pkg, version, bundle_file) == Ok(url) => Ok(version)
			_ => Err(NotAReleaseBundle(url))
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

	## The manual attached to the release `version`.
	pdf : Package, Str -> Str
	pdf = |pkg, version| "${pkg.name}-${version}.pdf"

	## Whether `entry` may appear in the examples archive whose top-level
	## directory is `root`: the README, the expected-output specification, or
	## a Roc file directly inside `examples`.
	valid_examples_entry : Str, Str -> Bool
	valid_examples_entry = |root, entry|
		entry == "${root}/README.md" or entry == "${root}/spec.json" or (entry.starts_with("${root}/examples/") and example_file(entry.drop_prefix("${root}/examples/")))

	## The README at the top of the examples archive.
	examples_readme : Package, Str, Str -> Str
	examples_readme = |pkg, version, url|
		Str.join_with(
			[
				"# ${pkg.name} ${version} examples",
				"",
				"These are the ${pkg.name} examples as they were when ${version} was released.",
				"Each application depends on that release's package:",
				"",
				"    ${url}",
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
			],
			"\n",
		)

	## The first line the authored notes of `version` must have.
	notes_title : Package, Str -> Str
	notes_title = |pkg, version| "= ${pkg.name} ${version}"

	## Render authored AsciiDoc notes as the Markdown body of a GitHub release,
	## with the placeholder replaced by `package_url`. The notes must open with
	## `title` and use the placeholder, and must leave no other `{...-url}`
	## unresolved. Only what release notes use is converted: headings, source
	## blocks and links. Paragraphs, lists and `code` read the same in both.
	render_notes : Str, { title : Str, package_url : Str } -> Try(Str, [WrongTitle(Str), UnusedPlaceholder, UnresolvedPlaceholder(Str), UnclosedBlock])
	render_notes = |source, { title, package_url }|
		match Str.split_on(source, "\n") {
			[first, .. as body] if first == title => {
				if !source.contains(placeholder) {
					return Err(UnusedPlaceholder)
				}
				lines = convert(body.map(|line| Str.replace_each(line, placeholder, package_url)), Prose, [])?
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

example_file = |file_name| file_name.ends_with(".roc") and GitHub.is_plain(file_name.drop_suffix(".roc"), "-_")

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

url = "https://github.com/lukewilliamboswell/roc-ansi/releases/download/0.14.0/Abc123.tar.zst"

expect ReleasePackage.valid_name("roc-ansi") and ReleasePackage.valid_name("basic_cli") and !ReleasePackage.valid_name("-x") and !ReleasePackage.valid_name("a/b") and !ReleasePackage.valid_name("")
expect ReleasePackage.valid_version("0.14.0") and ReleasePackage.valid_version("1.0.0-rc.1") and ReleasePackage.valid_version(ReleasePackage.dry_run_version)
expect !ReleasePackage.valid_version("0.14") and !ReleasePackage.valid_version("v0.14.0") and !ReleasePackage.valid_version("0.14.0-") and !ReleasePackage.valid_version("0.14.0/x") and !ReleasePackage.valid_version("latest")
expect ReleasePackage.publishable_version("0.14.0") and ReleasePackage.publishable_version(ReleasePackage.dry_run_version) and !ReleasePackage.publishable_version("0.0.0") and !ReleasePackage.publishable_version("0.0.0-rc1")
expect ReleasePackage.final_version("0.14.0") and !ReleasePackage.final_version("1.0.0-rc.1") and !ReleasePackage.final_version(ReleasePackage.dry_run_version)
expect ReleasePackage.base_version("0.14.0") == "0.14.0" and ReleasePackage.base_version("1.0.0-rc.1") == "1.0.0"
expect ReleasePackage.notes_file("0.14.0") == "docs/releases/0.14.0.adoc"
expect ReleasePackage.valid_bundle_file("Abc123.tar.zst") and !ReleasePackage.valid_bundle_file("../Abc123.tar.zst") and !ReleasePackage.valid_bundle_file(".tar.zst") and !ReleasePackage.valid_bundle_file("Abc123.tar.gz") and !ReleasePackage.valid_bundle_file("name-Abc123.tar.zst")
expect ReleasePackage.bundle_url(pkg, "0.14.0", "Abc123.tar.zst") == Ok(url)
expect ReleasePackage.bundle_url(pkg, "latest", "Abc123.tar.zst") == Err(InvalidVersion("latest"))
expect ReleasePackage.bundle_url(pkg, "0.0.0", "Abc123.tar.zst") == Err(InvalidVersion("0.0.0"))
expect ReleasePackage.bundle_url(pkg, "0.14.0", "a/b.tar.zst") == Err(InvalidBundleFile("a/b.tar.zst"))
expect ReleasePackage.version_of_bundle_url(pkg, url) == Ok("0.14.0")
expect ReleasePackage.version_of_bundle_url(pkg, "http://127.0.0.1:8000/Abc123.tar.zst").is_err()
expect ReleasePackage.version_of_bundle_url(pkg, "../package/main.roc").is_err()
expect ReleasePackage.version_of_bundle_url(pkg, "https://github.com/someone/roc-ansi/releases/download/0.14.0/Abc123.tar.zst").is_err()
expect ReleasePackage.version_of_bundle_url(pkg, "https://github.com/lukewilliamboswell/roc-ansi/releases/download/latest/Abc123.tar.zst").is_err()
expect ReleasePackage.examples_root(pkg, "0.14.0") == "roc-ansi-examples-0.14.0" and ReleasePackage.examples_archive(pkg, "0.14.0") == "roc-ansi-examples-0.14.0.zip"
expect ReleasePackage.docs_archive(pkg, "0.14.0") == "roc-ansi-docs-0.14.0.zip" and ReleasePackage.pdf(pkg, "0.14.0") == "roc-ansi-0.14.0.pdf"
expect ReleasePackage.valid_examples_entry("roc-ansi-examples-0.14.0", "roc-ansi-examples-0.14.0/README.md")
expect ReleasePackage.valid_examples_entry("roc-ansi-examples-0.14.0", "roc-ansi-examples-0.14.0/spec.json") and !ReleasePackage.valid_examples_entry("roc-ansi-examples-0.14.0", "roc-ansi-examples-0.14.0/examples/spec.json")
expect ReleasePackage.valid_examples_entry("roc-ansi-examples-0.14.0", "roc-ansi-examples-0.14.0/examples/menu.roc")
expect !ReleasePackage.valid_examples_entry("roc-ansi-examples-0.14.0", "roc-ansi-examples-0.14.0/examples/../../escape.roc")
expect !ReleasePackage.valid_examples_entry("roc-ansi-examples-0.14.0", "roc-ansi-examples-0.14.0/examples/nested/app.roc")
expect !ReleasePackage.valid_examples_entry("roc-ansi-examples-0.14.0", "roc-ansi-examples-0.14.0/examples/notes.txt")
expect !ReleasePackage.valid_examples_entry("roc-ansi-examples-0.14.0", "roc-ansi-examples-0.13.0/examples/colors.roc")
expect !ReleasePackage.valid_examples_entry("roc-ansi-examples-0.14.0", "/etc/passwd")
expect !ReleasePackage.valid_examples_entry("roc-ansi-examples-0.14.0", "roc-ansi-examples-0.14.0/examples/.roc")
expect ReleasePackage.examples_readme(pkg, "0.14.0", url).contains("    ${url}\n") and ReleasePackage.examples_readme(pkg, "0.14.0", url).ends_with("/releases/tag/0.14.0\n")
expect ReleasePackage.notes_title(pkg, "0.14.0") == "= roc-ansi 0.14.0"
expect ReleasePackage.newest_first(["0.9.0", "0.13.1", "0.10.0", "0.13.0"]) == ["0.13.1", "0.13.0", "0.10.0", "0.9.0"]

notes = "= roc-ansi 0.14.0\n\nAdds streaming.\n\n== Use this release\n\n[source,roc]\n----\napp [main!] {\n\tansi: \"{package-url}\",\n}\n----\n\n=== Thanks\n\n* See https://example.com/a[the issue] and https://example.com/b[the fix].\n* Plain https://example.com stays.\n"

release = { title: "= roc-ansi 0.14.0", package_url: "https://example.com/Abc.tar.zst" }

expect ReleasePackage.render_notes(notes, release) == Ok("Adds streaming.\n\n## Use this release\n\n```roc\napp [main!] {\n\tansi: \"https://example.com/Abc.tar.zst\",\n}\n```\n\n### Thanks\n\n* See [the issue](https://example.com/a) and [the fix](https://example.com/b).\n* Plain https://example.com stays.\n")
expect ReleasePackage.render_notes(notes, { ..release, title: "= roc-ansi 0.15.0" }) == Err(WrongTitle("= roc-ansi 0.14.0"))
expect ReleasePackage.render_notes("= roc-ansi 0.14.0\n\nNothing to install.\n", release) == Err(UnusedPlaceholder)
expect ReleasePackage.render_notes("= roc-ansi 0.14.0\n\n{package-url} and {tzdb-url}\n", release) == Err(UnresolvedPlaceholder("{tzdb-url}"))
expect ReleasePackage.render_notes("= roc-ansi 0.14.0\n\n{package-url}\n\n----\nnever closed\n", release) == Err(UnclosedBlock)
expect ReleasePackage.render_notes("= roc-ansi 0.14.0\n\n----\n{package-url}\n----\n", release) == Ok("```\nhttps://example.com/Abc.tar.zst\n```\n")
expect ReleasePackage.render_notes("", release) == Err(WrongTitle(""))
expect ReleasePackage.built_with({ compiler: "nightly-2", automation: "" }) == "## Built with\n\n- Package and examples built and tested with `roc` nightly-2\n"
expect ReleasePackage.built_with({ compiler: "nightly-2", automation: "lukewilliamboswell/roc-automation@abc" }).contains("workflow at `lukewilliamboswell/roc-automation@abc`\n")
