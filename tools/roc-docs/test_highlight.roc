app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.26.0-rc2/Gj3zVDr3qrQa6aTh7Z1zgUfe9NeBE6R76T5ho3xrc8af.tar.zst",
	parser: "https://github.com/lukewilliamboswell/roc-parser/releases/download/2.1.0-rc.1/6dvNG3Q2mx53i4YPJN2ZGK98WdXnBMMuceAmDsCq7BEd.tar.zst",
}

import SyntaxHighlight

main! = |_args| Ok({})

expect SyntaxHighlight.highlight("value = 42").len() > 0
