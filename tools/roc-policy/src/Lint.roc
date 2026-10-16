import parser.RocSyntax

Lint :: [].{
	Rule : { kind : [Dependency, CompilerPin, NoCompilerPin, ForbiddenImport], scope : Str, name : Str, value : Str }
	Config : { roots : List(Str), rules : List(Rule) }
	Issue : { rule : Str, message : Str, line : U64, column : U64 }

	safe_path = |path| !path.is_empty() and !path.starts_with("/") and !path.contains("\\") and path.split_on("/").all(|part| part != "" and part != "." and part != "..")

	config : Str -> Try(Config, Str)
	config = |source| {
		p = RocSyntax.parse_str(source)
		if !complete(p) {
			return Err("config must parse completely without diagnostics")
		}
		declarations = children(p, node(p, p.root))
		decl = match declarations {
			[one] => one
			_ => return Err("config requires exactly one top-level definition")
		}
		parts = children(p, decl)
		value = match parts {
			[binding, expression] if decl.kind == Declaration and text(p, binding) == "lint" => expression
			_ => return Err("config must be lint = { ... }")
		}
		fields = record(p, value, ["schema_version", "roots", "rules"])?
		version = required(fields, "schema_version")?
		if version.kind != NumberLiteral or text(p, version) != "1" {
			return Err("schema_version must be 1")
		}
		roots = string_list(p, required(fields, "roots")?)?
		if roots.is_empty() or !roots.all(safe_path) {
			return Err("roots must be nonempty safe relative paths")
		}
		rules_node = required(fields, "rules")?
		if rules_node.kind != ListLiteral {
			return Err("rules must be a list")
		}
		var $rules = []
		for item in children(p, rules_node) {
			fields_rule = record(p, item, ["kind", "scope", "name", "value"])?
			kind_node = required(fields_rule, "kind")?
			if kind_node.kind != Identifier {
				return Err("rule kind must be a supported literal tag")
			}
			kind = match text(p, kind_node) {
				"Dependency" => Dependency
				"CompilerPin" => CompilerPin
				"NoCompilerPin" => NoCompilerPin
				"ForbiddenImport" => ForbiddenImport
				_ => return Err("unknown rule kind")
			}
			scope = string(p, required(fields_rule, "scope")?)?
			if !safe_path(scope) {
				return Err("scope must be a safe path, matches that file or directory")
			}
			name = if kind == Dependency or kind == ForbiddenImport string(p, required(fields_rule, "name")?)? else "roc"
			value_text = if kind == Dependency or kind == CompilerPin string(p, required(fields_rule, "value")?)? else ""
			if name.is_empty() or ((kind == Dependency or kind == CompilerPin) and value_text.is_empty()) {
				return Err("rule name/value must not be empty")
			}
			if (kind == NoCompilerPin or kind == ForbiddenImport) and fields_rule.any(|(key, _)| key == "value") {
				return Err("this rule does not accept value")
			}
			if (kind == NoCompilerPin or kind == CompilerPin) and fields_rule.any(|(key, _)| key == "name") {
				return Err("compiler rules do not accept name")
			}
			$rules = $rules.append({ kind, scope, name, value: value_text })
		}
		if $rules.is_empty() {
			return Err("at least one rule is required")
		}
		Ok({ roots, rules: $rules })
	}

	check : Str, List(U8), List(Rule) -> List(Issue)
	check = |path, source, rules| {
		p = RocSyntax.parse_bytes(source)
		if !complete(p) {
			at = (p.diagnostics.first() ?? { code: UnexpectedToken, start: 0, end: 0 }).start
			return [issue(p, at, "syntax/incomplete", "analysis incomplete: ${Str.inspect(p.status)}")]
		}
		var $issues = []
		for rule in rules.keep_if(|rule| path == rule.scope or path.starts_with("${rule.scope}/")) {
			if rule.kind == ForbiddenImport {
				for imported in p.nodes.keep_if(|n| n.kind == ImportPath) {
					# The grammar accepts trivia between some path components.
					# Compare significant tokens, retaining the original span.
					spelling = Str.join_with(p.tokens.keep_if(|t| t.start >= imported.start and t.end <= imported.end and t.kind != Whitespace and t.kind != Comment).map(|t| Str.from_utf8_lossy(p.source.sublist({ start: t.start, len: t.end - t.start }))), "")
					if spelling == rule.name or spelling.starts_with("${rule.name}.") or spelling.starts_with("${rule.name}/") {
						$issues = $issues.append(issue(p, imported.start, "architecture/import-boundary", "import ${spelling} is forbidden in ${rule.scope}"))
					}
				}
			} else {
				headers = p.nodes.keep_if(|n| n.kind == Header)
				if !headers.is_empty() {
					fields = p.nodes.keep_if(|n| n.kind == DependencyField).keep_if(|n| text(p, children(p, n).first() ?? n) == rule.name)
					if rule.kind == NoCompilerPin {
						for field in fields {
							$issues = $issues.append(issue(p, field.start, "project/compiler-pin", "compiler pin forbidden in ${rule.scope}"))
						}
					} else {
						actual = fields.map(
							|field| {
								value = children(p, field).last() ?? field
								literal = if value.kind == PlatformReference children(p, value).first() ?? value else value
								string(p, literal)
							},
						)
						if actual != [Ok(rule.value)] {
							at = (fields.first() ?? (headers.first() ?? node(p, p.root))).start
							id = if rule.kind == Dependency "project/dependency" else "project/compiler-pin"
							$issues = $issues.append(issue(p, at, id, "expected exactly one ${rule.name}: ${Str.inspect(rule.value)}, found ${Str.inspect(actual)}"))
						}
					}
				}
			}
		}
		$issues
	}
}

