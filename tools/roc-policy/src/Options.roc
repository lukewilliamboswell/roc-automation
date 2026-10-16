## Explicit commands keep source checking independent of compiler execution.
Options :: [].{
	Value : { command : Str, root : Str, config : Str, definition : Str, format : Str, compiler : Str }
	usage = "roc-policy <check|manifest|run-tests> [--root PATH] [--config PATH] [--definition NAME] [--format text|json] [--compiler ABSOLUTE_PATH]\nroc-policy --help | --licenses\nDefaults: root '.', config 'Blueprint.roc', definition 'repository_policy'.\ncheck/manifest never execute source or external tools.\nrun-tests explicitly invokes the supplied Roc compiler and checks static test PASS locations (cached results accepted).\nExit: 0 completed/pass; 1 policy violations; 2 invalid input or incomplete analysis."
	parse : List(Str) -> Try(Value, Str)
	parse = |args| {
		command = args.first() ?? ""
		if !["check", "manifest", "run-tests"].contains(command) {
			return Err("unknown or missing command")
		}
		var $value = { command, root: ".", config: "Blueprint.roc", definition: "repository_policy", format: "text", compiler: "" }
		var $remaining = args.drop_first(1)
		var $seen = []
		while !$remaining.is_empty() {
			(flag, argument, rest) = match $remaining {
				[key, supplied, .. as remaining] if key.starts_with("--") and !supplied.starts_with("--") => (key, supplied, remaining)
				_ => return Err("options require a value")
			}
			if $seen.contains(flag) {
				return Err("duplicate option ${flag}")
			}
			$seen = $seen.append(flag)
			$value = match flag {
				"--root" => { ..$value, root: argument }
				"--config" => { ..$value, config: argument }
				"--definition" => { ..$value, definition: argument }
				"--format" => { ..$value, format: argument }
				"--compiler" => { ..$value, compiler: argument }
				_ => return Err("unknown option ${flag}")
			}
			$remaining = rest
		}
		if !["text", "json"].contains($value.format) {
			return Err("format must be text or json")
		}
		if command == "run-tests" and !$value.compiler.starts_with("/") {
			return Err("run-tests requires --compiler with an absolute executable path")
		}
		if command != "run-tests" and $value.compiler != "" {
			return Err("--compiler is only valid for run-tests")
		}
		Ok($value)
	}
}
