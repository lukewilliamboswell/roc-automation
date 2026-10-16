import parser.RocSyntax

## Bounded, inert constant data extraction. This is not Roc evaluation or typechecking.
ConstantData :: [].{
	Value := [Data({ start : U64, end : U64, kind : [String(Str), Integer(Str), Boolean(Bool), Tag(Str), Record(List((Str, Value))), ListValue(List(Value))] })].{
		is_eq : _
	}
	Error : { kind : Str, message : Str, start : U64, end : U64 }

	unwrap = |Data(value)| value

	evaluate : Str, Str -> Try(Value, Error)
	evaluate = |source, name| {
		if source.to_utf8().len() > 1048576 {
			return Err({ kind: "limit", message: "source exceeds 1 MiB", start: 0, end: 0 })
		}
		p = RocSyntax.parse_str(source)
		if p.status != Complete or !p.consumed or !p.diagnostics.is_empty() {
			d = p.diagnostics.first() ?? { code: UnexpectedToken, start: 0, end: 0 }
			return Err({ kind: "parse", message: "module must parse completely without diagnostics", start: d.start, end: d.end })
		}
		declarations = children(p, node(p, p.root)).keep_if(|n| n.kind == Declaration)
		if declarations.len() > 10000 {
			return Err({ kind: "limit", message: "too many top-level definitions", start: 0, end: 0 })
		}
		var $bindings = []
		for decl in declarations {
			match children(p, decl) {
				[binding, expression] if binding.kind == IdentifierPattern => {
					key = text(p, binding)
					if $bindings.any(|(old, _)| old == key) {
						return Err(error(binding, "duplicate", "duplicate top-level definition ${key}"))
					}
					$bindings = $bindings.append((key, expression))
				}
				_ => {}
			}
		}
		selected = match $bindings.find_first(|(key, _)| key == name) {
			Ok((_, n)) => n
			Err(_) => return Err({ kind: "unknown", message: "unknown definition ${name}", start: 0, end: 0 })
		}
		result = visit(p, $bindings, selected, [name], 0, { work: 0, size: 0 })?
		if result.budget.size > 1048576 {
			return Err(error(selected, "limit", "expanded constant exceeds 1 MiB budget"))
		}
		Ok(result.value)
	}
}

node = |p, index| p.nodes.get(index) ?? { kind: ErrorNode, start: 0, end: 0, children_start: 0, children_len: 0 }

children = |p, n| p.children.sublist({ start: n.children_start, len: n.children_len }).map(|index| node(p, index))

text = |p, n| Str.from_utf8_lossy(p.source.sublist({ start: n.start, len: n.end - n.start }))

error = |n, kind, message| { kind, message, start: n.start, end: n.end }

