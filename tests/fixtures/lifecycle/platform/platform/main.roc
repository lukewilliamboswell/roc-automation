## A tiny real native platform. A provided readonly value is linked with a
## target-built C host, then the examples runtime links and executes a C harness.
platform ""
	requires {
		answer : I64,
		main! : () => {}
	}
	exposes [Contract]
	packages {}
	provides { "roc_answer": answer_for_host, "roc_main": main_for_host! }
	targets: {
		inputs_dir: "targets/",
		x64glibc: {
			inputs: ["libhost.a", app],
			output: Archive,
		},
		x64musl: {
			inputs: ["libhost.a", app],
			output: Archive,
		},
		arm64mac: {
			inputs: ["libhost.a", app],
			output: Archive,
		},
		x64win: {
			inputs: ["host.lib", app],
			output: Archive,
		},
	}

import Contract

answer_for_host : I64
answer_for_host = answer

main_for_host! : () => {}
main_for_host! = main!
