app [main!] {
	example: "../../package/main.roc",
	names: "../../data/package/main.roc",
	pf: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
}

import pf.Stdout
import example.Greeting
import names.Names
import Format

# A directory example: an application with a sibling module beside it, which
# depends on both packages this repository releases.
#
#     cd examples/greet && roc main.roc
main! = |_args| {
	for name in Names.everyone {
		Stdout.line!(Format.line(Greeting.greeting, name))?
	}
	Ok({})
}
