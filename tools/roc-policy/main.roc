app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
	parser: "https://github.com/lukewilliamboswell/roc-parser/releases/download/2.1.0-rc.1/6dvNG3Q2mx53i4YPJN2ZGK98WdXnBMMuceAmDsCq7BEd.tar.zst",
}

import cli.Cmd
import cli.OsStr
import cli.Path
import cli.Stdout
import cli.Stderr
import src/Lint
import src/PolicyConfig
import src/Options
import src/TestAccounting

main! = |args| {
	match execute!(args.map(OsStr.display)) {
		Ok({}) => Ok({})
		Err(Exit(code)) => Err(Exit(code))
		Err(problem) => {
			Stderr.line!("error: ${Str.inspect(problem)}")?
			Err(Exit(2))
		}
	}
}

execute! = |args| {
	if args == ["--help"] {
		return Stdout.line!(Options.usage)
	}
	options = match Options.parse(args) {
		Ok(found) => found
		Err(message) => return fail!("${message}\n${Options.usage}")
	}
	root = Path.canonicalize!(Path.utf8(options.root))?
	config_source = read_file!(root, options.config)?
	config_text = match Str.from_utf8(config_source) {
		Ok(text) => text
		Err(_) => return fail!("config is not UTF-8")
	}
	config = match PolicyConfig.decode(config_text, options.definition) {
		Ok(found) => found
		Err(message) => return fail!(message)
	}
	if options.command == "check" {
		if config.rules.is_empty() {
			return fail!("check requires at least one rule")
		}
		files = collect!(root, config.roots, False)?
		for rule in config.rules {
			if !files.any(|file| Lint.eligible(file.path, file.source, rule)) {
				return fail!("rule ${Str.inspect(rule.kind)} has no applicable source files in ${rule.scope}")
			}
		}
		var $issues = []
		var $checked = 0.U64
		for file in files {
			if config.rules.any(|rule| Lint.eligible(file.path, file.source, rule)) {
				$checked = $checked + 1
			}
			for issue in Lint.check(file.path, file.source, config.rules) {
				$issues = $issues.append(located(file, issue))
			}
		}
		if $checked == 0 {
			return fail!("no selected files match the configured rules")
		}
		incomplete = $issues.any(|issue| issue.rule.ends_with("/incomplete"))
		report!($issues, $checked, options.format, "source-policy")?
		if incomplete Err(Exit(2)) else if $issues.is_empty() Ok({}) else Err(Exit(1))
	} else {
		files = collect!(root, config.test_roots, True)?
		var $inventories = []
		for file in files {
			inventory = match TestAccounting.inventory(file.path, file.source) {
				Ok(found) => found
				Err(message) => return fail!("${file.path}: ${message}")
			}
			if !inventory.tests.is_empty() {
				$inventories = $inventories.append(inventory)
			}
		}
		if $inventories.is_empty() {
			return fail!("no static tests selected")
		}
		if options.command == "manifest" {
			Stdout.line!(Json.to_str({ schema_version: 1.U64, files: $inventories }))
		} else if options.command == "verify-tests" {
			receipt = Str.from_utf8(read_file!(root, options.report)?)?
			findings = match TestAccounting.verify($inventories, receipt) {
				Ok(found) => found
				Err(message) => return fail!(message)
			}
			var $issues = []
			for issue in findings {
				file = files.find_first(|candidate| candidate.path == issue.path) ?? { path: issue.path, source: [] }
				$issues = $issues.append(located(file, { rule: issue.rule, message: issue.message, start: issue.start, end: issue.end }))
			}
			report!($issues, $inventories.len(), options.format, "test-identity-receipts")?
			if $issues.is_empty() Ok({}) else Err(Exit(1))
		} else {
			# Explicit execution is a separate command. The source-checking and
			# receipt-verification paths above never execute candidate code.
			compiler = Path.canonicalize!(Path.utf8(options.compiler))?
			if !Path.is_file!(compiler)? {
				return fail!("compiler must be a regular file")
			}
			if config.test_main != "" {
				_ = read_file!(root, config.test_main)?
			}
			var $issues = []
			for inventory in $inventories {
				argv = ["test", inventory.path, "--no-cache"].concat(if config.test_main == "" [] else ["--main", config.test_main])
				output = Cmd.new(Path.to_os_str(compiler)).args(argv.map(OsStr.from_str)).cwd(root).timeout_ms(60000).manage_tree(True).run!()?
				file = { path: inventory.path, source: inventory.source.to_utf8() }
				if output.status != Exited(0) {
					$issues = $issues.append(located(file, { rule: "tests/count", start: 0, end: 0, message: "compiler test command failed; ${Str.from_utf8_lossy(output.stderr_bytes)}" }))
				} else {
					count = match TestAccounting.summary_count(Str.from_utf8_lossy(output.stdout_bytes)) {
						Ok(found) => found
						Err(message) => return fail!("${inventory.path}: ${message}")
					}
					# Re-read after execution: a runner cannot accidentally
					# validate the count of source it changed while testing.
					if read_file!(root, inventory.path)? != inventory.source.to_utf8() {
						return fail!("${inventory.path}: source changed during test execution")
					}
					if count != inventory.tests.len() {
						$issues = $issues.append(located(file, { rule: "tests/count", start: 0, end: 0, message: "declared ${inventory.tests.len().to_str()} static tests but runner reports ${count.to_str()}" }))
					}
				}
			}
			report!($issues, $inventories.len(), options.format, "fresh-per-file-counts")?
			if options.format == "text" {
				Stdout.line!("Evidence: fresh per-file counts; this does not prove individual test identities.")?
			}
			if $issues.is_empty() Ok({}) else Err(Exit(1))
		}
	}
}

