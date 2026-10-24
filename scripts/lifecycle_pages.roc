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
import cli.Stdout
import cli.Stderr
import docs.BuildFiles
import docs.Sha256
import src/Compatibility
import src/LifecyclePlan
import src/LifecycleAdmission
import src/LifecyclePages
import src/ToolVersion

## Read-only after-publication restoration. No consumer checkout, renderer,
## Blueprint evaluation or candidate executable runs in this utility.
main! = |args| {
	if args.map(OsStr.display) == ["--version"] {
		Stdout.line!(ToolVersion.json("lifecycle-pages"))?
		return Ok({})
	}
	result = restore!(args.map(OsStr.display))
	match result {
		Ok({}) => Ok({})
		Err(reason) => {
			Stderr.line!("lifecycle-pages: ${Compatibility.console(reason)}")?
			Err(Exit(1))
		}
	}
}

env! = |name| Env.var_str!(OsStr.from_str(name)).map_err(|_| "missing trusted workflow identity ${name}")

native! = |path| Compatibility.native_path(
	Path.display(path),
	match Env.platform!().os {
		WINDOWS => Bool.True
		_ => Bool.False
	},
)

input! = |path, limit| {
	file = Path.utf8(path)
	if Path.type!(file).map_err(|_| "cannot inspect Pages input")? != IsFile {
		return Err("Pages input must be a regular file")
	}
	BuildFiles.read!(file, limit)
}

text! = |path, limit| Str.from_utf8(input!(path, limit)?).map_err(|_| "Pages metadata must be UTF-8")

restore! = |args| match args {
	["restore", admission_path, topology_path, requested, public_path, output_path] => {
		report = LifecycleAdmission.parse_report(text!(admission_path, 1_048_576)?)?
		topology_bytes = input!(topology_path, 65536)?
		topology = LifecyclePlan.parse_topology(Str.from_utf8(topology_bytes).map_err(|_| "reviewed topology must be UTF-8")?)?
		binding = { event: env!("GITHUB_EVENT_NAME")?, repository: env!("GITHUB_REPOSITORY")?, source_sha: env!("GITHUB_SHA")?, run_id: env!("GITHUB_RUN_ID")? }
		selected = LifecyclePages.select(report, topology, Sha256.hex(topology_bytes), requested, binding)?
		public = Path.utf8(public_path)
		if Path.type!(public).map_err(|_| "cannot inspect admitted public assets")? != IsDir {
			return Err("admitted public assets must be a regular directory")
		}
		archive = BuildFiles.inside!(public, selected.archive.path)?
		bytes = BuildFiles.read!(archive, 50_000_000)?
		if bytes.len() != selected.archive.size or Sha256.hex(bytes) != selected.archive.sha256 {
			return Err("PAGES_BYTES: documentation ZIP differs from admitted bytes")
		}
		if !output_path.to_utf8().all(|byte| byte >= 32 and byte != 127) {
			return Err("Pages output directory contains control characters")
		}
		output = Path.absolute!(Path.utf8(output_path)).map_err(|_| "cannot resolve Pages output directory")?
		python = env!("RA_PYTHON")?
		helper = env!("RA_ARCHIVE_HELPER")?
		trusted_path = env!("PATH")?
		var $command = Cmd.new(OsStr.from_str(python)).args([helper, "--artifact", native!(archive), "sha256:${selected.archive.sha256}", native!(output)].map(OsStr.from_str)).clear_envs().env_str("PATH", trusted_path)
		match Env.var_str!("SystemRoot") {
			Ok(root) => {
				$command = $command.env_str("SystemRoot", root)
			}
			Err(_) => {}
		}
		result = $command.stdout(Capture).stderr(Capture).timeout_ms(120_000).output_limit(2_000_000).manage_tree(Bool.True).run!().map_err(|_| "Pages ZIP verification was interrupted")?
		if result.status != Exited(0) {
			return Err("PAGES_ARCHIVE: ${Str.from_utf8_lossy(result.stderr_bytes)}")
		}
		site = match site!(output, selected.root) {
			Ok(value) => value
			Err(reason) => {
				_ = Path.delete_all!(output)
				return Err(reason)
			}
		}
		# Emit the handoff only after the exact original archive and the complete
		# extracted inventory have passed. A failed run never replaces an old site.
		handoff = "site-directory=${native!(site)}\narchive=${selected.archive.path}\nsha256=${selected.archive.sha256}\nbytes=${selected.archive.size.to_str()}\ndocumentation-id=${selected.documentation_id}\nversion=${report.version}\nsource-sha=${report.source_sha}\n"
		match Env.var_str!("GITHUB_OUTPUT") {
			Ok(path) => {
				before = Path.read_utf8!(Path.utf8(path)).map_err(|_| "cannot read Pages action outputs")?
				Path.write_utf8!(Path.utf8(path), "${before}${handoff}").map_err(|_| "cannot write Pages action outputs")?
			}
			Err(_) => {}
		}
		Stdout.line!(handoff).map_err(|_| "cannot report restored site")
	}
	_ => Err("usage: lifecycle-pages restore ADMISSION TOPOLOGY DOCS_ID PUBLIC_ASSETS OUTPUT_DIRECTORY")
}

site! = |output, expected| {
	entries = Path.list!(output).map_err(|_| "cannot inspect extracted Pages root")?
	site = match entries {
		[one] if Path.filename(one).map_ok(Path.display) == Ok(expected) => one
		_ => return Err("PAGES_ROOT: documentation ZIP must have its sole declared root")
	}
	if Path.type!(site).map_err(|_| "cannot inspect selected Pages root")? != IsDir {
		return Err("PAGES_ROOT: documentation root must be a regular directory")
	}
	index = BuildFiles.inside!(site, "index.html")?
	if BuildFiles.read!(index, 16_777_216)?.is_empty() {
		return Err("PAGES_INDEX: restored documentation needs a nonempty regular index.html")
	}
	Ok(site)
}
