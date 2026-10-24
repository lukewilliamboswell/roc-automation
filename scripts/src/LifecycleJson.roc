# Adapted from roc-blueprint 0.8.0 blueprint-core/JsonData.roc.
# https://github.com/lukewilliamboswell/roc-blueprint/blob/0.8.0/blueprint-core/JsonData.roc
# Retains duplicate-key and Unicode rejection
# Lifecycle limits are tighter.
LifecycleJson := [Object(List({ name : Str, value : LifecycleJson })), Array(List(LifecycleJson)), String(Str), Number(Str), Boolean(Bool), Null].{
	Error : [InvalidJson(Str), LimitExceeded(Str)]
	is_eq : _
	Rules : { max_items : U64, duplicates : [Reject, CollapseIdentical] }
	message = |error| match error {
		InvalidJson(text) => text
		LimitExceeded(text) => text
	}
	decode : Str -> Try(LifecycleJson, Error)
	decode = |text| decode_limit(text, 65536)
	decode_limit : Str, U64 -> Try(LifecycleJson, Error)
	decode_limit = |text, max_bytes| {
		if text.to_utf8().len() > max_bytes {
			return Err(LimitExceeded("lifecycle JSON exceeds byte bound"))
		}
		parsed = parse(text.to_utf8(), 0, 0, { max_items: 4096, duplicates: Reject })?
		if whitespace(text.to_utf8(), parsed.next) != text.to_utf8().len() {
			return Err(InvalidJson("trailing JSON data"))
		}
		Ok(parsed.value)
	}
	object : LifecycleJson, List(Str) -> Try({}, Str)
	object = |value, keys| match value {
		Object(fields) if fields.len() == keys.len() and fields.all(|field| keys.contains(field.name)) => Ok({})
		_ => Err("JSON object has missing or unknown fields")
	}
	field : LifecycleJson, Str -> Try(LifecycleJson, Str)
	field = |value, name| match value {
		Object(fields) => fields.find_first(|field| field.name == name).map_ok(|field| field.value).map_err(|_| "missing JSON field ${name}")
		_ => Err("expected JSON object")
	}
	array : LifecycleJson -> Try(List(LifecycleJson), Str)
	array = |value| match value {
		Array(items) => Ok(items)
		_ => Err("expected JSON array")
	}
}

Parsed : { value : LifecycleJson, next : U64 }

parse : List(U8), U64, U64, LifecycleJson.Rules -> Try(Parsed, LifecycleJson.Error)
parse = |bytes, offset, depth, rules| {
	if depth > 32 {
		return Err(LimitExceeded("JSON exceeds 32 levels"))
	}
	i = whitespace(bytes, offset)
	match bytes.get(i) {
		Ok('"') => {
			parsed = read_string(bytes, i + 1)?
			Ok({ value: String(parsed.text), next: parsed.next })
		}
		Ok('{') => {
			var $fields = []
			var $next = whitespace(bytes, i + 1)
			if bytes.get($next) == Ok('}') {
				return Ok({ value: Object([]), next: $next + 1 })
			}
			while Bool.True {
				if $fields.len() >= rules.max_items {
					return Err(LimitExceeded("JSON object exceeds ${rules.max_items.to_str()} fields"))
				}
				if bytes.get($next) != Ok('"') {
					return Err(InvalidJson("expected JSON object key"))
				}
				key = read_string(bytes, $next + 1)?
				colon = whitespace(bytes, key.next)
				if bytes.get(colon) != Ok(':') {
					return Err(InvalidJson("expected colon in JSON"))
				}
				item = parse(bytes, colon + 1, depth + 1, rules)?
				match ($fields.find_first(|f| f.name == key.text), rules.duplicates) {
					(Err(_), _) => {
						$fields = $fields.append({ name: key.text, value: item.value })
					}
					(Ok(earlier), CollapseIdentical) if earlier.value == item.value => {}
					_ => return Err(InvalidJson("duplicate JSON key: ${key.text}"))
				}
				$next = whitespace(bytes, item.next)
				if bytes.get($next) == Ok('}') {
					return Ok({ value: Object($fields), next: $next + 1 })
				}
				if bytes.get($next) != Ok(',') {
					return Err(InvalidJson("expected comma in JSON object"))
				}
				$next = whitespace(bytes, $next + 1)
			}
		}
		Ok('[') => {
			var $items = []
			var $next = whitespace(bytes, i + 1)
			if bytes.get($next) == Ok(']') {
				return Ok({ value: Array([]), next: $next + 1 })
			}
			while Bool.True {
				if $items.len() >= rules.max_items {
					return Err(LimitExceeded("JSON array exceeds ${rules.max_items.to_str()} items"))
				}
				item = parse(bytes, $next, depth + 1, rules)?
				$items = $items.append(item.value)
				$next = whitespace(bytes, item.next)
				if bytes.get($next) == Ok(']') {
					return Ok({ value: Array($items), next: $next + 1 })
				}
				if bytes.get($next) != Ok(',') {
					return Err(InvalidJson("expected comma in JSON array"))
				}
				$next = whitespace(bytes, $next + 1)
			}
		}
		Ok('t') => literal(bytes, i, "true", Boolean(Bool.True))
		Ok('f') => literal(bytes, i, "false", Boolean(Bool.False))
		Ok('n') => literal(bytes, i, "null", Null)
		Ok(_) => number(bytes, i)
		Err(_) => Err(InvalidJson("unexpected end of JSON"))
	}
}

