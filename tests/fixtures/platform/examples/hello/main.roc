app [main!] { pf: platform "../../platform/main.roc" }

import pf.Stdout

main! : List(Str) => Try({}, [StdoutErr(Str)])
main! = |_args| {
	Stdout.line!("hello")?
	Ok({})
}
