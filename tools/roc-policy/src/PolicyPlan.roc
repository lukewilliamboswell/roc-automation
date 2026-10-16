import ConstantData
import PolicyConfig
import PathGlob

## Pure schema and composition layer. Source loading and trust belong to callers.
PolicyPlan :: [].{
	Profile : { name : Str, builtin : Str, version : U64, path : Str, definition : Str, sha256 : Str }
	Rule : { id : Str, legacy : PolicyConfig.Rule, include : List(Str), exclude : List(Str), level : [Off, Warning, Error], require_match : Bool, origin : Str, trace : List(Str) }
	Override : { rule : Str, include : List(Str), exclude : List(Str), reason : Str, require_match : Bool, changes : List((Str, ConstantData.Value)) }
	Plan : { schema_version : U64, roots : List(Str), exclude : List(Str), test_roots : List(Str), test_main : Str, max_warnings : U64, profiles : List(Profile), rules : List(Rule), overrides : List(Override) }

	decode : Str, Str -> Try(Plan, Str)
	decode = |source, definition| {
		value = evaluate(source, definition)?
		fields = object(value, ["schema_version", "roots", "exclude", "test_roots", "test_main", "max_warnings", "profiles", "rules", "overrides"])?
		version = integer(required(fields, "schema_version")?)?
		if version == 1 {
			old = PolicyConfig.decode(source, definition)?
			var $rules = []
			for rule in old.rules {
				id = "legacy/${($rules.len() + 1).to_str()}"
				$rules = $rules.append({ id, legacy: rule, include: [rule.scope, "${rule.scope}/**"], exclude: [], level: Error, require_match: True, origin: "schema1", trace: ["schema1"] })
			}
			return Ok({ schema_version: 1, roots: old.roots, exclude: [], test_roots: old.test_roots, test_main: old.test_main, max_warnings: 18446744073709551615, profiles: [], rules: $rules, overrides: [] })
		}
		if version != 2 {
			return Err("schema_version must be 1 or 2")
		}
		roots = paths(required(fields, "roots")?)?
		test_roots = if contains_key(fields, "test_roots") paths(required(fields, "test_roots")?)? else roots
		test_main = optional_string(fields, "test_main", "")?
		if test_main != "" and !PolicyConfig.safe_path(test_main) {
			return Err("test_main must be a safe relative path")
		}
		exclude = optional_globs(fields, "exclude", [])?
		max_warnings = if contains_key(fields, "max_warnings") integer(required(fields, "max_warnings")?)? else 18446744073709551615
		var $profiles = []
		if contains_key(fields, "profiles") {
			profile_entries = items(required(fields, "profiles")?)?
			if profile_entries.len() > 32 {
				return Err("at most 32 profiles are supported")
			}
			for entry in profile_entries {
				f = object(entry, ["name", "builtin", "version", "path", "definition", "sha256"])?
				name = string(required(f, "name")?)?
				if !valid_id(name) or name.contains("/") or $profiles.any(|p| p.name == name) {
					return Err("profile names must be unique single-component IDs")
				}
				builtin = optional_string(f, "builtin", "")?
				path = optional_string(f, "path", "")?
				definition_name = optional_string(f, "definition", "profile")?
				sha256 = optional_string(f, "sha256", "")?
				profile_version = if contains_key(f, "version") integer(required(f, "version")?)? else 0
				if builtin != "" {
					if !valid_id(builtin) or profile_version == 0 or contains_key(f, "path") or contains_key(f, "definition") or contains_key(f, "sha256") {
						return Err("builtin profiles require builtin and a positive version only")
					}
				} else {
					if !PolicyConfig.safe_path(path) or !contains_key(f, "definition") or definition_name.is_empty() or contains_key(f, "version") or sha256.to_utf8().len() != 64 or !sha256.to_utf8().all(|b| (b >= '0' and b <= '9') or (b >= 'a' and b <= 'f')) {
						return Err("vendored profiles require path, definition and lowercase SHA-256")
					}
				}
				$profiles = $profiles.append({ name, builtin, version: profile_version, path, definition: definition_name, sha256 })
			}
		}
		rules = if contains_key(fields, "rules") decode_rules(required(fields, "rules")?, "local:${definition}")? else []
		var $overrides = []
		if contains_key(fields, "overrides") {
			override_entries = items(required(fields, "overrides")?)?
			if override_entries.len() > 10000 {
				return Err("at most 10000 overrides are supported")
			}
			for entry in override_entries {
				f = object(entry, ["rule", "include", "exclude", "reason", "require_match", "changes"])?
				rule = string(required(f, "rule")?)?
				reason = string(required(f, "reason")?)?
				if !valid_id(rule) or reason.trim().is_empty() {
					return Err("overrides require a rule ID and nonempty reason")
				}
				include = optional_globs(f, "include", [])?
				override_exclude = optional_globs(f, "exclude", [])?
				changes = object(required(f, "changes")?, ["level", "include", "exclude", "require_match", "name", "value", "phrase", "replacement", "channels", "accepted_words", "accepted_identifiers", "corrections"])?
				if changes.is_empty() {
					return Err("override changes must not be empty")
				}
				if (!include.is_empty() or !override_exclude.is_empty()) and (contains_key(changes, "include") or contains_key(changes, "exclude") or contains_key(changes, "require_match")) {
					return Err("file-scoped overrides cannot modify selectors or aggregate require_match")
				}
				require_match = optional_bool(f, "require_match", True)?
				$overrides = $overrides.append({ rule, include, exclude: override_exclude, reason, require_match, changes })
			}
		}
		Ok({ schema_version: 2, roots, exclude, test_roots, test_main, max_warnings, profiles: $profiles, rules, overrides: $overrides })
	}

	## Loads supplied profile bytes in descriptor order, then validates composition.
	finalize : Plan, List({ profile : Profile, source : Str }) -> Try(Plan, Str)
	finalize = |plan, loaded| {
		if plan.rules.len() > 10000 or plan.overrides.len() > 10000 or plan.profiles.len() > 32 {
			return Err("policy composition limit exceeded")
		}
		if loaded.len() != plan.profiles.len() {
			return Err("profile source count does not match descriptors")
		}
		var $rules = []
		var $profiles = []
		for descriptor in plan.profiles {
			entry = match loaded.find_first(|candidate| candidate.profile.name == descriptor.name) {
				Ok(found) => found
				Err(_) => return Err("missing profile source ${descriptor.name}")
			}
			normalized = if descriptor.builtin != "" { ..entry.profile, sha256: descriptor.sha256 } else entry.profile
			if normalized != descriptor {
				return Err("loaded profile descriptor does not match")
			}
			$profiles = $profiles.append(entry.profile)
			f = object(evaluate(entry.source, descriptor.definition)?, ["schema_version", "rules"])?
			if integer(required(f, "schema_version")?)? != 2 {
				return Err("profiles require schema_version 2")
			}
			origin = if descriptor.builtin != "" "builtin:${descriptor.builtin}@${descriptor.version.to_str()}#${entry.profile.sha256}" else "${descriptor.path}@${descriptor.sha256}"
			profile_rules = required(f, "rules")?
			if items(profile_rules)?.len() + $rules.len() + plan.rules.len() > 10000 {
				return Err("at most 10000 final rules are supported")
			}
			for rule in decode_rules(profile_rules, origin)? {
				$rules = $rules.append({ ..rule, id: "${descriptor.name}/${rule.id}" })
			}
		}
		$rules = $rules.concat(plan.rules)
		var $seen = []
		for rule in $rules {
			if $seen.contains(rule.id) {
				return Err("duplicate rule ID ${rule.id}")
			}
			$seen = $seen.append(rule.id)
		}
		for override in plan.overrides {
			rule = match $rules.find_first(|r| r.id == override.rule) {
				Ok(r) => r
				Err(_) => return Err("override targets unknown rule ${override.rule}")
			}
			_validated = apply(rule, override)?
		}
		resolved = { ..plan, rules: $rules, profiles: $profiles }
		_globals = PolicyPlan.global_rules(resolved)?
		Ok(resolved)
	}

	global_rules : Plan -> Try(List(Rule), Str)
	global_rules = |plan| {
		var $out = []
		for original in plan.rules {
			var $rule = original
			for override in plan.overrides {
				if override.rule == $rule.id and override.include.is_empty() and override.exclude.is_empty() {
					$rule = apply($rule, override)?
				}
			}
			$out = $out.append(validate_rule($rule)?)
		}
		Ok($out)
	}

	override_matches : Override, Str -> Bool
	override_matches = |override, path| (override.include.is_empty() or PathGlob.any_match(override.include, path)) and !PathGlob.any_match(override.exclude, path)

	## Effective settings before selection, also used to explain excluded rules.
	effective_rules : Plan, Str -> Try(List(Rule), Str)
	effective_rules = |plan, path| {
		var $out = []
		for original in plan.rules {
			var $rule = original
			for override in plan.overrides {
				if override.rule == $rule.id and override_matches(override, path) {
					$rule = apply($rule, override)?
				}
			}
			$out = $out.append(validate_rule($rule)?)
		}
		Ok($out)
	}

	selected : Plan, Rule, Str -> Bool
	selected = |plan, rule, path| {
		if plan.schema_version == 1 {
			path == rule.legacy.scope or path.starts_with("${rule.legacy.scope}/")
		} else {
			PathGlob.any_match(rule.include, path) and !PathGlob.any_match(rule.exclude, path)
		}
	}

	for_file : Plan, Str -> Try(List(Rule), Str)
	for_file = |plan, path| {
		rules = effective_rules(plan, path)?
		Ok(rules.keep_if(|rule| selected(plan, rule, path)))
	}
}