literal : List(U8), U64, Str, LifecycleJson -> Try(Parsed, LifecycleJson.Error)
literal = |bytes, i, text, value| {
	if bytes.drop_first(i).take_first(text.to_utf8().len()) == text.to_utf8() {
		Ok({ value, next: i + text.to_utf8().len() })
	} else Err(InvalidJson("invalid JSON literal"))
}

whitespace : List(U8), U64 -> U64
whitespace = |bytes, offset| {
	var $i = offset
	while match bytes.get($i) {
		Ok(byte) => [' ', '\n', '\r', '\t'].contains(byte)
		Err(_) => Bool.False
	} {
		$i = $i + 1
	}
	$i
}

digit : U8 -> Bool
digit = |byte| byte >= '0' and byte <= '9'

number : List(U8), U64 -> Try(Parsed, LifecycleJson.Error)
number = |bytes, start| {
	var $i = start
	if bytes.get($i) == Ok('-') {
		$i = $i + 1
	}
	if bytes.get($i) == Ok('0') {
		$i = $i + 1
	} else {
		if !digit(bytes.get($i) ?? 0) {
			return Err(InvalidJson("invalid JSON number"))
		}
		while digit(bytes.get($i) ?? 0) {
			$i = $i + 1
		}
	}
	if bytes.get($i) == Ok('.') {
		$i = $i + 1
		if !digit(bytes.get($i) ?? 0) {
			return Err(InvalidJson("invalid JSON fraction"))
		}
		while digit(bytes.get($i) ?? 0) {
			$i = $i + 1
		}
	}
	if ['e', 'E'].contains(bytes.get($i) ?? 0) {
		$i = $i + 1
		if ['+', '-'].contains(bytes.get($i) ?? 0) {
			$i = $i + 1
		}
		if !digit(bytes.get($i) ?? 0) {
			return Err(InvalidJson("invalid JSON exponent"))
		}
		while digit(bytes.get($i) ?? 0) {
			$i = $i + 1
		}
	}
	text = Str.from_utf8(bytes.drop_first(start).take_first($i - start))
		.map_err(|_| InvalidJson("invalid UTF-8 number"))?
	Ok({ value: Number(text), next: $i })
}

