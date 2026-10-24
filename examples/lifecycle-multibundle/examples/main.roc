app [main!] { cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.26.0-rc2/Gj3zVDr3qrQa6aTh7Z1zgUfe9NeBE6R76T5ho3xrc8af.tar.zst", core: "../core/main.roc", greeting: "../greeting/main.roc" }

import cli.Stdout
import greeting.Greeting
import core.Answer

main! = |_args| Stdout.line!("${Greeting.message}; twice is ${(Answer.value * 2).to_str()}")
