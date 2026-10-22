## Admit the canonical application header subset used by published examples.
## Comments and string contents never become fields. Unsupported or ambiguous
## header syntax fails closed; the compiler still checks the application itself.
AppHeader := [].{
	valid = |source, dependencies| {
		tokens = match lex(Str.to_utf8(source), [], []) {
			Ok(value) => value
			Err(_) => return Bool.False
		}
		match tokens {
			[Word("app"), Mark('['), .. as rest] => entrypoints(rest, dependencies)
			_ => Bool.False
		}
	}

	lex = |bytes, word, tokens| match bytes {
		[] => Err(Invalid)
		[byte, .. as rest] if (byte >= 'a' and byte <= 'z') or (byte >= 'A' and byte <= 'Z') or (byte >= '0' and byte <= '9') or byte == '_' => lex(rest, word.append(byte), tokens)
		_ if !word.is_empty() => lex(bytes, [], tokens.append(Word(Str.from_utf8_lossy(word))))
		[byte, .. as rest] if [9, 10, 13, 32].contains(byte) => lex(rest, [], tokens)
		['#', .. as rest] => comment(rest, tokens)
		['"', .. as rest] => quoted(rest, [], tokens)
		['}', ..] => Ok(tokens.append(Mark('}')))
		[byte, .. as rest] if ['[', ']', '{', ':', ',', '!'].contains(byte) => lex(rest, [], tokens.append(Mark(byte)))
		_ => Err(Invalid)
	}
	comment = |bytes, tokens| match bytes {
		[10, .. as rest] => lex(rest, [], tokens)
		[_, .. as rest] => comment(rest, tokens)
		[] => Err(Invalid)
	}
	quoted = |bytes, value, tokens| match bytes {
		['"', .. as rest] => lex(rest, [], tokens.append(Text(Str.from_utf8_lossy(value))))
		[byte, .. as rest] if byte >= 32 and byte != '\\' => quoted(rest, value.append(byte), tokens)
		_ => Err(Invalid)
	}
	entrypoints = |tokens, dependencies| match tokens {
		[Mark(']'), Mark('{'), .. as rest] => bindings(rest, dependencies, [], Bool.False, 0)
		[Word(_), .. as rest] => entrypoint_end(rest, dependencies)
		_ => Bool.False
	}
	entrypoint_end = |tokens, dependencies| match tokens {
		[Mark('!'), .. as rest] => entrypoint_separator(rest, dependencies)
		_ => entrypoint_separator(tokens, dependencies)
	}
	entrypoint_separator = |tokens, dependencies| match tokens {
		[Mark(','), .. as rest] => entrypoints(rest, dependencies)
		[Mark(']'), Mark('{'), .. as rest] => bindings(rest, dependencies, [], Bool.False, 0)
		_ => Bool.False
	}
	bindings = |tokens, dependencies, names, found, platforms| match tokens {
		[Mark('}')] => found and platforms == 1
		[Word(name), Mark(':'), .. as rest] if name != "roc" and !names.contains(name) => match rest {
			[Word("platform"), Text(url), .. as after] => separator(after, dependencies, names.append(name), found or dependencies.contains(url), platforms + 1)
			[Text(url), .. as after] => separator(after, dependencies, names.append(name), found or dependencies.contains(url), platforms)
			_ => Bool.False
		}
		_ => Bool.False
	}
	separator = |tokens, dependencies, names, found, platforms| match tokens {
		[Mark(','), .. as rest] => bindings(rest, dependencies, names, found, platforms)
		[Mark('}')] => found and platforms == 1
		_ => Bool.False
	}
}

url = "https://github.com/o/pkg/releases/download/1.0.0/hash.tar.zst"
expect AppHeader.valid("app [main!] { pf: platform \"${url}\" }", [url])
expect AppHeader.valid("# header comment\napp [main!, other] {\n# ignored } \"bogus\"\n cli: platform \"third-party\", pkg: \"${url}\",\n}", [url])
expect !AppHeader.valid("app [main!] { # \"${url}\"\n pf: platform \"other\" }", [url])
expect !AppHeader.valid("app [main!] { pf: platform \"other\" }\nvalue = \"${url}\"", [url])
expect !AppHeader.valid("app [main!] { pf: platform \"${url}\", roc\n:\n\"nightly-x\" }", [url])
expect !AppHeader.valid("app [main!] { pf: platform \"${url}\", pf: \"other\" }", [url])
expect !AppHeader.valid("app [main!] { pf: platform \"other\", ignored: [\"${url}\"] }", [url])
expect !AppHeader.valid("app [main!] { pf: platform \"${url}\"", [url])
expect !AppHeader.valid("app [main!] { pf: platform \"other\", pkg: \"${url}\" extra }", [url])
expect !AppHeader.valid("app [main!] { pf: platform \"other\", pkg: \"${url}\" roc: \"nightly\" }", [url])
