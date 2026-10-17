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
import src/PolicyPlan
import src/PathGlob
import src/Sha256
import src/EmbeddedProfiles
import src/Options
import src/TestAccounting
import src/TypoData

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
	if args == ["list-profiles"] {
		return Stdout.line!(Json.to_str(EmbeddedProfiles.list))
	}
	if args == ["--licenses"] {
		return Stdout.line!("typos correction dictionary (MIT):\n${TypoData.license}")
	}
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
	declared = match PolicyPlan.decode(config_text, options.definition) {
		Ok(found) => found
		Err(message) => return fail!(message)
	}
	if declared.profiles.len() > 32 {
		return fail!("at most 32 shared profiles may be selected")
	}
	var $loaded = []
	for profile in declared.profiles {
		bytes = if profile.builtin != "" {
			match EmbeddedProfiles.get(profile.builtin, profile.version) {
				Ok(source) => source.to_utf8()
				Err(message) => return fail!(message)
			}
		} else {
			read_file!(root, profile.path)?
		}
		if bytes.len() > 1024 * 1024 {
			return fail!("profile ${profile.name} exceeds 1 MiB")
		}
		digest = Sha256.hex(bytes)
		if profile.builtin == "" and digest != profile.sha256 {
			return fail!("profile ${profile.name}: SHA-256 mismatch")
		}
		source = match Str.from_utf8(bytes) {
			Ok(value) => value
			Err(_) => return fail!("profile ${profile.name} is not UTF-8")
		}
		$loaded = $loaded.append({ profile: { ..profile, sha256: digest }, source })
	}
	config = match PolicyPlan.finalize(declared, $loaded) {
		Ok(found) => found
		Err(message) => return fail!(message)
	}
	if options.command == "explain-config" {
		return explain!(root, config, options)
	}
	if options.command == "check" {
		if config.rules.is_empty() {
			return fail!("check requires at least one rule")
		}
		files = collect!(root, config.roots, False, config.exclude)?
		var $issues = []
		var $checked = 0.U64
		var $matched = []
		var $used_overrides = []
		for file in files {
			selected = match PolicyPlan.for_file(config, file.path) {
				Ok(found) => found
				Err(message) => return fail!(message)
			}
			for (override, index) in config.overrides.map_with_index(|item, index| (item, index)) {
				if PolicyPlan.override_matches(override, file.path) and selected.any(|rule| rule.id == override.rule) {
					$used_overrides = $used_overrides.append(index)
				}
			}
			var $file_checked = False
			for rule in selected {
				required_header = if config.schema_version == 1 file.path == rule.legacy.scope else rule.include.contains(file.path)
				eligible = if config.schema_version == 1 Lint.eligible(file.path, file.source, rule.legacy) else Lint.eligible_selected(file.path, file.source, rule.legacy, required_header)
				if rule.level == Off or eligible {
					$matched = $matched.append(rule.id)
				}
				if rule.level != Off and eligible {
					$file_checked = True
					findings = Lint.check_selected(file.path, file.source, rule.legacy, required_header)
					for issue in findings {
						severity = if issue.rule.ends_with("/incomplete") "error" else level_name(rule.level)
						$issues = $issues.append({ ..located(file, issue), rule_id: rule.id, level: severity, origin: rule.origin, trace: rule.trace })
					}
				}
			}
			if $file_checked {
				$checked = $checked + 1
			}
		}
		global_rules = match PolicyPlan.global_rules(config) {
			Ok(found) => found
			Err(message) => return fail!(message)
		}
		for rule in global_rules {
			if rule.require_match and rule.level != Off and !$matched.contains(rule.id) {
				return fail!("rule ${rule.id} has no applicable source files")
			}
		}
		for (override, index) in config.overrides.map_with_index(|item, index| (item, index)) {
			if override.require_match and !$used_overrides.contains(index) {
				return fail!("override for ${override.rule} matches no selected rule/file; set require_match: False if intentionally optional")
			}
		}
		incomplete = $issues.any(|issue| issue.rule.ends_with("/incomplete"))
		report!($issues, $checked, options.format, "source-policy")?
		warnings = $issues.keep_if(|issue| issue.level == "warning").len()
		if incomplete Err(Exit(2)) else if $issues.any(|issue| issue.level == "error") or warnings > config.max_warnings Err(Exit(1)) else Ok({})
	} else {
		files = collect!(root, config.test_roots, True, config.exclude)?
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
		} else {
			# Explicit execution is a separate command. Source checking and
			# inventory discovery above never execute candidate code.
			compiler = Path.canonicalize!(Path.utf8(options.compiler))?
			if !Path.is_file!(compiler)? {
				return fail!("compiler must be a regular file")
			}
			main_flags = if config.test_main == "" [] else {
				_ = read_file!(root, config.test_main)?
				["--main=${Path.display(checked_path!(root, config.test_main)?)}"]
			}
			var $issues = []
			for inventory in $inventories {
				source_path = Path.display(checked_path!(root, inventory.path)?)
				argv = ["test", source_path, "--verbose"].concat(main_flags)
				output = Cmd.new(Path.to_os_str(compiler)).args(argv.map(OsStr.from_str)).cwd(root).timeout_ms(60000).manage_tree(True).run!()?
				file = { path: inventory.path, source: inventory.source.to_utf8() }
				if output.status != Exited(0) {
					$issues = $issues.append(located(file, { rule: "tests/accounting", start: 0, end: 0, message: "compiler test command failed; ${Str.from_utf8_lossy(output.stderr_bytes)}" }))
				} else {
					findings = match TestAccounting.verify_locations(inventory, source_path, Str.from_utf8_lossy(output.stdout_bytes)) {
						Ok(found) => found
						Err(message) => return fail!("${inventory.path}: ${message}")
					}
					for issue in findings {
						$issues = $issues.append(located(file, { rule: issue.rule, start: issue.start, end: issue.end, message: issue.message }))
					}
				}
			}
			# Check every inventoried source after the complete run, including files
			# tested earlier that a later invocation could have changed.
			for inventory in $inventories {
				if read_file!(root, inventory.path)? != inventory.source.to_utf8() {
					return fail!("${inventory.path}: source changed during test execution")
				}
			}
			report!($issues, $inventories.len(), options.format, "compiler-pass-locations")?
			if options.format == "text" {
				Stdout.line!("Evidence: compiler-reported source lines; cached results are accepted.")?
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

collect! = |root, roots, roc_only, exclude| {
	var $queue = roots
	var $seen = []
	var $files = []
	while !$queue.is_empty() {
		if $seen.len() > 100000 {
			return Err(InvalidInput("source inventory exceeds 100000 paths"))
		}
		relative = $queue.first() ?? ""
		$queue = $queue.drop_first(1)
		if !PathGlob.any_match(exclude, relative) {
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
	}
	Ok($files)
}

Diagnostic : { path : Str, rule : Str, rule_id : Str, level : Str, origin : Str, trace : List(Str), message : Str, start : U64, end : U64, line : U64, column : U64 }

located : { path : Str, source : List(U8) }, Lint.Issue -> Diagnostic
located = |file, issue| {
	prefix = file.source.sublist({ start: 0, len: issue.start })
	lines = Str.from_utf8_lossy(prefix).split_on("\n")
	column = (lines.last() ?? "").to_utf8().keep_if(|b| b < 128 or b >= 192).len() + 1
	{ path: file.path, rule: issue.rule, rule_id: issue.rule, level: "error", origin: "runner", trace: [], message: issue.message, start: issue.start, end: issue.end, line: lines.len(), column }
}

report! : List(Diagnostic), U64, Str, Str => Try({}, _)
report! = |issues, count, format, evidence| {
	if format == "json" {
		Stdout.line!(Json.to_str({ schema_version: 2.U64, evidence, complete: !issues.any(|issue| issue.rule.ends_with("/incomplete")), files: count, issues }))
	} else {
		for issue in issues {
			Stdout.line!("${issue.path}:${issue.line.to_str()}:${issue.column.to_str()}: ${issue.rule_id} [${issue.level}] ${issue.rule}: ${issue.message}")?
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

level_name = |level| match level {
	Off => "off"
	Warning => "warning"
	Error => "error"
}

explain! = |root, config, options| {
	config_file : Str
	config_file = options.config
	definition : Str
	definition = options.definition
	if options.path != "" and (!PolicyConfig.safe_path(options.path) or !PathGlob.matches("**", options.path)) {
		return fail!("--path must be a safe relative path")
	}
	rules_result = if options.path == "" PolicyPlan.global_rules(config) else PolicyPlan.effective_rules(config, options.path)
	rules = match rules_result {
		Ok(found) => found
		Err(message) => return fail!(message)
	}
	var $prefixes = []
	var $prefix = ""
	if options.path != "" {
		for component in options.path.split_on("/") {
			$prefix = if $prefix == "" component else "${$prefix}/${component}"
			$prefixes = $prefixes.append($prefix)
		}
	}
	discovery_exclusions = config.exclude.keep_if(|pattern| $prefixes.any(|prefix| PathGlob.matches(pattern, prefix)))
	within_roots = options.path != "" and config.roots.any(|root_path| options.path == root_path or options.path.starts_with("${root_path}/"))
	described = rules.map(
		|rule| {
			id: rule.id,
			kind: Str.inspect(rule.legacy.kind),
			level: level_name(rule.level),
			selected: within_roots and discovery_exclusions.is_empty() and PolicyPlan.selected(config, rule, options.path) and rule.level != Off,
			selection: if options.path == "" "global defaults; provide --path for file selection" else if !within_roots "outside discovery roots" else if !discovery_exclusions.is_empty() "excluded from discovery" else if !PolicyPlan.selected(config, rule, options.path) "outside rule selectors" else if rule.level == Off "disabled by level" else "selected",
			matching_rule_exclusions: rule.exclude.keep_if(|pattern| options.path != "" and PathGlob.matches(pattern, options.path)),
			include: rule.include,
			exclude: rule.exclude,
			require_match: rule.require_match,
			origin: rule.origin,
			trace: rule.trace,
			options: {
				name: rule.legacy.name,
				value: rule.legacy.value,
				channels: rule.legacy.channels.map(Str.inspect),
				accepted_words: rule.legacy.allow,
				accepted_identifiers: rule.legacy.accepted_identifiers,
				corrections: rule.legacy.words.map(|(word, replacement)| { word, replacement }),
			},
		},
	)
	overrides = config.overrides.map(|item| { rule: item.rule, include: item.include, exclude: item.exclude, reason: item.reason, require_match: item.require_match })
	Stdout.line!(Json.to_str({ schema_version: 2.U64, root: Path.display(root), config: config_file, definition, path: options.path, excluded: !discovery_exclusions.is_empty(), within_roots, discovery_exclusions, roots: config.roots, exclude: config.exclude, max_warnings: config.max_warnings, test_roots: config.test_roots, test_main: config.test_main, profiles: config.profiles, rules: described, overrides }))
}
