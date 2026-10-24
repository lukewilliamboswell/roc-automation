import GitHub
import AppHeader

## Canonical published-release suite and result contract. This module has no
## effects: neither archive source nor diagnostic text can issue workflow commands.
Compatibility := [].{
	Asset : { id : U64, name : Str, size : U64, digest : Str }
	Release : { id : U64, tag_name : Str, draft : Bool, prerelease : Bool, published_at : Str, assets : List(Asset) }
	Selection : { schema_version : U64, repository : Str, nightly : Str, release : Str, release_id : U64, asset : Asset, runners : List(Str) }
	Case : { name : Str, args : List(Str), stdin : Str, stdout : Str, stdout_match : Try(Str, [Missing]), stderr : Str, stderr_match : Try(Str, [Missing]), exit_code : I32, skip : Try({ reason : Str, issue : Str }, [Missing]) }
	App : { path : Str, build_args : Try(List(Str), [Missing]), target : Try(Str, [Missing]), run : Try(List(Str), [Missing]), cases : List(Case) }
	Suite : { schema_version : U64, release : Str, dependencies : List(Str), apps : List(App) }
	Result : { schema_version : U64, repository : Str, nightly : Str, release : Str, asset_digest : Str, runner : Str, run_id : Str, run_attempt : Str, outcome : Str, tested : U64, failed : U64, skipped : U64, diagnostic_commands : U64, detail : Str }
	Environment : { name : Str, value : Str }
	valid_environment : List(Environment) -> Bool
	valid_environment = |values| unique(values.map(|entry| upper(entry.name))).len() == values.len() and values.all(
		|entry| {
			name = upper(entry.name)
			GitHub.is_plain(entry.name, "_") and !["PATH", "PATHEXT", "SYSTEMROOT", "COMSPEC", "ROC", "ROC_CACHE_DIR", "NO_COLOR", "XDG_CACHE_HOME"].contains(name) and !name.starts_with("GITHUB_") and !name.starts_with("GH_") and !name.starts_with("RA_")
		},
	)
	upper = |value| Str.from_utf8_lossy(Str.to_utf8(value).map(|byte| if byte >= 'a' and byte <= 'z' byte - 32 else byte))

	## External compiler/runtime interfaces receive the host's path separators;
	## Windows compiler cache identities compare their literal root prefixes.
	native_path = |value, windows| if windows value.replace_each("/", "\\") else value
	compiler_matches = |output, expected| (output.split_on("\n").first() ?? "").trim().replace_each("\t", " ").split_on(" ").contains(expected)
	valid_repository = GitHub.valid_repository

	runners = ["ubuntu-22.04", "ubuntu-24.04", "ubuntu-24.04-arm", "macos-15", "macos-26", "windows-2022", "windows-2025"]
	valid_runners = |values| !values.is_empty() and values.len() <= 7 and values.all(|value| runners.contains(value)) and unique(values).len() == values.len()
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
		validate_apps(found)
	}

	validate_apps : Suite -> Try(Suite, Str)
	validate_apps = |found| {
		if found.schema_version != 1 or found.apps.is_empty() or found.apps.len() > 100 {
			return Err("suite schema 1 needs 1–100 applications")
		}
		if unique(found.apps.map(|example| example.path)).len() != found.apps.len() {
			return Err("duplicate suite applications")
		}
		for example in found.apps {
			build_args = example.build_args ?? []
			if build_args != [] and build_args != ["--fuzz"] {
				return Err("build_args permits only --fuzz once; compiler, target and output are runner-owned")
			}
			target = example.target ?? "native"
			runtime = example.run ?? []
			if !GitHub.is_plain(target, "-_") or (target != "native" and runtime.is_empty()) or (!runtime.is_empty() and !runtime.any(|arg| arg.contains("{executable}"))) {
				return Err("cross targets need an explicit runtime argv using {executable}")
			}
			if !path(example.path) or !example.path.starts_with("examples/") or !example.path.ends_with(".roc") or example.cases.is_empty() or example.cases.len() > 100 or unique(example.cases.map(|case| case.name)).len() != example.cases.len() {
				return Err("invalid application path or cases")
			}
			if !example.cases.all(
				|case| case.name != "" and ["exact", "strip-sgr-and-cr"].contains(case.stdout_match ?? "exact") and case.args.len() <= 100 and ["exact", "ignore"].contains(case.stderr_match ?? "exact") and ((case.stderr_match ?? "exact") != "ignore" or case.stderr == "") and match case.skip {
					Ok(skip) => skip.reason != "" and skip.issue.starts_with("https://github.com/")
					Err(Missing) => Bool.True
				},
			) {
				return Err("cases need unique names and valid skips; stdout_match must be exact or strip-sgr-and-cr, stderr_match exact or ignore (with empty stderr)")
			}
		}
		Ok(found)
	}

	## An example must name a declared release dependency in its header. The runner
	## never substitutes the default-branch bundle or edits a compiler constraint.
	app_source = AppHeader.valid

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

