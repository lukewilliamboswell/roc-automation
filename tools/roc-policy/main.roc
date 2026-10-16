app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
	parser: "https://github.com/lukewilliamboswell/roc-parser/releases/download/2.1.0-rc.1/6dvNG3Q2mx53i4YPJN2ZGK98WdXnBMMuceAmDsCq7BEd.tar.zst",
}

import cli.OsStr
import cli.Path
import cli.Stdout
import cli.Stderr
import src/Lint

main! = |args| {
	words = args.map(OsStr.display)
	if words == ["--help"] {
		return Stdout.line!("roc-policy <root> <config-relative-to-root>\nRead-only source policies; config is literal Roc data. Exit: 0 pass, 1 findings, 2 config/path-policy error; I/O failures also fail.")
	}
	(root_arg, config_arg) = match words {
		[root, config] => (root, config)
		_ => return fail!("usage: roc-policy <root> <config-relative-to-root>")
	}
	if !Lint.safe_path(config_arg) {
		return fail!("config must be a relative path without dot segments")
	}
	root = Path.canonicalize!(Path.utf8(root_arg))?
	root_text = Path.display(root)
	config_path = Path.canonicalize!(Path.join(root, config_arg))?
	if Path.display(config_path) != "${root_text}/${config_arg}" {
		return fail!("config escapes root or traverses a symlink")
	}
	if !Path.is_file!(config_path)? {
		return fail!("config must be a regular file")
	}
	config = match Lint.config(Path.read_utf8!(config_path)?) {
		Ok(value) => value
		Err(message) => return fail!(message)
	}
	var $queue = config.roots
	var $seen = []
	var $count = 0.U64
	var $findings = 0.U64
	while !$queue.is_empty() {
		relative = $queue.first() ?? ""
		$queue = $queue.drop_first(1)
		path = Path.canonicalize!(Path.join(root, relative))?
		absolute = Path.display(path)
		if absolute != "${root_text}/${relative}" {
			return fail!("${relative}: path escapes root or traverses a symlink")
		}
		if !$seen.contains(absolute) {
			$seen = $seen.append(absolute)
			if Path.is_dir!(path)? {
				entries = Path.list!(path)?.map(|entry| Path.display(entry)).sort_with(|a, b| compare(a, b))
				for entry in entries {
					name = entry.split_on("/").last() ?? ""
					$queue = $queue.append("${relative}/${name}")
				}
			} else if relative.ends_with(".roc") {
				if !Path.is_file!(path)? {
					return fail!("${relative}: source must be a regular file")
				}
				$count = $count + 1
				issues = Lint.check(relative, Path.read_bytes!(path)?, config.rules)
				for issue in issues {
					Stdout.line!("${relative}:${issue.line.to_str()}:${issue.column.to_str()}: ${issue.rule}: ${issue.message}")?
				}
				$findings = $findings + issues.len()
			}
		}
	}
	if $count == 0 {
		return fail!("no Roc files selected")
	}
	Stdout.line!("${$count.to_str()} files, ${$findings.to_str()} findings")?
	if $findings == 0 Ok({}) else Err(Exit(1))
}

fail! = |message| {
	Stderr.line!(message)?
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
