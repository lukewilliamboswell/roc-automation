import ConstantData

## Repository-specific data decoded independently of the generic evaluator.
PolicyConfig :: [].{
	Channel : [Comments, Identifiers, Strings, Prose]
	Rule : {
		kind : [Dependency, CompilerPin, NoCompilerPin, ForbiddenImport, Spelling, Phrase, PublicDocs],
		scope : Str,
		name : Str,
		value : Str,
		channels : List(Channel),
		allow : List(Str),
		words : List((Str, Str)),
	}
	Config : { roots : List(Str), rules : List(Rule), test_roots : List(Str), test_main : Str }

	safe_path = |path| !path.is_empty() and !path.starts_with("/") and !path.contains("\\") and path.split_on("/").all(|part| part != "" and part != "." and part != "..")

	decode : Str, Str -> Try(Config, Str)
	decode = |source, definition| {
		value = match ConstantData.evaluate(source, definition) {
			Ok(found) => found
			Err(error) => return Err("${error.kind} at bytes ${error.start.to_str()}..${error.end.to_str()}: ${error.message}")
		}
		fields = object(value, ["schema_version", "roots", "rules", "test_roots", "test_main"])?
		match ConstantData.unwrap(required(fields, "schema_version")?).kind {
			Integer("1") => {}
			_ => return Err("schema_version must be 1")
		}
		roots = strings(required(fields, "roots")?)?
		if roots.is_empty() or !roots.all(safe_path) {
			return Err("roots must be nonempty safe relative paths")
		}
		var $rules = []
		for entry in items(required(fields, "rules")?)? {
			f = object(entry, ["kind", "scope", "name", "value", "phrase", "replacement", "channels", "allow", "words"])?
			kind = match ConstantData.unwrap(required(f, "kind")?).kind {
				Tag("Dependency") => Dependency
				Tag("CompilerPin") => CompilerPin
				Tag("NoCompilerPin") => NoCompilerPin
				Tag("ForbiddenImport") => ForbiddenImport
				Tag("Spelling") => Spelling
				Tag("Phrase") => Phrase
				Tag("PublicDocs") => PublicDocs
				_ => return Err("unknown rule kind")
			}
			allowed = match kind {
				Dependency => ["kind", "scope", "name", "value"]
				CompilerPin => ["kind", "scope", "value"]
				NoCompilerPin | PublicDocs => ["kind", "scope"]
				ForbiddenImport => ["kind", "scope", "name"]
				Spelling => ["kind", "scope", "channels", "allow", "words"]
				Phrase => ["kind", "scope", "channels", "allow", "phrase", "replacement"]
			}
			if f.any(|(key, _)| !allowed.contains(key)) {
				return Err("irrelevant field for ${Str.inspect(kind)} rule")
			}
			scope = string(required(f, "scope")?)?
			if !safe_path(scope) {
				return Err("scope must be a safe relative file or directory path")
			}
			name = match kind {
				Dependency | ForbiddenImport => string(required(f, "name")?)?
				Phrase => string(required(f, "phrase")?)?
				_ => "roc"
			}
			value_text = match kind {
				Dependency | CompilerPin => string(required(f, "value")?)?
				Phrase => optional_string(f, "replacement", "")?
				_ => ""
			}
			if name.is_empty() or ((kind == Dependency or kind == CompilerPin) and value_text.is_empty()) {
				return Err("rule name/value must not be empty")
			}
			if kind == Phrase and !name.to_utf8().any(word_byte) {
				return Err("phrase must contain a matchable word")
			}
			var $channels = [Comments, Identifiers, Prose]
			if contains_key(f, "channels") {
				$channels = []
				for ch in items(required(f, "channels")?)? {
					channel = match ConstantData.unwrap(ch).kind {
						Tag("Comments") => Comments
						Tag("Identifiers") => Identifiers
						Tag("Strings") => Strings
						Tag("Prose") => Prose
						_ => return Err("unknown text channel")
					}
					if $channels.contains(channel) {
						return Err("duplicate text channel")
					}
					$channels = $channels.append(channel)
				}
				if $channels.is_empty() {
					return Err("channels must not be empty")
				}
			}
			allow = if contains_key(f, "allow") strings(required(f, "allow")?)? else []
			if allow.any(Str.is_empty) {
				return Err("allow entries must not be empty")
			}
			var $words = []
			if contains_key(f, "words") {
				for word in items(required(f, "words")?)? {
					wf = object(word, ["word", "replacement"])?
					typo = string(required(wf, "word")?)?
					correction = string(required(wf, "replacement")?)?
					if typo.is_empty() or correction.is_empty() {
						return Err("spelling word/replacement must not be empty")
					}
					if !typo.to_utf8().all(word_byte) {
						return Err("custom spelling keys must be single words")
					}
					if $words.any(|(known, _)| ascii_fold(known) == ascii_fold(typo)) {
						return Err("duplicate custom spelling word")
					}
					$words = $words.append((typo, correction))
				}
			}
			$rules = $rules.append({ kind, scope, name, value: value_text, channels: $channels, allow, words: $words })
		}
		test_roots = if contains_key(fields, "test_roots") strings(required(fields, "test_roots")?)? else roots
		if test_roots.is_empty() or !test_roots.all(safe_path) {
			return Err("test_roots must be nonempty safe paths")
		}
		test_main = optional_string(fields, "test_main", "")?
		if test_main != "" and !safe_path(test_main) {
			return Err("test_main must be a safe relative path")
		}
		Ok({ roots, rules: $rules, test_roots, test_main })
	}
}

object = |value, allowed| {
	match ConstantData.unwrap(value).kind {
		Record(fields) => {
			if fields.any(|(name, _)| !allowed.contains(name)) {
				return Err("unknown configuration field")
			}
			Ok(fields)
		}
		_ => Err("expected a record")
	}
}

required = |fields, name| match fields.find_first(|(key, _)| key == name) {
	Ok((_, value)) => Ok(value)
	Err(_) => Err("missing field ${name}")
}

contains_key = |fields, name| fields.any(|(key, _)| key == name)

string = |value| match ConstantData.unwrap(value).kind {
	String(found) => Ok(found)
	_ => Err("expected a string")
}

items = |value| match ConstantData.unwrap(value).kind {
	ListValue(found) => Ok(found)
	_ => Err("expected a list")
}

strings = |value| {
	var $out = []
	for item in items(value)? {
		$out = $out.append(string(item)?)
	}
	Ok($out)
}

optional_string = |fields, name, default| if contains_key(fields, name) string(required(fields, name)?) else Ok(default)

word_byte = |b| (b >= 'a' and b <= 'z') or (b >= 'A' and b <= 'Z') or b >= 128

ascii_fold = |word| Str.from_utf8_lossy(word.to_utf8().map(|b| if b >= 'A' and b <= 'Z' b + 32 else b))