# Helpers are private: this module evaluates only literal configuration data.
evaluate : Str, Str -> Try(ConstantData.Value, Str)
evaluate = |source, definition| match ConstantData.evaluate(source, definition) {
	Ok(v) => Ok(v)
	Err(e) => Err("${e.kind}: ${e.message}")
}

object : ConstantData.Value, List(Str) -> Try(List((Str, ConstantData.Value)), Str)
object = |value, allowed| match ConstantData.unwrap(value).kind {
	Record(fields) => if fields.all(|(k, _)| allowed.contains(k)) Ok(fields) else Err("unknown configuration field")
	_ => Err("expected a record")
}

required : List((Str, ConstantData.Value)), Str -> Try(ConstantData.Value, Str)
required = |fields, key| match fields.find_first(|(k, _)| k == key) {
	Ok((_, v)) => Ok(v)
	Err(_) => Err("missing field ${key}")
}

contains_key : List((Str, ConstantData.Value)), Str -> Bool
contains_key = |fields, key| fields.any(|(k, _)| k == key)

string : ConstantData.Value -> Try(Str, Str)
string = |v| match ConstantData.unwrap(v).kind {
	String(s) => Ok(s)
	_ => Err("expected a string")
}

integer : ConstantData.Value -> Try(U64, Str)
integer = |v| match ConstantData.unwrap(v).kind {
	Integer(s) => parse_u64(s)
	_ => Err("expected unsigned integer")
}

