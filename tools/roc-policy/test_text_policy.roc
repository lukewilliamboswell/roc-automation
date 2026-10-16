app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
	parser: "https://github.com/lukewilliamboswell/roc-parser/releases/download/2.1.0-rc.1/6dvNG3Q2mx53i4YPJN2ZGK98WdXnBMMuceAmDsCq7BEd.tar.zst",
}

import parser.RocSyntax
import src/TextPolicy

spelling : TextPolicy.Rule
spelling = { kind: Spelling, channels: [Prose, Comments, Identifiers], forbidden: "", replacement: "", allow: [], words: [] }

phrase : TextPolicy.Rule
phrase = { kind: Phrase, channels: [Prose, Comments], forbidden: "it's worth noting", replacement: "", allow: [], words: [] }

expect TextPolicy.check_text("compiler boundary necessary receive unknown".to_utf8(), spelling).is_empty()
expect TextPolicy.check_text("complier boundry neccessary recieve unkown".to_utf8(), spelling).len() == 5
expect TextPolicy.check_text("é teh".to_utf8(), spelling).map(|i| (i.start, i.end)) == [(3, 6)]
expect TextPolicy.check_text("tehran other".to_utf8(), spelling).is_empty()
expect TextPolicy.check_text("TEH".to_utf8(), { ..spelling, allow: ["teh"] }).is_empty()
expect TextPolicy.check_text("frobnicate".to_utf8(), { ..spelling, forbidden: "frobnicate", replacement: "transform" }).len() == 1
expect TextPolicy.check_text("teh".to_utf8(), { ..spelling, channels: [Comments] }).is_empty()
expect TextPolicy.check_text("Worth noting noteworthy".to_utf8(), { ..phrase, forbidden: "noting" }).map(|i| (i.start, i.end)) == [(6, 12)]
expect TextPolicy.check_text("worth\n\tnoting".to_utf8(), { ..phrase, forbidden: "worth noting" }).len() == 1
expect TextPolicy.check_text("worth, noting".to_utf8(), { ..phrase, forbidden: "worth noting" }).is_empty()
expect TextPolicy.check_text("worth noting".to_utf8(), { ..phrase, forbidden: "worth noting", allow: ["Worth Noting"] }).is_empty()
expect {
	p = RocSyntax.parse_str("# recieve\nvalue = \"teh\"\n")
	p.status == Complete and TextPolicy.check_roc(p, spelling).len() == 1
}
expect {
	p = RocSyntax.parse_str("value = \"teh\"\n")
	TextPolicy.check_roc(p, { ..spelling, channels: [Strings] }).map(|i| (i.start, i.end)) == [(9, 12)]
}
expect {
	p = RocSyntax.parse_str("HTTPRecieveCount = 1\nrecieve_count2 = 2\n")
	TextPolicy.check_roc(p, spelling).len() == 2
}
expect {
	p = RocSyntax.parse_str("recieveCount = 1\n")
	TextPolicy.check_roc(p, { ..spelling, allow: ["recieveCount"] }).is_empty()
}
expect {
	p = RocSyntax.parse_str("## worth\n## noting\nvalue = 1\n")
	TextPolicy.check_roc(p, { ..phrase, forbidden: "worth noting" }).map(|i| (i.start, i.end)) == [(3, 18)]
}
expect {
	p = RocSyntax.parse_str("# worth\nvalue = 1\n# noting\n")
	TextPolicy.check_roc(p, { ..phrase, forbidden: "worth noting" }).is_empty()
}

main! = |_args| Ok({})

expect TextPolicy.check_text("It's worth\n noting".to_utf8(), phrase).len() == 1
expect TextPolicy.check_text("its worth noting".to_utf8(), phrase).is_empty()
expect {
	p = RocSyntax.parse_str("# worth # noting\nvalue = 1\n")
	TextPolicy.check_roc(p, { ..phrase, forbidden: "worth noting" }).is_empty()
}
expect {
	p = RocSyntax.parse_str("value = \"worth noting\"\n")
	TextPolicy.check_roc(p, { ..phrase, forbidden: "worth noting" }).is_empty()
}
expect {
	issues = TextPolicy.check_text("teh frobnicate".to_utf8(), { ..spelling, words: [("Frobnicate", "transform"), ("teh", "custom")] })
	issues.map(|i| i.message) == ["possible misspelling 'teh'; use 'custom'", "possible misspelling 'frobnicate'; use 'transform'"]
}
expect TextPolicy.check_text("éteh tehé".to_utf8(), spelling).is_empty()
expect {
	p = RocSyntax.parse_str("recieve2Count HTTPRecieveCount recieve_count = 1\n")
	TextPolicy.check_roc(p, { ..spelling, channels: [Identifiers] }).map(|i| (i.start, i.end)) == [(0, 7), (18, 25), (31, 38)]
}

expect TextPolicy.check_text("word word!".to_utf8(), { ..phrase, forbidden: "word!" }).map(|i| (i.start, i.end)) == [(5, 10)]
expect TextPolicy.check_text("'word' word".to_utf8(), { ..phrase, forbidden: "'word'" }).map(|i| (i.start, i.end)) == [(0, 6)]

# Bundled upstream entries beyond the original project-authored shortlist.
expect TextPolicy.check_text("abandonded aache".to_utf8(), spelling).len() == 2
expect TextPolicy.check_text("aache".to_utf8(), spelling).map(|i| i.message) == ["possible misspelling 'aache'; use 'ache' or 'cache'"]
expect TextPolicy.check_text("aache".to_utf8(), { ..spelling, words: [("aache", "projectword")] }).map(|i| i.message) == ["possible misspelling 'aache'; use 'projectword'"]
expect TextPolicy.check_text("aache".to_utf8(), { ..spelling, allow: ["aache"] }).is_empty()
expect TextPolicy.check_text("abacus frobnicate".to_utf8(), spelling).is_empty()
expect TextPolicy.check_text("сontains".to_utf8(), spelling).len() == 1
