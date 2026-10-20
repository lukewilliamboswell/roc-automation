import GitHub

## Canonical published-release suite and result contract. This module has no
## effects: neither archive source nor diagnostic text can issue workflow commands.
Compatibility := [].{
	Asset : { id : U64, name : Str, size : U64, digest : Str }
	Release : { id : U64, tag_name : Str, draft : Bool, prerelease : Bool, published_at : Str, assets : List(Asset) }
	Selection : { schema_version : U64, repository : Str, nightly : Str, release : Str, release_id : U64, asset : Asset, runners : List(Str) }
	Case : { name : Str, args : List(Str), stdin : Str, stdout : Str, stderr : Str, exit_code : I32, skip : Try({ reason : Str, issue : Str }, [Missing]) }
	App : { path : Str, cases : List(Case) }
	Suite : { schema_version : U64, release : Str, dependencies : List(Str), apps : List(App) }
	Result : { schema_version : U64, repository : Str, nightly : Str, release : Str, asset_digest : Str, runner : Str, run_id : Str, run_attempt : Str, outcome : Str, tested : U64, failed : U64, skipped : U64, diagnostic_commands : U64, detail : Str }

	runners = ["ubuntu-22.04", "ubuntu-24.04", "ubuntu-24.04-arm", "macos-15", "macos-26"]
	valid_runners = |values| !values.is_empty() and values.len() <= 5 and values.all(|value| runners.contains(value)) and unique(values).len() == values.len()
	tag = |value| value != "" and Str.to_utf8(value).len() <= 100 and GitHub.is_plain(value, "-._+") and !value.starts_with("-")
	version = |value| match Str.split_on(value.drop_prefix("v"), ".") {
		[a, b, c] => [a, b, c].all(GitHub.is_digits)
		_ => Bool.False
	}
	digest = |value| value.starts_with("sha256:") and Str.to_utf8(value).len() == 71 and Str.to_utf8(value.drop_prefix("sha256:")).all(|b| (b >= '0' and b <= '9') or (b >= 'a' and b <= 'f'))
	path = |value| value != "" and !value.starts_with("/") and Str.split_on(value, "/").all(|part| part != "." and part != ".." and GitHub.is_plain(part, "-._"))
	unique = |values| values.fold([], |seen, value| if seen.contains(value) seen else seen.append(value))

	## Product releases use final numeric tags. Linker-input, runtime and tools
	## releases are never mistaken for the package_name, even when newer. No fallback
	## to an older product release when the newest one lacks its examples asset.
	select : Str, Str, Str, Str, List(Str), List(Release) -> Try(Selection, Str)
	select = |repository, nightly, requested, package_name, runner_names, releases| {
		if !GitHub.valid_repository(repository) or !tag(nightly) or !nightly.starts_with("nightly-") or !GitHub.is_plain(package_name, "-_") or !valid_runners(runner_names) {
			return Err("invalid repository, nightly, package_name name or runner list")
		}
		products = releases.keep_if(|release| !release.draft and !release.prerelease and version(release.tag_name) and (requested == "" or release.tag_name == requested))
		ordered = products.sort_with(|a, b| if a.published_at == b.published_at Same else if GitHub.sort([a.published_at, b.published_at]) == [a.published_at, b.published_at] After else Before)
		selected = match ordered {
			[first, ..] => first
			[] => return Err("no published final product release")
		}
		name = "${package_name}-examples-${selected.tag_name}.zip"
		asset = match selected.assets.keep_if(|entry| entry.name == name) {
			[found] if found.id > 0 and found.size > 0 and found.size <= 50_000_000 and digest(found.digest) => found
			_ => return Err("latest product release must have one examples ZIP with a SHA-256 digest (at most 50 MB)")
		}
		Ok({ schema_version: 1, repository, nightly, release: selected.tag_name, release_id: selected.id, asset, runners: runner_names })
	}

	## ZIP metadata is inspected before extraction. Only ordinary Unix files and
	## directories with canonical paths are admitted; links, duplicates, traversal,
	## encryption and expansion beyond 100 MB fail closed.
	archive : Str, Str -> Try({}, Str)
	archive = |root, listing| {
		var $names = []
		var $files = []
		var $bytes = 0
		for line in Str.split_on(listing, "\n").keep_if(|line| !line.starts_with("Archive:") and !line.starts_with("Zip file size:") and line.trim() != "" and !line.contains(" bytes uncompressed,")) {
			parts = Str.split_on(line.trim(), " ").keep_if(|part| part != "")
			match parts {
				[mode, _, "unx", size, flags, _, _, _, _, name] if mode.starts_with("-") or mode.starts_with("d") => {
					plain_name = name.drop_suffix("/")
					if !path(plain_name) or (plain_name != root and !plain_name.starts_with("${root}/")) or $names.contains(plain_name) or flags != "b-" and flags != "tx" and flags != "t-" {
						return Err("unsafe, encrypted or duplicate ZIP member")
					}
					length = match U64.from_str(size) {
						Ok(n) if n <= 100_000_000 => n
						_ => return Err("invalid ZIP member size")
					}
					$bytes = $bytes + length
					if $bytes > 100_000_000 or $names.len() >= 10_000 {
						return Err("examples archive exceeds extraction limits")
					}
					$names = $names.append(plain_name)
					if mode.starts_with("-") {
						$files = $files.append(plain_name)
					}
				}
				_ => return Err("unsupported ZIP metadata or file type")
			}
		}
		if !$files.contains("${root}/spec.json") or $names.any(|name| $files.any(|file| name.starts_with("${file}/"))) {
			Err("archive lacks spec.json or has file/directory collisions")
		} else {
			Ok({})
		}
	}

	suite : Str, Selection -> Try(Suite, Str)
	suite = |source, selected| {
		parsed : Try(Suite, _)
		parsed = Json.parse(source)
		found = match parsed {
			Ok(value) => value
			Err(_) => return Err("invalid suite JSON")
		}
		prefix = "https://github.com/${selected.repository}/releases/download/${selected.release}/"
		if found.schema_version != 1 or found.release != selected.release or found.apps.is_empty() or found.apps.len() > 100 or found.dependencies.is_empty() or !found.dependencies.all(|url| url.starts_with(prefix) and path(url.drop_prefix(prefix)) and url.ends_with(".tar.zst")) {
			return Err("suite must describe this release, its immutable bundle URLs and 1–100 applications")
		}
		if unique(found.apps.map(|example| example.path)).len() != found.apps.len() {
			return Err("duplicate suite applications")
		}
		for example in found.apps {
			if !path(example.path) or !example.path.starts_with("examples/") or !example.path.ends_with(".roc") or example.cases.is_empty() or example.cases.len() > 100 or unique(example.cases.map(|case| case.name)).len() != example.cases.len() {
				return Err("invalid application path or cases")
			}
			if !example.cases.all(
				|case| case.name != "" and case.args.len() <= 100 and match case.skip {
					Ok(skip) => skip.reason != "" and skip.issue.starts_with("https://github.com/")
					Err(Missing) => Bool.True
				},
			) {
				return Err("cases need unique names and skips need a reason and issue")
			}
		}
		Ok(found)
	}

	## An example must name a declared release dependency in its header. The runner
	## never substitutes the default-branch bundle or edits a compiler constraint.
	app_source = |source, dependencies| match Str.split_first(source, "}") {
		Ok({ before, .. }) => dependencies.any(|url| before.contains("\"${url}\"")) and !Str.split_on(before, "\n").any(|line| line.trim().starts_with("roc:"))
		Err(_) => Bool.False
	}

	## Preserve raw diagnostics as artifacts; escape command markers and control
	## characters only in their console rendering. Success never discards output.
	console = |value| value.replace_each("::", ": :").replace_each("\r", "\\r").replace_each("\u(001b)", "\\e")
	classify = |tested, failed, skipped| if failed > 0 "incompatible" else if tested == 0 or skipped > 0 "not-tested" else "compatible"

	## Every target must contribute the current attempt's matching evidence.
	## An infrastructure failure can never close a compatibility issue.
	aggregate : Selection, Str, Str, List(Result) -> { outcome : Str, report : Str }
	aggregate = |selection, run_id, attempt, results| {
		matching = results.len() == selection.runners.len() and unique(results.map(|r| r.runner)).len() == results.len() and results.all(|r| r.schema_version == 1 and selection.runners.contains(r.runner) and r.repository == selection.repository and r.nightly == selection.nightly and r.release == selection.release and r.asset_digest == selection.asset.digest and r.run_id == run_id and r.run_attempt == attempt)
		if !matching {
			return { outcome: "setup-failed", report: "none" }
		}
		if results.any(|r| r.outcome == "incompatible" and r.failed > 0 and r.diagnostic_commands > 0) {
			return { outcome: "incompatible", report: "failure" }
		}
		if results.any(|r| r.outcome == "setup-failed") {
			return { outcome: "setup-failed", report: "none" }
		}
		if results.any(|r| r.outcome == "test-timeout") {
			return { outcome: "test-timeout", report: "none" }
		}
		if results.any(|r| r.outcome == "output-limit") {
			return { outcome: "output-limit", report: "none" }
		}
		if results.all(|r| r.outcome == "compatible" and r.tested > 0 and r.failed == 0 and r.skipped == 0 and r.diagnostic_commands > 0) {
			{ outcome: "compatible", report: "success" }
		} else {
			{ outcome: "not-tested", report: "none" }
		}
	}
}