read_string : List(U8), U64 -> Try({ text : Str, next : U64 }, LifecycleJson.Error)
read_string = |bytes, offset| {
	var $i = offset
	var $out = []
	while $i < bytes.len() {
		byte = bytes.get($i) ?? 0
		$i = $i + 1
		if byte == '"' {
			text = Str.from_utf8($out).map_err(|_| InvalidJson("invalid UTF-8 JSON string"))?
			return Ok({ text, next: $i })
		} else if byte == '\\' {
			escaped = bytes.get($i).map_err(|_| InvalidJson("unfinished JSON escape"))?
			$i = $i + 1
			match escaped {
				'"' => {
					$out = $out.append('"')
				}
				'\\' => {
					$out = $out.append('\\')
				}
				'/' => {
					$out = $out.append('/')
				}
				'b' => {
					$out = $out.append(8)
				}
				'f' => {
					$out = $out.append(12)
				}
				'n' => {
					$out = $out.append('\n')
				}
				'r' => {
					$out = $out.append('\r')
				}
				't' => {
					$out = $out.append('\t')
				}
				'u' => {
					var $point = hex4(bytes, $i)?
					$i = $i + 4
					if $point >= 0xD800 and $point <= 0xDBFF {
						if bytes.get($i) != Ok('\\') or bytes.get($i + 1) != Ok('u') {
							return Err(InvalidJson("unpaired JSON Unicode surrogate"))
						}
						low = hex4(bytes, $i + 2)?
						if low < 0xDC00 or low > 0xDFFF {
							return Err(InvalidJson("invalid JSON Unicode surrogate pair"))
						}
						$point = 0x10000 + ($point - 0xD800) * 1024 + low - 0xDC00
						$i = $i + 6
					} else if $point >= 0xDC00 and $point <= 0xDFFF {
						return Err(InvalidJson("unpaired JSON Unicode surrogate"))
					}
					$out = $out.concat(utf8($point))
				}
				_ => return Err(InvalidJson("invalid JSON string escape"))
			}
		} else if byte < 32 {
			return Err(InvalidJson("unescaped control byte in JSON string"))
		} else {
			$out = $out.append(byte)
		}
	}
	Err(InvalidJson("unterminated JSON string"))
}

hex4 : List(U8), U64 -> Try(U32, LifecycleJson.Error)
hex4 = |bytes, offset| {
	var $value = 0.U32
	var $i = offset
	while $i < offset + 4 {
		byte = bytes.get($i).map_err(|_| InvalidJson("short JSON Unicode escape"))?
		digit_ = if (byte >= '0' and byte <= '9') byte - '0'
		else if (byte >= 'a' and byte <= 'f') byte - 'a' + 10
		else if (byte >= 'A' and byte <= 'F') byte - 'A' + 10
		else return Err(InvalidJson("invalid JSON Unicode escape"))
		$value = $value * 16 + digit_.to_u32()
		$i = $i + 1
	}
	Ok($value)
}

utf8 : U32 -> List(U8)
utf8 = |point| {
	if point < 128 {
		[point.to_u8_wrap()]
	}
		else if point < 2048 {
			[(192 + point // 64).to_u8_wrap(), (128 + point % 64).to_u8_wrap()]
		} else if point < 65536 {
			[
				(224 + point // 4096).to_u8_wrap(),
				(128 + point // 64 % 64).to_u8_wrap(),
				(128 + point % 64).to_u8_wrap(),
			]
		} else {
			[
				(240 + point // 262144).to_u8_wrap(),
				(128 + point // 4096 % 64).to_u8_wrap(),
				(128 + point // 64 % 64).to_u8_wrap(),
				(128 + point % 64).to_u8_wrap(),
			]
		}
}

expect LifecycleJson.decode("{\"a\":1,\"\\u0061\":2}").is_err()
expect ["{}{}", "[1,]", "01", "1e", "\"\\ud800\"", "{\"a\" 1}"].all(|text| LifecycleJson.decode(text).is_err())

expect LifecycleJson.decode("{\"\\u0061\":1}") == Ok(LifecycleJson.Object([{ name: "a", value: LifecycleJson.Number("1") }]))
expect LifecycleJson.decode("\"\\ud83d\\ude00\"") == Ok(LifecycleJson.String("😀"))
expect LifecycleJson.decode("${Str.repeat("[", 34)}0${Str.repeat("]", 34)}").is_err()
