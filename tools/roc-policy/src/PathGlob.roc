## Root-relative globs with bounded dynamic-programming matching. A double star
## is a complete component and matches zero or more path components. Single
## stars and question marks match Unicode scalars within one component.
PathGlob :: [].{

	## Reject unsupported syntax rather than interpreting it as literal text.
	validate : Str -> Try({}, Str)
	validate = |pattern| {
		validate_parts(pattern)?
		if pattern.to_utf8().any(|b| b == '[' or b == ']' or b == '{' or b == '}' or b == '!' or b == '\\') {
			return Err("unsupported glob syntax")
		}
		if pattern.split_on("/").any(|part| part.contains("**") and part != "**") {
			return Err("double star must be an entire path component")
		}
		Ok({})
	}

	## Invalid patterns and unsafe paths never match. Directory descendants must
	## be explicit: dir is exact; dir/** also matches dir itself.
	matches : Str, Str -> Bool
	matches = |pattern, path| {
		if validate(pattern).is_err() or validate_parts(path).is_err() {
			return False
		}
		parts = path.split_on("/")
		var $row = [True].concat(parts.map(|_| False))
		for component in pattern.split_on("/") {
			recursive = component == "**"
			var $next = [recursive and ($row.first() ?? False)]
			for part in parts.map_with_index(|text, index| (text, index)) {
				(text, index) = part
				matched = if recursive {
					($row.get(index + 1) ?? False) or ($next.get(index) ?? False)
				} else {
					($row.get(index) ?? False) and component_matches(component, text)
				}
				$next = $next.append(matched)
			}
			$row = $next
		}
		$row.last() ?? False
	}

	## Match any validated glob, using the same exact root-relative semantics.
	any_match : List(Str), Str -> Bool
	any_match = |patterns, path| patterns.any(|pattern| matches(pattern, path))
}

validate_parts : Str -> Try({}, Str)
validate_parts = |path| {
	bytes = path.to_utf8()
	if bytes.is_empty() or bytes.len() > 4096 {
		return Err("path or pattern must contain 1 to 4096 UTF-8 bytes")
	}
	if bytes.any(|b| b < 32 or b == 127 or b == '\\') or bytes.map_with_index(|b, index| b == 194 and (bytes.get(index + 1) ?? 0) >= 128 and (bytes.get(index + 1) ?? 0) <= 159).any(|control| control) {
		return Err("path or pattern contains a control character or backslash")
	}
	parts = path.split_on("/")
	if parts.len() > 128 or parts.any(|part| part.is_empty() or part == "." or part == "..") {
		return Err("path or pattern must use at most 128 nonempty relative components without dot segments")
	}
	Ok({})
}

# Split valid UTF-8 at scalar boundaries; '?' must not count bytes.
scalars : Str -> List(List(U8))
scalars = |text| {
	var $out = []
	var $current = []
	for b in text.to_utf8() {
		if b < 128 or b >= 192 {
			if !$current.is_empty() {
				$out = $out.append($current)
			}
			$current = [b]
		} else {
			$current = $current.append(b)
		}
	}
	if !$current.is_empty() {
		$out = $out.append($current)
	}
	$out
}

component_matches : Str, Str -> Bool
component_matches = |pattern, text| {
	characters = scalars(text)
	var $row = [True].concat(characters.map(|_| False))
	for token in scalars(pattern) {
		star = token == ['*']
		var $next = [star and ($row.first() ?? False)]
		for item in characters.map_with_index(|character, index| (character, index)) {
			(character, index) = item
			matched = if star {
				($row.get(index + 1) ?? False) or ($next.get(index) ?? False)
			} else {
				($row.get(index) ?? False) and (token == ['?'] or token == character)
			}
			$next = $next.append(matched)
		}
		$row = $next
	}
	$row.last() ?? False
}
