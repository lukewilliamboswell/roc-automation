## The compiler a script is written for, read from text without running one.
Pins := [].{

	## The release tag that the text of `Blueprint.roc` names as `roc-stable`.
	stable_tag : Str -> Try(Str, [NoStableCompiler])
	stable_tag = |blueprint|
		match Str.split_on(blueprint, "Command(\"roc-stable\", \"rocpkgs.") {
			[_, after] =>
				match Str.split_first(after, "\"") {
					Ok({ before, .. }) if before != "" => Ok(before)
					_ => Err(NoStableCompiler)
				}
			_ => Err(NoStableCompiler)
		}

	## Whether `source` is an application, which has an `app` header.
	is_app : Str -> Bool
	is_app = |source| header(Str.split_on(source, "\n")) != ""

	## The release named by the `roc` field of an app header.
	roc_pin : Str -> Try(Str, [NoPin])
	roc_pin = |source| {
		fields = match Str.split_first(header(Str.split_on(source, "\n")), "{") {
			Ok({ after, .. }) => Str.split_on(after, ",")
			Err(_) => []
		}
		match fields.keep_oks(|field| Str.split_first(field, ":")).keep_if(|field| field.before.trim() == "roc") {
			[{ after, .. }] =>
				match Str.split_on(after.trim(), "\"") {
					["", tag, ""] => Ok(tag)
					_ => Err(NoPin)
				}
			_ => Err(NoPin)
		}
	}
}

# The app header, from `app` up to the brace that closes it. A module has none.
header = |lines|
	match lines {
		[line, ..] if line.starts_with("app ") =>
			match Str.split_first(Str.join_with(lines, "\n"), "}") {
				Ok({ before, .. }) => before
				Err(_) => ""
			}
		[_, .. as rest] => header(rest)
		[] => ""
	}

script = "#!/usr/bin/env roc-stable\napp [main!] {\n\tcli: platform \"https://example.com/cli.tar.zst\",\n\troc: \"nightly-2026-10-09-258ab27\",\n}\n\nmain! = |_| { roc: \"not a header\" }\n"

expect Pins.stable_tag("\t\t\tOverlays([\"roc\"]),\n\t\t\tCommand(\"roc-stable\", \"rocpkgs.nightly-2026-10-09-258ab27\"),\n") == Ok("nightly-2026-10-09-258ab27")
expect Pins.stable_tag("# `roc-stable` is the release named below\n") == Err(NoStableCompiler)
expect Pins.stable_tag("Command(\"roc-stable\", \"rocpkgs.\")") == Err(NoStableCompiler)
expect Pins.is_app(script) and Pins.is_app("app [main!] {}\n")
expect !Pins.is_app("Pins := [].{\n\tapp = 1\n}\n") and !Pins.is_app("package [A] {}\n")
expect Pins.roc_pin(script) == Ok("nightly-2026-10-09-258ab27")
expect Pins.roc_pin("app [main!] { cli: platform \"https://example.com/cli.tar.zst\", roc : \"nightly-1\" }\n") == Ok("nightly-1")
expect Pins.roc_pin("app [main!] {\n\tcli: platform \"https://example.com/cli.tar.zst\",\n}\n\nmain! = |_| { roc: \"nightly-1\" }\n") == Err(NoPin)
expect Pins.roc_pin("app [main!] { heroc: \"nightly-1\" }\n") == Err(NoPin)
expect Pins.roc_pin("app [main!] { roc: \"a\", roc: \"b\" }\n") == Err(NoPin)
expect Pins.roc_pin("Pins := [].{\n\tpin = { roc: \"nightly-1\" }\n}\n") == Err(NoPin)
