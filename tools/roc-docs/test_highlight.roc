app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.26.0-rc2/Gj3zVDr3qrQa6aTh7Z1zgUfe9NeBE6R76T5ho3xrc8af.tar.zst",
	parser: "https://github.com/lukewilliamboswell/roc-parser/releases/download/3.0.0/6xsRdRqenJrAb4wG7HaST4Xh349vD5sHAJDsKTK6E3dr.tar.zst",
}

import SyntaxHighlight

main! = |_args| Ok({})

expect SyntaxHighlight.highlight("value = 42").len() > 0
