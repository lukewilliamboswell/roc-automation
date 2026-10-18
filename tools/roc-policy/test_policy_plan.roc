app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
	parser: "https://github.com/lukewilliamboswell/roc-parser/releases/download/2.1.0-rc.1/6dvNG3Q2mx53i4YPJN2ZGK98WdXnBMMuceAmDsCq7BEd.tar.zst",
}

import src/PolicyPlan
main! = |_| Ok({})
expect PolicyPlan.decode("config = {schema_version: 2, roots: [\"src\"], rules: []}", "config").is_ok()

plan = |body| {
	p = PolicyPlan.decode("config = {schema_version: 2, roots: [\"src\"], ${body}}", "config")?
	PolicyPlan.finalize(p, [])
}

spelling = "rules: [{id: \"spell\", kind: Spelling}]"

selected = |body, path| {
	p = plan(body)?
	PolicyPlan.for_file(p, path)
}

expect plan("rules: [{id: \"a\", kind: Phrase, phrase: \"very important\"}, {id: \"b\", kind: Phrase, phrase: \"obviously\"}]").map_ok(|p| p.rules.len()) == Ok(2)
expect plan("rules: [{id: \"a\", kind: PublicDocs}, {id: \"a\", kind: PublicDocs}]").is_err()
expect plan("rules: [{id: \"../a\", kind: PublicDocs}]").is_err()
expect plan("rules: [{id: \"a\", kind: Dependency}]").is_err()
expect plan("rules: [{id: \"a\", kind: Phrase}]").is_err()
expect plan("rules: [{id: \"a\", kind: PublicDocs, value: \"bad\"}]").is_err()
expect plan("rules: [{id: \"a\", kind: PublicDocs, include: [\"../secret\"]}]").is_err()
expect plan("rules: [{id: \"a\", kind: PublicDocs, include: []}]").is_err()
expect plan("roots: [\"elsewhere\"]").is_err()
expect plan("max_warnings: -1").is_err()
expect plan("max_warnings: 18446744073709551616").is_err()
expect plan("max_warnings: 0").map_ok(|p| p.max_warnings) == Ok(0)
expect plan("rules: []").map_ok(|p| p.max_warnings) == Ok(18446744073709551615)
expect plan("rules: [], overrides: [{rule: \"missing\", reason: \"test\", changes: {level: Off}}]").is_err()
expect plan("${spelling}, overrides: [{rule: \"spell\", reason: \"\", changes: {level: Off}}]").is_err()
expect plan("${spelling}, overrides: [{rule: \"spell\", include: [\"src/**\"], reason: \"fixtures\", changes: {include: {replace: [\"other/**\"]}}}]").is_err()
expect selected("${spelling}, overrides: [{rule: \"spell\", reason: \"adopt slowly\", changes: {level: Warning}}, {rule: \"spell\", include: [\"src/fixtures/**\"], reason: \"fixtures\", changes: {level: Off}}]", "src/fixtures/x.roc").map_ok(|rs| rs.map(|r| (r.level, r.trace))) == Ok([(Off, ["local:config", "override spell [level]: adopt slowly", "override spell [level]: fixtures"])])
expect selected("${spelling}, overrides: [{rule: \"spell\", include: [\"src/fixtures/**\"], reason: \"fixtures\", changes: {level: Off}}, {rule: \"spell\", reason: \"later wins\", changes: {level: Warning}}]", "src/fixtures/x.roc").map_ok(|rs| rs.map(|r| r.level)) == Ok([Warning])
expect selected("rules: [{id: \"a\", kind: Phrase, phrase: \"very important\"}, {id: \"b\", kind: Phrase, phrase: \"obviously\"}], overrides: [{rule: \"a\", reason: \"allow one phrase\", changes: {level: Off}}]", "src/a.roc").map_ok(|rs| rs.map(|r| (r.id, r.level))) == Ok([("a", Off), ("b", Error)])
expect selected("rules: [{id: \"spell\", kind: Spelling, accepted_words: [\"Alpha\", \"Beta\"]}], overrides: [{rule: \"spell\", reason: \"vocabulary\", changes: {accepted_words: {add: [\"Gamma\",\"ALPHA\"], remove: [\"beta\"]}}}]", "src/a.roc").map_ok(|rs| rs.map(|r| r.legacy.allow)) == Ok([["Alpha", "Gamma"]])
expect selected("rules: [{id: \"spell\", kind: Spelling, accepted_words: [\"Alpha\"]}], overrides: [{rule: \"spell\", reason: \"replace\", changes: {accepted_words: {replace: [\"Beta\"]}}}]", "src/a.roc").map_ok(|rs| rs.map(|r| r.legacy.allow)) == Ok([["Beta"]])
expect plan("${spelling}, overrides: [{rule: \"spell\", reason: \"bad\", changes: {accepted_words: {replace: [], add: []}}}]").is_err()
expect plan("${spelling}, overrides: [{rule: \"spell\", reason: \"bad\", changes: {accepted_words: {add: [\"Alpha\"], remove: [\"alpha\"]}}}]").is_err()
expect plan("rules: [{id: \"spell\", kind: Spelling, accepted_words: [\"a\",\"A\"]}]").is_err()
expect selected("rules: [{id: \"spell\", kind: Spelling, accepted_identifiers: [\"tehThing\"], accepted_words: [\"widget\"]}]", "src/a.roc").map_ok(|rs| rs.map(|r| (r.legacy.allow, r.legacy.accepted_identifiers))) == Ok([(["widget"], ["tehThing"])])
expect selected("rules: [{id: \"spell\", kind: Spelling, corrections: [{word: \"teh\", replacement: \"the\"}]}], overrides: [{rule: \"spell\", reason: \"domain correction\", changes: {corrections: {add: [{word: \"TEH\", replacement: \"Teh\"}]}}}]", "src/a.roc").map_ok(|rs| rs.map(|r| r.legacy.words)) == Ok([[("TEH", "Teh")]])
expect plan("rules: [{id: \"spell\", kind: Spelling, corrections: [{word: \"teh\", replacement: \"the\"},{word: \"TEH\", replacement: \"Teh\"}]}]").is_err()
expect plan("${spelling}, overrides: [{rule: \"spell\", reason: \"invalid\", changes: {channels: {remove: [Comments, Identifiers, Prose]}}}]").is_err()
expect selected("${spelling}, overrides: [{rule: \"spell\", reason: \"limit\", changes: {include: {replace: [\"src/**/*.roc\"]}, exclude: {add: [\"src/generated/**\"]}}}]", "src/generated/x.roc").map_ok(List.len) == Ok(0)
expect plan("${spelling}, overrides: [{rule: \"spell\", reason: \"temporary\", require_match: False, changes: {level: Off}}]").map_ok(|p| p.overrides.map(|o| o.require_match)) == Ok([False])
expect PolicyPlan.decode("config = f(1)", "config").is_err()
expect PolicyPlan.decode("import Foo\nconfig = Foo.config", "config").is_err()
expect PolicyPlan.decode("config = {schema_version: 1, roots: [\"src\"], rules: [{kind: Spelling, scope: \"src\", allow: [\"tehThing\"]}]}", "config").map_ok(|p| p.rules.map(|r| (r.id, r.legacy.scope, r.legacy.accepted_identifiers))) == Ok([("legacy/1", "src", ["tehThing"])])