boolean : ConstantData.Value -> Try(Bool, Str)
boolean = |v| match ConstantData.unwrap(v).kind {
	Boolean(b) => Ok(b)
	_ => Err("expected Boolean")
}

items : ConstantData.Value -> Try(List(ConstantData.Value), Str)
items = |v| match ConstantData.unwrap(v).kind {
	ListValue(xs) => Ok(xs)
	_ => Err("expected list")
}

strings : ConstantData.Value -> Try(List(Str), Str)
strings = |v| {
	var $out = []
	for x in items(v)? {
		$out = $out.append(string(x)?)
	}
	Ok($out)
}

optional_string : List((Str, ConstantData.Value)), Str, Str -> Try(Str, Str)
optional_string = |f, k, d| if contains_key(f, k) string(required(f, k)?) else Ok(d)

optional_bool : List((Str, ConstantData.Value)), Str, Bool -> Try(Bool, Str)
optional_bool = |f, k, d| if contains_key(f, k) boolean(required(f, k)?) else Ok(d)

fold : Str -> Str
fold = |s| Str.from_utf8_lossy(s.to_utf8().map(|b| if b >= 'A' and b <= 'Z' b + 32 else b))

valid_id : Str -> Bool
valid_id = |s| !s.is_empty() and s.split_on("/").all(|part| !part.is_empty() and part != "." and part != ".." and part.to_utf8().all(|b| (b >= 'a' and b <= 'z') or (b >= 'A' and b <= 'Z') or (b >= '0' and b <= '9') or b == '-' or b == '_' or b == '.'))

