app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.26.0-rc2/Gj3zVDr3qrQa6aTh7Z1zgUfe9NeBE6R76T5ho3xrc8af.tar.zst",
	parser: "https://github.com/lukewilliamboswell/roc-parser/releases/download/2.1.0-rc.2/CZPwnt8ziVkc4v27SX7y2c8shxvPToiXiU1Y55E8tobb.tar.zst",
}

import SyntaxHighlight

main! = |_args| Ok({})

expect SyntaxHighlight.highlight("value = 42").len() > 0