profile_plan = PolicyPlan.decode("config = {schema_version: 2, roots: [\"src\"], profiles: [{name: \"shared\", builtin: \"roc\", version: 1}], rules: [{id: \"local\", kind: PublicDocs}], overrides: [{rule: \"shared/spell\", reason: \"domain vocabulary\", changes: {accepted_words: {add: [\"widget\"]}}}]}", "config")
expect {
	p = profile_plan?
	descriptor = p.profiles.first()?
	result = PolicyPlan.finalize(p, [{ profile: descriptor, source: "profile = {schema_version: 2, rules: [{id: \"spell\", kind: Spelling}]}" }])?
	result.rules.map(|r| r.id) == ["shared/spell", "local"]
}
expect {
	p = profile_plan?
	descriptor = p.profiles.first()?
	PolicyPlan.finalize(p, [{ profile: descriptor, source: "profile = {schema_version: 2, roots: [\"bad\"], rules: []}" }]).is_err()
}
expect {
	p = PolicyPlan.decode("config = {schema_version: 2, roots: [\"src\"], profiles: [{name: \"shared\", builtin: \"roc\", version: 1}], rules: [{id: \"shared/spell\", kind: PublicDocs}]}", "config")?
	descriptor = p.profiles.first()?
	PolicyPlan.finalize(p, [{ profile: descriptor, source: "profile = {schema_version: 2, rules: [{id: \"spell\", kind: Spelling}]}" }]).is_err()
}
expect PolicyPlan.decode("config = {schema_version: 2, roots: [\"src\"], profiles: [{name: \"shared\", builtin: \"roc\", version: 0}]}", "config").is_err()
expect PolicyPlan.decode("config = {schema_version: 2, roots: [\"src\"], profiles: [{name: \"shared\", path: \"policy.roc\", definition: \"profile\", sha256: \"bad\"}]}", "config").is_err()
expect {
	p = plan("${spelling}, overrides: [{rule: \"spell\", reason: \"global\", changes: {level: Warning}}, {rule: \"spell\", include: [\"fixtures/**\"], reason: \"fixture\", changes: {level: Off}}]")?
	PolicyPlan.global_rules(p).map_ok(|rs| rs.map(|r| r.level)) == Ok([Warning])
}
expect selected("${spelling}, overrides: [{rule: \"spell\", reason: \"add strings\", changes: {channels: {add: [Strings]}}}, {rule: \"spell\", reason: \"only strings\", changes: {channels: {remove: [Comments, Identifiers, Prose]}}}]", "src/a.roc").map_ok(|rs| rs.map(|r| r.legacy.channels)) == Ok([[Strings]])
expect selected("${spelling}, overrides: [{rule: \"spell\", reason: \"clear\", changes: {channels: {replace: []}}}, {rule: \"spell\", reason: \"new selection\", changes: {channels: {add: [Strings]}}}]", "src/a.roc").map_ok(|rs| rs.map(|r| r.legacy.channels)) == Ok([[Strings]])
expect {
	p = profile_plan?
	d = p.profiles.first()?
	loaded = { ..d, sha256: "abcdef" }
	result = PolicyPlan.finalize(p, [{ profile: loaded, source: "profile = {schema_version: 2, rules: [{id: \"spell\", kind: Spelling}]}" }])?
	result.profiles == [loaded] and result.rules.map(|r| r.origin) == ["builtin:roc@1#abcdef", "local:config"]
}
expect PolicyPlan.decode("config = {schema_version: 2, roots: [\"src\"], profiles: [{name: \"shared\", builtin: \"future-profile\", version: 42}]}", "config").is_ok()
expect {
	p = PolicyPlan.decode("config = {schema_version: 2, roots: [\"src\"], profiles: [{name: \"shared\", path: \"policy.roc\", definition: \"profile\", sha256: \"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}]}", "config")?
	d = p.profiles.first()?
	PolicyPlan.finalize(p, [{ profile: { ..d, sha256: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" }, source: "profile = {schema_version: 2, rules: []}" }]).is_err()
}
expect plan("rules: [{id: \"phrase\", kind: Phrase, phrase: \"obviously\", accepted_words: [\"obviously\"]}]").is_err()
expect plan("rules: [{id: \"phrase\", kind: Phrase, phrase: \"obviously\"}], overrides: [{rule: \"phrase\", reason: \"wrong vocabulary mechanism\", changes: {accepted_identifiers: {add: [\"obviously\"]}}}]").is_err()
expect plan("rules: [{id: \"spell\", kind: Spelling, accepted_words: [\"two words\"]}]").is_err()
expect plan("rules: [{id: \"spell\", kind: Spelling, accepted_words: [\"thing_name\"]}]").is_err()
expect plan("${spelling}, overrides: [{rule: \"spell\", reason: \"bad words\", changes: {accepted_words: {add: [\"thing-name\"]}}}]").is_err()
expect plan("${spelling}, overrides: [{rule: \"spell\", reason: \"bad words\", changes: {accepted_words: {replace: [\"123\"]}}}]").is_err()
expect plan("rules: [{id: \"spell\", kind: Spelling, accepted_identifiers: [\"thing_name2\"]}]").is_ok()
expect {
	p = PolicyPlan.decode("config = {schema_version: 1, roots: [\"src\"], rules: [{kind: PublicDocs, scope: \"src/*\"}]}", "config")?
	PolicyPlan.for_file(p, "src/ordinary/File.roc").map_ok(List.len) == Ok(0) and PolicyPlan.for_file(p, "src/*/File.roc").map_ok(List.len) == Ok(1)
}
expect {
	p = plan(spelling)?
	r = p.rules.first()?
	var $many = []
	while $many.len() < 10001 {
		$many = $many.append(r)
	}
	PolicyPlan.finalize({ ..p, rules: $many }, []).is_err()
}
expect {
	p = plan("${spelling}, overrides: [{rule: \"spell\", reason: \"test\", changes: {level: Off}}]")?
	o = p.overrides.first()?
	var $many = []
	while $many.len() < 10001 {
		$many = $many.append(o)
	}
	PolicyPlan.finalize({ ..p, overrides: $many }, []).is_err()
}
expect {
	var $entries = []
	while $entries.len() < 33 {
		$entries = $entries.append("{name: \"p${$entries.len().to_str()}\", builtin: \"roc\", version: 1}")
	}
	PolicyPlan.decode("config = {schema_version: 2, roots: [\"src\"], profiles: [${Str.join_with($entries, ",")}]}", "config").is_err()
}

expect plan("${spelling}, overrides: [{rule: \"spell\", include: [\"src/**\"], reason: \"invalid aggregate setting\", changes: {require_match: False}}]").is_err()