asset = { id: 2, name: "pkg-examples-1.0.0.zip", size: 100, digest: "sha256:${Str.repeat("a", 64)}" }

release = { id: 1, tag_name: "1.0.0", draft: Bool.False, prerelease: Bool.False, published_at: "2026-10-20T01:00:00Z", assets: [asset] }

selection = { schema_version: 1, repository: "o/pkg", nightly: "nightly-2026-10-20-abc", release: "1.0.0", release_id: 1, asset, runners: ["ubuntu-24.04"] }

result = { schema_version: 1, repository: "o/pkg", nightly: selection.nightly, release: "1.0.0", asset_digest: asset.digest, runner: "ubuntu-24.04", run_id: "10", run_attempt: "1", outcome: "compatible", tested: 1, failed: 0, skipped: 0, diagnostic_commands: 2, detail: "" }

listing = "Archive: x\nZip file size: 100 bytes, number of entries: 1\n-rw-r--r--  2.0 unx 10 b- 10 stor 26-Oct-20 09:25 root/spec.json\n1 file, 10 bytes uncompressed, 10 bytes compressed: 0%\n"

expect Compatibility.select("o/pkg", selection.nightly, "", "pkg", ["ubuntu-24.04"], [release]) == Ok(selection)
expect Compatibility.select("o/pkg", selection.nightly, "", "pkg", ["ubuntu-24.04"], [{ ..release, tag_name: "runtime-v2.0.0" }, release]) == Ok(selection)
expect Compatibility.select("o/pkg", selection.nightly, "", "pkg", ["ubuntu-24.04"], [{ ..release, tag_name: "2.0.0", published_at: "2026-10-21", assets: [] }, release]).is_err()
expect Compatibility.select("o/pkg", selection.nightly, "", "pkg", ["ubuntu-24.04"], [{ ..release, prerelease: Bool.True }]).is_err()
expect Compatibility.select("o/pkg", selection.nightly, "", "pkg", ["self-hosted"], [release]).is_err()
expect !Compatibility.valid_runners(["ubuntu-24.04", "ubuntu-24.04"])
expect Compatibility.archive("root", listing) == Ok({})
expect Compatibility.archive("root", listing.replace_each("root/spec.json", "root/../spec.json")).is_err()
expect Compatibility.archive("root", listing.replace_each("-rw-r--r--", "lrwxrwxrwx")).is_err()
expect Compatibility.archive("root", listing.replace_each(" b- ", " BX ")).is_err()
expect Compatibility.archive("root", listing.replace_each(" 10 b-", " 100000001 b-")).is_err()
expect Compatibility.archive("root", "").is_err()
expect Compatibility.console("::error::bad\r\n\u(001b)[31mwarning") == ": :error: :bad\\r\n\\e[31mwarning"
expect Compatibility.classify(0, 0, 0) == "not-tested" and Compatibility.classify(1, 0, 1) == "not-tested"
expect Compatibility.classify(1, 1, 0) == "incompatible" and Compatibility.classify(1, 0, 0) == "compatible"
expect Compatibility.aggregate(selection, "10", "1", [result]) == { outcome: "compatible", report: "success" }
expect Compatibility.aggregate(selection, "10", "1", []).report == "none"
expect Compatibility.aggregate(selection, "10", "2", [result]).report == "none"
expect Compatibility.aggregate(selection, "10", "1", [{ ..result, tested: 0 }]).report == "none"
expect Compatibility.aggregate(selection, "10", "1", [{ ..result, skipped: 1 }]).report == "none"
expect Compatibility.aggregate(selection, "10", "1", [{ ..result, outcome: "setup-failed" }]).report == "none"
expect Compatibility.aggregate(selection, "10", "1", [{ ..result, outcome: "incompatible", failed: 1 }]).report == "failure"
expect Compatibility.aggregate(selection, "10", "1", [{ ..result, asset_digest: "changed" }]).report == "none"
expect Compatibility.aggregate(selection, "10", "1", [result, result]).report == "none"
