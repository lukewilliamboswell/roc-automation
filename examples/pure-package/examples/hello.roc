app [main!] {
	example: "../package/main.roc",
	pf: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
}

import pf.Stdout
import example.Greeting

# Print the package's greeting.
#
#     roc examples/hello.roc
main! = |_args| {
	Stdout.line!(Greeting.greeting)?
	Ok({})
}