complete = |p| p.status == Complete and p.consumed and p.diagnostics.is_empty()

node = |p, index| p.nodes.get(index) ?? { kind: ErrorNode, start: 0, end: 0, children_start: 0, children_len: 0 }

children = |p, n| p.children.sublist({ start: n.children_start, len: n.children_len }).map(|index| node(p, index))

text = |p, n| Str.from_utf8_lossy(p.source.sublist({ start: n.start, len: n.end - n.start }))

record = |p, n, allowed| {
	if n.kind != Record {
		return Err("expected a literal record")
	}
	var $fields = []
	for field in children(p, n) {
		(name, value) = match children(p, field) {
			[key_node, value_node] if field.kind == RecordField => (text(p, key_node), value_node)
			_ => return Err("record fields must be explicit name: value pairs")
		}
		if !allowed.contains(name) {
			return Err("unknown field ${name}")
		}
		if $fields.any(|(key, _)| key == name) {
			return Err("duplicate field ${name}")
		}
		$fields = $fields.append((name, value))
	}
	Ok($fields)
}

required = |fields, name| match fields.find_first(|(key, _)| key == name) {
	Ok((_, value)) => Ok(value)
	Err(_) => Err("missing field ${name}")
}

string_list = |p, n| {
	if n.kind != ListLiteral {
		return Err("expected a literal list")
	}
	var $out = []
	for item in children(p, n) {
		$out = $out.append(string(p, item)?)
	}
	Ok($out)
}

string = |p, n| {
	if n.kind != StringLiteral or children(p, n).any(|child| child.kind != StringSegment) {
		return Err("expected a non-interpolated single-line string")
	}
	bytes = text(p, n).to_utf8().drop_first(1).drop_last(1)
	var $out = []
	var $i = 0
	while $i < bytes.len() {
		b = bytes.get($i) ?? 0
		if b == '\\' {
			$i = $i + 1
			escaped = match bytes.get($i) ?? 0 {
				'n' => '\n'
				'r' => '\r'
				't' => '\t'
				'\\' => '\\'
				'"' => '"'
				'\'' => '\''
				'$' => '$'
				_ => return Err("unsupported string escape in prototype")
			}
			$out = $out.append(escaped)
		} else {
			$out = $out.append(b)
		}
		$i = $i + 1
	}
	Ok(Str.from_utf8_lossy($out))
}

issue = |p, at, rule, message| {
	prefix = p.source.sublist({ start: 0, len: at })
	lines = Str.from_utf8_lossy(prefix).split_on("\n")
	# Human columns count Unicode scalars, not UTF-8 bytes.
	column = (lines.last() ?? "").to_utf8().keep_if(|b| b < 128 or b >= 192).len() + 1
	{ rule, message, line: lines.len(), column }
}