paths : ConstantData.Value -> Try(List(Str), Str)
paths = |v| {
	xs = strings(v)?
	if xs.is_empty() or !xs.all(PolicyConfig.safe_path) Err("roots must be nonempty safe relative paths") else Ok(xs)
}

globs : ConstantData.Value -> Try(List(Str), Str)
globs = |v| {
	xs = strings(v)?
	for x in xs {
		_ok = PathGlob.validate(x)?
	}
	if xs.any(|x| xs.keep_if(|y| y == x).len() > 1) Err("duplicate glob") else Ok(xs)
}

optional_globs : List((Str, ConstantData.Value)), Str, List(Str) -> Try(List(Str), Str)
optional_globs = |f, k, d| if contains_key(f, k) globs(required(f, k)?) else Ok(d)

level : ConstantData.Value -> Try([Off, Warning, Error], Str)
level = |v| match ConstantData.unwrap(v).kind {
	Tag("Off") => Ok(Off)
	Tag("Warning") => Ok(Warning)
	Tag("Error") => Ok(Error)
	_ => Err("level must be Off, Warning or Error")
}

channels : ConstantData.Value -> Try(List(PolicyConfig.Channel), Str)
channels = |v| {
	var $out = []
	for x in items(v)? {
		c = match ConstantData.unwrap(x).kind {
			Tag("Comments") => Comments
			Tag("Identifiers") => Identifiers
			Tag("Strings") => Strings
			Tag("Prose") => Prose
			_ => return Err("unknown channel")
		}
		if $out.contains(c) {
			return Err("duplicate channel")
		}
		$out = $out.append(c)
	}
	Ok($out)
}

words : ConstantData.Value -> Try(List(Str), Str)
words = |v| {
	xs = strings(v)?
	var $seen = []
	for x in xs {
		key = fold(x)
		if x.is_empty() or $seen.contains(key) {
			return Err("empty or duplicate vocabulary entry")
		}
		$seen = $seen.append(key)
	}
	Ok(xs)
}

corrections : ConstantData.Value -> Try(List((Str, Str)), Str)
corrections = |v| {
	var $out = []
	for x in items(v)? {
		f = object(x, ["word", "replacement"])?
		word = string(required(f, "word")?)?
		replacement = string(required(f, "replacement")?)?
		if word.is_empty() or replacement.is_empty() or !word.to_utf8().all(|b| (b >= 'a' and b <= 'z') or (b >= 'A' and b <= 'Z') or b >= 128) {
			return Err("corrections require single-word keys and nonempty replacements")
		}
		if $out.any(|(k, _)| fold(k) == fold(word)) {
			return Err("duplicate correction key")
		}
		$out = $out.append((word, replacement))
	}
	Ok($out)
}

