import parser.RocSyntax

## Checks source-declared public definitions without resolving imported symbols.
PublicDocs :: [].{

	## A missing-documentation finding or incomplete-analysis byte span.
	Issue : { rule : Str, message : Str, start : U64, end : U64 }

	## A path is necessary: headerless type modules expose the filename's type.
	check : Str, RocSyntax.Parsed -> List(Issue)
	check = |path, parsed| {
		if parsed.status != Complete or !parsed.consumed or !parsed.diagnostics.is_empty() {
			return [problem(0, 0, "documentation/incomplete", "documentation analysis requires a complete parse")]
		}
		items = children(parsed, node(parsed, parsed.root))
		headers = items.keep_if(|item| item.kind == Header)
		if headers.is_empty() {
			filename = path.split_on("/").last() ?? ""
			name = filename.drop_suffix(".roc")
			public_items = items.keep_if(|item| item.kind == TypeDeclaration and declaration_name(parsed, item) == name)
			if public_items.is_empty() {
				first = name.to_utf8().first() ?? 0
				if first >= 65 and first <= 90 {
					return [problem(0, 0, "documentation/incomplete", "type module ${name} has no matching top-level type declaration")]
				}
				return []
			}
			return inspect_items(parsed, public_items)
		}
		header = headers.first() ?? node(parsed, parsed.root)
		kind = parsed.tokens.keep_if(|token| token.start >= header.start and token.end <= header.end and token.kind != Whitespace and token.kind != Comment).first()
		package_header = match kind {
			Ok(token) => text(parsed, token) == "package"
			Err(_) => False
		}
		var $issues = []
		for exposed in children(parsed, header).keep_if(|item| item.kind == ExposedItem or item.kind == ExposedWildcard) {
			name_node = children(parsed, exposed).first() ?? exposed
			name = text(parsed, name_node)
			if exposed.kind == ExposedWildcard or name_node.kind == FieldAccess {
				$issues = $issues.append(problem(exposed.start, exposed.end, "documentation/incomplete", "cannot establish documentation of reexport ${name} from this file"))
			} else {
				# Package uppercase exposures name modules in other files, not local definitions.
				first = name.to_utf8().first() ?? 0
				if !(package_header and first >= 65 and first <= 90) {
					definitions = items.keep_if(|item| is_definition(item) and declaration_name(parsed, item) == name)
					if definitions.is_empty() {
						$issues = $issues.append(problem(exposed.start, exposed.end, "documentation/incomplete", "exposed name ${name} has no local declaration; imported documentation cannot be checked"))
					} else {
						$issues = $issues.concat(inspect_items(parsed, definitions))
					}
				}
			}
		}
		$issues
	}
}

problem = |start, end, rule, message| { start, end, rule, message }

node = |parsed, index| parsed.nodes.get(index) ?? { kind: ErrorNode, start: 0, end: 0, children_start: 0, children_len: 0 }

children = |parsed, parent| parsed.children.sublist({ start: parent.children_start, len: parent.children_len }).map(|index| node(parsed, index))

text = |parsed, span| Str.from_utf8_lossy(parsed.source.sublist({ start: span.start, len: span.end - span.start }))

is_definition = |item| item.kind == Declaration or item.kind == TypeAnnotation or item.kind == TypeDeclaration

declaration_name = |parsed, item| {
	binding = children(parsed, item).first() ?? item
	if binding.kind == TypeHeader {
		text(parsed, children(parsed, binding).first() ?? binding)
	} else if binding.kind == IdentifierPattern or binding.kind == Identifier {
		text(parsed, binding)
	} else {
		""
	}
}

inspect_items : RocSyntax.Parsed, List(RocSyntax.Node) -> List(PublicDocs.Issue)
inspect_items = |parsed, items| {
	var $issues = []
	var $seen = []
	for item in items.keep_if(is_definition) {
		name = declaration_name(parsed, item)
		if name.is_empty() {
			$issues = $issues.append(problem(item.start, item.end, "documentation/incomplete", "public binding pattern is unsupported"))
		} else if !$seen.contains(name) {
			$seen = $seen.append(name)
			group = items.keep_if(|candidate| is_definition(candidate) and declaration_name(parsed, candidate) == name)
			if !group.any(|candidate| documented(parsed, candidate.start)) {
				$issues = $issues.append(problem(item.start, item.start + name.to_utf8().len(), "documentation/public", "public definition ${name} requires nonempty ## documentation"))
			}
			for declaration in group.keep_if(|candidate| candidate.kind == TypeDeclaration) {
				for block in children(parsed, declaration).keep_if(|child| child.kind == AssociatedBlock) {
					$issues = $issues.concat(inspect_items(parsed, children(parsed, block)))
				}
			}
		}
	}
	$issues
}

# Only contiguous preceding ## comment lines attach. A blank line, ordinary
# comment or significant token ends attachment. Comment-looking strings cannot.
documented = |parsed, start| {
	before = parsed.tokens.keep_if(|token| token.end <= start and token.kind != EndOfFile)
	var $index = before.len()
	var $nonempty = False
	while $index > 0 {
		$index = $index - 1
		token = before.get($index) ?? { kind: EndOfFile, start: 0, end: 0 }
		spelling = text(parsed, token)
		if token.kind == Whitespace {
			if spelling.to_utf8().keep_if(|byte| byte == 10).len() > 1 {
				return $nonempty
			}
		} else if token.kind == Comment and spelling.starts_with("##") and own_line(parsed, token.start) {
			body = Str.from_utf8_lossy(spelling.to_utf8().drop_first(2)).trim()
			$nonempty = $nonempty or !body.is_empty()
		} else {
			return $nonempty
		}
	}
	$nonempty
}

# A trailing comment belongs to preceding code, not the next declaration.
own_line = |parsed, start| {
	var $index = start
	while $index > 0 {
		$index = $index - 1
		byte = parsed.source.get($index) ?? 0
		if byte == 10 {
			return True
		}
		if byte != 32 and byte != 9 and byte != 13 {
			return False
		}
	}
	True
}
