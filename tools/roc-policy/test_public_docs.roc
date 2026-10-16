app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
	parser: "https://github.com/lukewilliamboswell/roc-parser/releases/download/2.1.0-rc.1/6dvNG3Q2mx53i4YPJN2ZGK98WdXnBMMuceAmDsCq7BEd.tar.zst",
}
import parser.RocSyntax
import src/PublicDocs
main! = |_| Ok({})

issues = |path, source| PublicDocs.check(path, RocSyntax.parse_str(source))
expect issues("Thing.roc", "## A thing.\nThing := [].{\n## Returns one.\none : U64\none = 1\n}\nhelper = 2").is_empty()
expect issues("Thing.roc", "Thing :: [].{\none : U64\none = 1\n}").len() == 2
expect issues("Thing.roc", "## Type\nThing := [].{\n## Nested\nNested := [].{\nmethod = 1\n}\n}").len() == 1
expect issues("Thing.roc", "## Type\nThing := []\nPrivate := [].{\nmethod = 1\n}\nexpect True").is_empty()
expect issues("X.roc", "module [run]\n## Runs\nrun : U64\nrun = 1\nprivate = 2").is_empty()
expect issues("X.roc", "module [run]\nrun : U64\n## Runs\nrun = 1").is_empty()
expect issues("X.roc", "module [run]\n##\nrun = 1").len() == 1
expect issues("X.roc", "module [run]\n## Runs\n\nrun = 1").len() == 1
expect issues("X.roc", "module [run]\n## Runs\n# ordinary\nrun = 1").len() == 1
expect issues("X.roc", "module [run]\nfake = \"## Runs\"\nrun = 1").len() == 1
expect issues("X.roc", "module [run]\n## café\n\trun = 1").is_empty()
expect issues("main.roc", "package [Thing] {}\nimport Thing").is_empty()
expect issues("X.roc", "module [Thing.*]\nimport Thing").map(|issue| issue.rule) == ["documentation/incomplete"]
expect issues("X.roc", "module [Thing.run]\nimport Thing").map(|issue| issue.rule) == ["documentation/incomplete"]
expect issues("X.roc", "module [run]\nimport Thing exposing [run]").map(|issue| issue.rule) == ["documentation/incomplete"]
expect issues("X.roc", "module [Count]\n## Count\nCount : U64").is_empty()
expect issues("X.roc", "module [run]\nrun =").map(|issue| issue.rule) == ["documentation/incomplete"]

expect issues("X.roc", "module [run]\nhelper = 2 ## Helper\nrun = 1").len() == 1
expect issues("X.roc", "module [run]\r\n## Runs\r\nrun = 1").is_empty()
expect issues("X.roc", "module [run]\n##  \n## Runs\n##\nrun = 1").is_empty()
expect issues("Thing.roc", "Other := []").map(|issue| issue.rule) == ["documentation/incomplete"]
expect issues("main.roc", "helper = 1").is_empty()
expect issues("Thing.roc", "## Type\nThing := [].{\n## Method\nmethod = |x| { local = 1\nlocal + x }\n}").is_empty()
expect {
	result = issues("X.roc", "module [run]\n# café\nrun = 1")
	result.map(|issue| (issue.start, issue.end)) == [(21, 24)]
}