expect Compatibility.select("o/pkg", selection.nightly, "", "pkg", ["ubuntu-24.04"], [release]) == Ok(selection)
expect Compatibility.select("o/pkg", selection.nightly, "", "pkg", ["ubuntu-24.04"], [{ ..release, tag_name: "runtime-v2.0.0" }, release]) == Ok(selection)
expect Compatibility.select("o/pkg", selection.nightly, "", "pkg", ["ubuntu-24.04"], [{ ..release, tag_name: "2.0.0", published_at: "2026-10-21", assets: [] }, release]).is_err()
expect Compatibility.select("o/pkg", selection.nightly, "", "pkg", ["ubuntu-24.04"], [{ ..release, prerelease: Bool.True }]).is_err()
expect Compatibility.select("o/pkg", selection.nightly, "", "pkg", ["self-hosted"], [release]).is_err()
expect !Compatibility.valid_runners(["ubuntu-24.04", "ubuntu-24.04"])
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

expect Compatibility.valid_environment([{ name: "DISPLAY", value: ":99" }])
expect !Compatibility.valid_environment([{ name: "GH_TOKEN", value: "secret" }])
expect !Compatibility.valid_environment([{ name: "PATH", value: "/tmp" }])
expect !Compatibility.valid_environment([{ name: "DISPLAY", value: ":99" }, { name: "DISPLAY", value: ":98" }])
expect !Compatibility.app_source("app [main!] { roc : \"old\", pf: platform \"url\" }", ["url"])
expect Compatibility.aggregate(selection, "10", "1", [{ ..result, outcome: "test-timeout" }]) == { outcome: "test-timeout", report: "none" }
expect Compatibility.aggregate(selection, "10", "1", [{ ..result, outcome: "output-limit" }]) == { outcome: "output-limit", report: "none" }

expect Compatibility.valid_runners(["windows-2022", "windows-2025"])
expect !Compatibility.valid_environment([{ name: "Path", value: "x" }])
expect !Compatibility.valid_environment([{ name: "gh_token", value: "x" }])
expect !Compatibility.valid_environment([{ name: "Display", value: "x" }, { name: "DISPLAY", value: "y" }])
expect !Compatibility.valid_environment([{ name: "SystemRoot", value: "x" }])

expect Compatibility.native_path("D:\\a\\temp/output/empty-cache", Bool.True) == "D:\\a\\temp\\output\\empty-cache"
expect Compatibility.native_path("/tmp/output/empty-cache", Bool.False) == "/tmp/output/empty-cache"

expect Compatibility.compiler_matches("Roc compiler version nightly-example\n", "nightly-example")
expect !Compatibility.compiler_matches("Roc compiler version nightly-example-extra\n", "nightly-example")
expect !Compatibility.compiler_matches("other\nnightly-example", "nightly-example")