checked_path! = |root, relative| {
	if !PolicyConfig.safe_path(relative) {
		return Err(InvalidInput("unsafe relative path ${relative}"))
	}
	path = Path.canonicalize!(Path.join(root, relative))?
	if Path.display(path) != "${Path.display(root)}/${relative}" {
		return Err(InvalidInput("${relative}: path escapes root or traverses a symlink"))
	}
	Ok(path)
}

read_file! = |root, relative| {
	path = checked_path!(root, relative)?
	if !Path.is_file!(path)? {
		return Err(InvalidInput("${relative}: expected a regular file"))
	}
	bytes = Path.read_bytes!(path)?
	if bytes.len() > 8 * 1024 * 1024 {
		return Err(InvalidInput("${relative}: file exceeds 8 MiB input limit"))
	}
	Ok(bytes)
}

collect! = |root, roots, roc_only| {
	var $queue = roots
	var $seen = []
	var $files = []
	while !$queue.is_empty() {
		if $seen.len() > 100000 {
			return Err(InvalidInput("source inventory exceeds 100000 paths"))
		}
		relative = $queue.first() ?? ""
		$queue = $queue.drop_first(1)
		path = checked_path!(root, relative)?
		if !$seen.contains(relative) {
			$seen = $seen.append(relative)
			if Path.is_dir!(path)? {
				entries = Path.list!(path)?.map(Path.display).sort_with(compare)
				for entry in entries {
					name = entry.split_on("/").last() ?? ""
					$queue = $queue.append("${relative}/${name}")
				}
			} else if relative.ends_with(".roc") or (!roc_only and [".md", ".markdown", ".adoc", ".txt", ".rst"].any(|suffix| relative.ends_with(suffix))) {
				$files = $files.append({ path: relative, source: read_file!(root, relative)? })
			}
		}
	}
	if $files.is_empty() {
		return Err(InvalidInput("no supported source files selected"))
	}
	Ok($files)
}

Diagnostic : { path : Str, rule : Str, message : Str, start : U64, end : U64, line : U64, column : U64 }

located : { path : Str, source : List(U8) }, Lint.Issue -> Diagnostic
located = |file, issue| {
	prefix = file.source.sublist({ start: 0, len: issue.start })
	lines = Str.from_utf8_lossy(prefix).split_on("\n")
	column = (lines.last() ?? "").to_utf8().keep_if(|b| b < 128 or b >= 192).len() + 1
	{ path: file.path, rule: issue.rule, message: issue.message, start: issue.start, end: issue.end, line: lines.len(), column }
}

report! : List(Diagnostic), U64, Str, Str => Try({}, _)
report! = |issues, count, format, evidence| {
	if format == "json" {
		Stdout.line!(Json.to_str({ schema_version: 1.U64, evidence, complete: !issues.any(|issue| issue.rule.ends_with("/incomplete")), files: count, issues }))
	} else {
		for issue in issues {
			Stdout.line!("${issue.path}:${issue.line.to_str()}:${issue.column.to_str()}: ${issue.rule}: ${issue.message}")?
		}
		Stdout.line!("${count.to_str()} files, ${issues.len().to_str()} findings")
	}
}

fail! = |message| {
	Stderr.line!("error: ${message}")?
	Err(Exit(2))
}

compare = |left, right| {
	a = left.to_utf8()
	b = right.to_utf8()
	var $i = 0
	while $i < a.len() and $i < b.len() {
		x = a.get($i) ?? 0
		y = b.get($i) ?? 0
		if x < y {
			return Before
		}
		if x > y {
			return After
		}
		$i = $i + 1
	}
	if a.len() < b.len() Before else if a.len() > b.len() After else Same
}