# Decode through the existing kind validator to keep leaf semantics in one place.
decode_rules : ConstantData.Value, Str -> Try(List(PolicyPlan.Rule), Str)
decode_rules = |v, origin| {
	var $out = []
	entries = items(v)?
	if entries.len() > 10000 {
		return Err("at most 10000 rules are supported")
	}
	for entry in entries {
		f = object(entry, ["id", "kind", "include", "exclude", "level", "require_match", "name", "value", "phrase", "replacement", "channels", "accepted_words", "accepted_identifiers", "corrections"])?
		id = string(required(f, "id")?)?
		if !valid_id(id) or $out.any(|r| r.id == id) {
			return Err("invalid or duplicate rule ID")
		}
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
		required_options = match kind {
			Dependency => ["name", "value"]
			CompilerPin => ["value"]
			ForbiddenImport => ["name"]
			Phrase => ["phrase"]
			_ => []
		}
		if !required_options.all(|key| contains_key(f, key)) {
			return Err("missing required rule option")
		}
		legacy = { kind, scope: "", name: "roc", value: "", channels: [Comments, Identifiers, Prose], allow: [], accepted_identifiers: [], words: [] }
		rule = { id, legacy, include: optional_globs(f, "include", ["**"])?, exclude: optional_globs(f, "exclude", [])?, level: if contains_key(f, "level") level(required(f, "level")?)? else Error, require_match: optional_bool(f, "require_match", True)?, origin, trace: [origin] }
		changes = f.keep_if(|(k, _)| !["id", "kind", "include", "exclude", "level", "require_match"].contains(k))
		$out = $out.append(validate_rule(apply_fields(rule, changes, False)?)?)
	}
	Ok($out)
}

apply : PolicyPlan.Rule, PolicyPlan.Override -> Try(PolicyPlan.Rule, Str)
apply = |rule, override| {
	updated = apply_fields(rule, override.changes, True)?
	Ok({ ..updated, trace: updated.trace.append("override ${override.rule} [${Str.join_with(override.changes.map(|(key, _)| key), ", ")}]: ${override.reason}") })
}

apply_fields : PolicyPlan.Rule, List((Str, ConstantData.Value)), Bool -> Try(PolicyPlan.Rule, Str)
apply_fields = |rule, changes, operations| {
	allowed = match rule.legacy.kind {
		Dependency => ["name", "value"]
		CompilerPin => ["value"]
		ForbiddenImport => ["name"]
		NoCompilerPin | PublicDocs => []
		Spelling => ["channels", "accepted_words", "accepted_identifiers", "corrections"]
		Phrase => ["channels", "phrase", "replacement"]
	}
	if changes.any(|(k, _)| !["level", "include", "exclude", "require_match"].contains(k) and !allowed.contains(k)) {
		return Err("irrelevant field for rule kind")
	}
	var $r = rule
	var $leaf = rule.legacy
	for (key, value) in changes {
		match key {
			"level" => {
				$r = { ..$r, level: level(value)? }
			}
			"require_match" => {
				$r = { ..$r, require_match: boolean(value)? }
			}
			"include" => {
				$r = { ..$r, include: if operations update_strings($r.include, value, False, True)? else globs(value)? }
			}
			"exclude" => {
				$r = { ..$r, exclude: if operations update_strings($r.exclude, value, False, True)? else globs(value)? }
			}
			"name" | "phrase" => {
				$leaf = { ..$leaf, name: string(value)? }
			}
			"value" | "replacement" => {
				$leaf = { ..$leaf, value: string(value)? }
			}
			"accepted_words" => {
				accepted = if operations update_strings($leaf.allow, value, True, False)? else words(value)?
				if !accepted.all(|word| word.to_utf8().all(word_byte)) {
					return Err("accepted_words entries must be single word components; use accepted_identifiers for whole identifiers")
				}
				$leaf = { ..$leaf, allow: accepted }
			}
			"accepted_identifiers" => {
				$leaf = { ..$leaf, accepted_identifiers: if operations update_strings($leaf.accepted_identifiers, value, True, False)? else words(value)? }
			}
			"channels" => {
				if operations {
					f = operation(value)?
					if contains_key(f, "replace") {
						$leaf = { ..$leaf, channels: channels(required(f, "replace")?)? }
					} else {
						add = if contains_key(f, "add") channels(required(f, "add")?)? else []
						remove = if contains_key(f, "remove") channels(required(f, "remove")?)? else []
						if add.any(|x| remove.contains(x)) {
							return Err("contradictory channel operations")
						}
						var $xs = $leaf.channels.keep_if(|x| !remove.contains(x))
						for x in add {
							if !$xs.contains(x) {
								$xs = $xs.append(x)
							}
						}

						$leaf = { ..$leaf, channels: $xs }
					}
				} else {
					$leaf = { ..$leaf, channels: channels(value)? }
				}
			}
			"corrections" => {
				if operations {
					f = operation(value)?
					if contains_key(f, "replace") {
						$leaf = { ..$leaf, words: corrections(required(f, "replace")?)? }
					} else {
						add = if contains_key(f, "add") corrections(required(f, "add")?)? else []
						remove = if contains_key(f, "remove") words(required(f, "remove")?)?.map(fold) else []
						if add.any(|(k, _)| remove.contains(fold(k))) {
							return Err("contradictory correction operations")
						}
						xs = $leaf.words.keep_if(|(k, _)| !remove.contains(fold(k)) and !add.any(|(new, _)| fold(k) == fold(new)))
						$leaf = { ..$leaf, words: xs.concat(add) }
					}
				} else {
					$leaf = { ..$leaf, words: corrections(value)? }
				}
			}
			_ => return Err("unknown change")
		}
	}
	if $leaf.name.is_empty() or (($leaf.kind == Dependency or $leaf.kind == CompilerPin) and $leaf.value.is_empty()) {
		return Err("rule name/value must not be empty")
	}
	if $leaf.kind == Phrase and !$leaf.name.to_utf8().any(|b| (b >= 'a' and b <= 'z') or (b >= 'A' and b <= 'Z') or b >= 128) {
		return Err("phrase must contain a word")
	}

	Ok({ ..$r, legacy: $leaf })
}

