app [main!] { cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.26.0-rc2/Gj3zVDr3qrQa6aTh7Z1zgUfe9NeBE6R76T5ho3xrc8af.tar.zst", pkg: "../package/main.roc" }

import cli.Stdout
import pkg.Answer

main! = |_args| Stdout.line!("The answer is ${Answer.value.to_str()}")
