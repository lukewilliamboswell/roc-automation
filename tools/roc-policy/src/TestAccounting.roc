import parser.RocSyntax

## Inventories static expects and compares runner evidence without executing it.
TestAccounting :: [].{

	## Source byte spans identify tests within an exact source revision.
	Test : { start : U64, end : U64 }

	## Exact inventoried source and its static test declarations. No execution results.
	FileInventory : { path : Str, source : Str, tests : List(Test) }

	## A source-linked accounting discrepancy.
	Issue : { path : Str, start : U64, end : U64, rule : Str, message : Str }

	## Discover static file and associated-block expects. Local inline assertions
	## are valid but are not separate required test entries.
	inventory : Str, List(U8) -> Try(FileInventory, Str)
	inventory = |path, bytes| {
		parsed = RocSyntax.parse_bytes(bytes)
		if parsed.status != Complete or !parsed.consumed or !parsed.diagnostics.is_empty() {
			return Err("${path}: test inventory requires a complete parse")
		}
		tests = discover(parsed, node(parsed, parsed.root))
		all_expects = with_lines(bytes, parsed.nodes.keep_if(|item| item.kind == Expect).map(|item| { start: item.start, end: item.end }))
		var $index = 0.U64
		for test in with_lines(bytes, tests) {
			while $index < all_expects.len() and (all_expects.get($index) ?? test).start < test.start {
				$index = $index + 1
			}
			before = if $index == 0 False else (all_expects.get($index - 1) ?? { ..test, line: 0 }).line == test.line
			after = (all_expects.get($index + 1) ?? { ..test, line: 0 }).line == test.line
			if before or after {
				return Err("${path}:${test.line.to_str()}: multiple expect sites start on one static-test line; location evidence is ambiguous")
			}
		}
		Ok({ path, source: Str.from_utf8_lossy(bytes), tests })
	}

	## Match selected static sites against compiler --verbose PASS locations.
	## Caller must enforce successful compiler exit and source stability. This is
	## line evidence from human output, not span receipts or compiler provenance.
	verify_locations : FileInventory, Str, Str -> Try(List(Issue), Str)
	verify_locations = |expected, absolute_path, output| {
		if !absolute_path.starts_with("/") or absolute_path.contains("\n") or absolute_path.contains("\r") {
			return Err("location matching requires a single-line absolute source path")
		}
		checked = inventory(expected.path, expected.source.to_utf8())?
		if checked.tests.is_empty() {
			return Err("location accounting requires a nonempty static test inventory")
		}
		if checked.tests != expected.tests {
			return Err("expected inventory does not match its source")
		}
		reported_total = summary_count(output)?
		var $locations = []
		var $total = 0.U64
		for line in output.split_on("\n") {
			if line.starts_with("PASS: ") {
				$total = $total + 1
				location = line.drop_prefix("PASS: ")
				parts = location.split_on(":")
				line_text = parts.last() ?? ""
				number = decimal(line_text)?
				if number == 0 {
					return Err("compiler PASS location must have a positive line")
				}
				path = Str.join_with(parts.drop_last(1), ":")
				if !path.starts_with("/") {
					return Err("compiler PASS location must use an absolute path")
				}
				if path == absolute_path {
					$locations = $locations.append(number)
				}
			}
		}
		if $total != reported_total {
			return Err("compiler PASS records do not match the summary total")
		}
		var $issues = []
		locations = $locations.sort_with(compare)
		var $index = 0.U64
		for test in with_lines(expected.source.to_utf8(), checked.tests) {
			while $index < locations.len() and (locations.get($index) ?? 0) < test.line {
				$index = $index + 1
			}
			var $count = 0.U64
			while $index < locations.len() and (locations.get($index) ?? 0) == test.line {
				$count = $count + 1
				$index = $index + 1
			}
			if $count != 1 {
				$issues = $issues.append(issue(expected.path, test.start, test.end, "expected exactly one compiler PASS at line ${test.line.to_str()}, found ${$count.to_str()}"))
			}
		}
		Ok($issues)
	}

	## Parse one uncolored Roc success summary, including ordinary cached results.
	## Callers must independently enforce the compiler process exit status.
	summary_count : Str -> Try(U64, Str)
	summary_count = |output| {
		if output.contains("\u(001b)") or output.split_on("\n").any(|line| line.starts_with("FAIL: ") or line.ends_with(" failed")) {
			return Err("test summary must be uncolored and successful")
		}
		lines = output.split_on("\n").keep_if(|line| !line.trim().is_empty())
		summaries = lines.keep_if(|line| line.starts_with("All ("))
		line = match summaries {
			[one] => one
			_ => return Err("expected exactly one Roc success summary")
		}
		uncached_line = if line.ends_with(" (cached)") line.drop_suffix(" (cached)") else line
		parts = uncached_line.drop_prefix("All (").split_on(") tests passed in ")
		(count_text, duration) = match parts {
			[count, elapsed] => (count, elapsed)
			_ => return Err("invalid Roc success summary")
		}
		count = decimal(count_text)?
		elapsed = if duration.ends_with(" ms.") duration.drop_suffix(" ms.") else if duration.ends_with(" s.") duration.drop_suffix(" s.") else return Err("invalid summary duration unit")
		components = elapsed.split_on(".")
		if components.is_empty() or components.len() > 2 or !components.all(|part| !part.is_empty() and part.to_utf8().all(|byte| byte >= 48 and byte <= 57)) {
			return Err("invalid summary duration")
		}
		Ok(count)
	}
}

issue = |path, start, end, message| { path, start, end, rule: "tests/accounting", message }

node = |parsed, index| parsed.nodes.get(index) ?? { kind: ErrorNode, start: 0, end: 0, children_start: 0, children_len: 0 }

children = |parsed, parent| parsed.children.sublist({ start: parent.children_start, len: parent.children_len }).map(|index| node(parsed, index))

discover : RocSyntax.Parsed, RocSyntax.Node -> List(TestAccounting.Test)
discover = |parsed, parent| {
	var $tests = []
	for child in children(parsed, parent) {
		if child.kind == Expect {
			$tests = $tests.append({ start: child.start, end: child.end })
		} else if child.kind == TypeDeclaration or child.kind == AssociatedBlock {
			$tests = $tests.concat(discover(parsed, child))
		}
	}
	$tests
}

decimal : Str -> Try(U64, Str)
decimal = |text| {
	if text.is_empty() {
		return Err("empty test count")
	}
	var $number = 0.U64
	for byte in text.to_utf8() {
		if byte < 48 or byte > 57 {
			return Err("invalid test count")
		}
		digit = byte.to_u64() - 48
		if $number > (18446744073709551615.U64 - digit) // 10 {
			return Err("test count overflows")
		}
		$number = $number * 10 + digit
	}
	Ok($number)
}

# Walk source bytes once in source order, avoiding a prefix scan for every pair
# of expectation nodes. Runtime assertions are included when checking ambiguity.
with_lines : List(U8), List(TestAccounting.Test) -> List({ start : U64, end : U64, line : U64 })
with_lines = |bytes, tests| {
	var $cursor = 0.U64
	var $line = 1.U64
	var $out = []
	for test in tests.sort_with(|a, b| compare(a.start, b.start)) {
		while $cursor < test.start {
			if (bytes.get($cursor) ?? 0) == 10 {
				$line = $line + 1
			}
			$cursor = $cursor + 1
		}
		$out = $out.append({ start: test.start, end: test.end, line: $line })
	}
	$out
}

compare = |a, b| if a < b Before else if a > b After else Same