operation : ConstantData.Value -> Try(List((Str, ConstantData.Value)), Str)
operation = |v| {
	f = object(v, ["replace", "add", "remove"])?
	if f.is_empty() or (contains_key(f, "replace") and f.len() != 1) Err("collection operations require replace or add/remove") else Ok(f)
}

update_strings : List(Str), ConstantData.Value, Bool, Bool -> Try(List(Str), Str)
update_strings = |current, v, folded, is_glob| {
	f = operation(v)?
	if contains_key(f, "replace") {
		return if is_glob globs(required(f, "replace")?) else words(required(f, "replace")?)
	}
	add = if contains_key(f, "add") (if is_glob globs(required(f, "add")?)? else words(required(f, "add")?)?) else []
	remove = if contains_key(f, "remove") (if is_glob globs(required(f, "remove")?)? else words(required(f, "remove")?)?) else []
	normalized = |s| if folded fold(s) else s
	if add.any(|s| remove.any(|r| normalized(s) == normalized(r))) {
		return Err("contradictory collection operations")
	}
	var $out = current.keep_if(|s| !remove.any(|r| normalized(s) == normalized(r)))
	for s in add {
		if !$out.any(|r| normalized(s) == normalized(r)) {
			$out = $out.append(s)
		}
	}
	Ok($out)
}

parse_u64 : Str -> Try(U64, Str)
parse_u64 = |s| {
	if s.is_empty() {
		return Err("expected unsigned integer")
	}
	var $n = 0.U64
	for b in s.to_utf8() {
		if b < '0' or b > '9' {
			return Err("expected decimal unsigned integer")
		}
		digit = b.to_u64() - 48
		if $n > (18446744073709551615 - digit) / 10 {
			return Err("integer overflow")
		}
		$n = $n * 10 + digit
	}
	Ok($n)
}

validate_rule : PolicyPlan.Rule -> Try(PolicyPlan.Rule, Str)
validate_rule = |rule| {
	if rule.include.is_empty() {
		return Err("rule include must not be empty")
	}
	if rule.legacy.channels.is_empty() {
		return Err("channels must not be empty")
	}
	Ok(rule)
}

word_byte : U8 -> Bool
word_byte = |b| (b >= 'a' and b <= 'z') or (b >= 'A' and b <= 'Z') or b >= 128