visit : RocSyntax.Parsed, List((Str, RocSyntax.Node)), RocSyntax.Node, List(Str), U64, { work : U64, size : U64 } -> Try({ value : ConstantData.Value, budget : { work : U64, size : U64 } }, ConstantData.Error)
visit = |p, bindings, n, active, depth, budget| {
	if depth >= 64 or budget.work >= 10000 or budget.size >= 1048576 {
		return Err(error(n, "limit", "constant evaluation resource limit exceeded"))
	}
	next = { work: budget.work + 1, size: budget.size + 32 }
	parts = children(p, n)
	spelling = text(p, n)
	kind = match n.kind {
		StringLiteral => {
			if parts.any(|child| child.kind != StringSegment) {
				return Err(error(n, "unsupported", "string interpolation is unsupported"))
			}
			String(decode_string(spelling, n)?)
		}
		NumberLiteral => {
			digits = if spelling.starts_with("-") spelling.to_utf8().drop_first(1) else spelling.to_utf8()
			if digits.is_empty() or !digits.all(|b| b >= '0' and b <= '9') {
				return Err(error(n, "unsupported", "only decimal integer literals are supported"))
			}
			Integer(spelling)
		}
		Unary => {
			if !spelling.starts_with("-") {
				return Err(error(n, "unsupported", "only negative decimal integer unary literals are supported"))
			}
			literal = match parts {
				[one] if one.kind == NumberLiteral => text(p, one)
				_ => return Err(error(n, "unsupported", "expected negative decimal integer literal"))
			}
			if !literal.to_utf8().all(|b| b >= '0' and b <= '9') {
				return Err(error(n, "unsupported", "expected negative decimal integer literal"))
			}
			Integer("-${literal}")
		}
		Identifier => {
			if spelling == "True" or spelling == "False" {
				Boolean(spelling == "True")
			} else if (spelling.to_utf8().first() ?? 0) >= 'A' and (spelling.to_utf8().first() ?? 0) <= 'Z' {
				Tag(spelling)
			} else {
				if active.contains(spelling) {
					return Err(error(n, "cycle", "cyclic constant reference ${spelling}"))
				}
				target = match bindings.find_first(|(key, _)| key == spelling) {
					Ok((_, value)) => value
					Err(_) => return Err(error(n, "unknown", "unknown constant ${spelling}"))
				}
				return visit(p, bindings, target, active.append(spelling), depth + 1, next)
			}
		}
		Parenthesized => {
			match parts {
				[one] => return visit(p, bindings, one, active, depth + 1, next)
				_ => return Err(error(n, "unsupported", "invalid parenthesized constant"))
			}
		}
		FieldAccess => {
			(base, field) = match parts {
				[a, b] => (a, b)
				_ => return Err(error(n, "unsupported", "invalid field access"))
			}
			resolved = visit(p, bindings, base, active, depth + 1, next)?
			value = match ConstantData.unwrap(resolved.value).kind {
				Record(fields) => match fields.find_first(|(key, _)| key == text(p, field)) {
					Ok((_, v)) => v
					Err(_) => return Err(error(field, "unknown", "unknown record field ${text(p, field)}"))
				}
				_ => return Err(error(base, "type", "field access requires a record"))
			}
			return Ok({ value, budget: resolved.budget })
		}
		Record => {
			var $fields = []
			var $budget = next
			for field in parts {
				(key, expression) = match children(p, field) {
					[k, v] if field.kind == RecordField => (text(p, k), v)
					[k] if field.kind == RecordField => (text(p, k), k)
					_ => return Err(error(field, "unsupported", "only plain record fields and puns are supported"))
				}
				if $fields.any(|(old, _)| old == key) {
					return Err(error(field, "duplicate", "duplicate record field ${key}"))
				}
				result = visit(p, bindings, expression, active, depth + 1, $budget)?
				$budget = result.budget
				$fields = $fields.append((key, result.value))
			}
			return Ok({ value: Data({ start: n.start, end: n.end, kind: Record($fields) }), budget: $budget })
		}
		ListLiteral => {
			var $items = []
			var $budget = next
			for item in parts {
				result = visit(p, bindings, item, active, depth + 1, $budget)?
				$budget = result.budget
				$items = $items.append(result.value)
			}
			return Ok({ value: Data({ start: n.start, end: n.end, kind: ListValue($items) }), budget: $budget })
		}
		_ => return Err(error(n, "unsupported", "unsupported constant expression ${Str.inspect(n.kind)}"))
	}
	Ok({ value: Data({ start: n.start, end: n.end, kind }), budget: { work: next.work, size: next.size + spelling.to_utf8().len() } })
}

decode_string = |spelling, n| {
	bytes = spelling.to_utf8().drop_first(1).drop_last(1)
	var $out = []
	var $i = 0
	while $i < bytes.len() {
		b = bytes.get($i) ?? 0
		if b == '\\' {
			$i = $i + 1
			if (bytes.get($i) ?? 0) == 'u' {
				$i = $i + 2
				var $scalar = 0.U64
				while $i < bytes.len() and (bytes.get($i) ?? 0) != ')' {
					c = bytes.get($i) ?? 0
					digit = if c >= '0' and c <= '9' U8.to_u64(c - '0') else if c >= 'a' and c <= 'f' U8.to_u64(c - 'a') + 10 else U8.to_u64(c - 'A') + 10
					$scalar = $scalar * 16 + digit
					$i = $i + 1
				}
				$out = $out.concat(utf8($scalar))
			} else {
				escaped = match bytes.get($i) ?? 0 {
					'n' => '\n'
					'r' => '\r'
					't' => '\t'
					'\\' => '\\'
					'"' => '"'
					'\'' => '\''
					'$' => '$'
					_ => return Err(error(n, "unsupported", "unsupported string escape"))
				}
				$out = $out.append(escaped)
			}
		} else {
			$out = $out.append(b)
		}
		$i = $i + 1
	}
	Ok(Str.from_utf8_lossy($out))
}

# The syntax parser has validated the Unicode scalar and escape delimiters.
utf8 = |scalar| {
	if scalar < 128 {
		[U64.to_u8_wrap(scalar)]
	} else if scalar < 2048 {
		[U64.to_u8_wrap(192 + scalar // 64), U64.to_u8_wrap(128 + scalar % 64)]
	} else if scalar < 65536 {
		[U64.to_u8_wrap(224 + scalar // 4096), U64.to_u8_wrap(128 + (scalar // 64) % 64), U64.to_u8_wrap(128 + scalar % 64)]
	} else {
		[U64.to_u8_wrap(240 + scalar // 262144), U64.to_u8_wrap(128 + (scalar // 4096) % 64), U64.to_u8_wrap(128 + (scalar // 64) % 64), U64.to_u8_wrap(128 + scalar % 64)]
	}
}
