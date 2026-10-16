import parser.RocSyntax

## Inventories static expects and compares runner evidence without executing it.
TestAccounting :: [].{

	## Source byte spans identify tests within an exact source revision.
	Test : { start : U64, end : U64, status : Str }

	## A runner must report its source and explicitly confirm completion.
	FileInventory : { path : Str, source : Str, tests : List(Test), completed : Bool }

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
		all_expects = parsed.nodes.keep_if(|item| item.kind == Expect)
		for test in tests {
			line = line_at(bytes, test.start)
			if all_expects.keep_if(|item| line_at(bytes, item.start) == line).len() != 1 {
				return Err("${path}:${line.to_str()}: multiple expect sites start on one static-test line; location evidence is ambiguous")
			}
		}
		Ok({ path, source: Str.from_utf8_lossy(bytes), tests, completed: False })
	}

	## Verify schema-1 JSON receipts. Unknown fields are extensible; duplicate keys
	## anywhere are rejected. Source identities prevent stale, not forged, receipts.
	verify : List(FileInventory), Str -> Try(List(Issue), Str)
	verify = |expected, report| {
		if expected.is_empty() or expected.fold(0.U64, |count, file| count + file.tests.len()) == 0 {
			return Err("test accounting requires a nonempty expected test inventory")
		}
		decoded : Try({ schema_version : U64, files : List(FileInventory) }, _)
		decoded = Json.parse(report)
		actual = decoded.map_err(|_| "invalid test report JSON or required field types")?
		unique_json_keys(report)?
		if actual.schema_version != 1 {
			return Err("unsupported test report schema_version")
		}
		var $expected_paths = []
		for file in expected {
			if $expected_paths.contains(file.path) {
				return Err("duplicate expected file ${file.path}")
			}
			$expected_paths = $expected_paths.append(file.path)
			unique_tests(file.tests).map_err(|_| "duplicate expected test identity in ${file.path}")?
		}
		var $issues = []
		var $actual_paths = []
		for file in actual.files {
			if $actual_paths.contains(file.path) {
				return Err("duplicate report file ${file.path}")
			}
			$actual_paths = $actual_paths.append(file.path)
			unique_tests(file.tests).map_err(|_| "duplicate report test identity in ${file.path}")?
			for test in file.tests {
				if !["passed", "failed", "skipped", "pending"].contains(test.status) {
					return Err("unknown report test status ${test.status}")
				}
				if test.start >= test.end or test.end > file.source.to_utf8().len() {
					return Err("invalid report test span in ${file.path}")
				}
			}
			wanted = match expected.find_first(|candidate| candidate.path == file.path) {
				Ok(value) => value
				Err(_) => {
					$issues = $issues.append(issue(file.path, 0, 0, "unexpected file in runner report"))
					{ ..file, tests: [] }
				}
			}
			if file.source != wanted.source {
				$issues = $issues.append(issue(file.path, 0, 0, "runner source does not match expected source"))
			}
			if !file.completed {
				$issues = $issues.append(issue(file.path, 0, 0, "runner did not confirm file completion"))
			}
			for test in file.tests {
				if !wanted.tests.any(|candidate| same_test(candidate, test)) {
					$issues = $issues.append(issue(file.path, test.start, test.end, "unexpected test identity"))
				} else if test.status != "passed" {
					$issues = $issues.append(issue(file.path, test.start, test.end, "test status is ${test.status}, expected passed"))
				}
			}
			for test in wanted.tests {
				if !file.tests.any(|candidate| same_test(candidate, test)) {
					$issues = $issues.append(issue(file.path, test.start, test.end, "expected test missing from runner report"))
				}
			}
		}
		for file in expected {
			if !$actual_paths.contains(file.path) {
				$issues = $issues.append(issue(file.path, 0, 0, "expected file missing from runner report"))
			}
		}
		Ok($issues)
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
		_ = summary_count(output)?
		var $locations = []
		for line in output.split_on("\n") {
			if line.starts_with("PASS: ") {
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
		var $issues = []
		for test in checked.tests {
			line = line_at(expected.source.to_utf8(), test.start)
			count = $locations.keep_if(|found| found == line).len()
			if count != 1 {
				$issues = $issues.append(issue(expected.path, test.start, test.end, "expected exactly one compiler PASS at line ${line.to_str()}, found ${count.to_str()}"))
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
			$tests = $tests.append({ start: child.start, end: child.end, status: "declared" })
		} else if child.kind == TypeDeclaration or child.kind == AssociatedBlock {
			$tests = $tests.concat(discover(parsed, child))
		}
	}
	$tests
}

same_test = |a, b| a.start == b.start and a.end == b.end

unique_tests : List(TestAccounting.Test) -> Try({}, Str)
unique_tests = |tests| {
	var $seen = []
	for test in tests {
		identity = (test.start, test.end)
		if $seen.contains(identity) {
			return Err("duplicate test")
		}
		$seen = $seen.append(identity)
	}
	Ok({})
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

# Json.parse validates grammar first. This scanner only tracks object scopes and
# decoded keys; string escapes and punctuation inside strings cannot confuse it.
unique_json_keys : Str -> Try({}, Str)
unique_json_keys = |source| {
	bytes = source.to_utf8()
	var $index = 0.U64
	var $objects = []
	while $index < bytes.len() {
		byte = bytes.get($index) ?? 0
		if byte == 123 {
			$objects = $objects.append([])
		} else if byte == 125 {
			$objects = $objects.drop_last(1)
		} else if byte == 34 {
			start = $index
			$index = $index + 1
			while $index < bytes.len() and (bytes.get($index) ?? 0) != 34 {
				if (bytes.get($index) ?? 0) == 92 {
					$index = $index + 1
				}
				$index = $index + 1
			}
			end = $index + 1
			var $look = end
			while $look < bytes.len() and [32.U8, 9, 10, 13].contains(bytes.get($look) ?? 0) {
				$look = $look + 1
			}
			if (bytes.get($look) ?? 0) == 58 {
				decoded : Try(Str, _)
				decoded = Json.parse(Str.from_utf8_lossy(bytes.sublist({ start, len: end - start })))
				key = decoded.map_err(|_| "invalid JSON key")?
				keys = $objects.last() ?? []
				if keys.contains(key) {
					return Err("duplicate JSON key ${key}")
				}
				$objects = $objects.drop_last(1).append(keys.append(key))
			}
		}
		$index = $index + 1
	}
	Ok({})
}

line_at = |bytes, offset| bytes.sublist({ start: 0, len: offset }).keep_if(|byte| byte == 10).len() + 1
