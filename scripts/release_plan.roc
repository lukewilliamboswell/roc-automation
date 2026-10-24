#!/usr/bin/env roc-stable
app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
	roc: "nightly-2026-10-09-258ab27",
}

import cli.File
import cli.OsStr
import cli.Path
import cli.Stderr
import src/LifecyclePlan

## The trusted caller supplies context from event/run metadata and independently
## hashes the exact inert topology bytes. This utility never executes a task.
main! = |args| {
	result = plan!(args)
	match result {
		Ok({}) => Ok({})
		Err(reason) => {
			Stderr.line!("release-plan: ${reason.replace_each("::", ": :")}")?
			Err(Exit(1))
		}
	}
}

read! = |path| {
	reader = File.open_reader!(Path.utf8(path)).map_err(|_| "cannot open lifecycle input")?
	bytes = reader.read_up_to!(65537).map_err(|_| "cannot read lifecycle input")?
	if bytes.len() > 65536 {
		return Err("lifecycle input exceeds 64 KiB")
	}
	Str.from_utf8(bytes).map_err(|_| "lifecycle input must be UTF-8")
}

plan! = |args| {
	match args.map(OsStr.display) {
		["plan", context_path, topology_path, changes_path, output_path] => {
			context = LifecyclePlan.parse_context(read!(context_path)?)?
			topology = LifecyclePlan.parse_topology(read!(topology_path)?)?
			changes = LifecyclePlan.parse_changes(read!(changes_path)?)?
			plan = LifecyclePlan.plan(context, topology, changes)?
			Path.write_utf8!(Path.utf8(output_path), Json.to_str(plan)).map_err(|_| "cannot write plan output")
		}
		_ => Err("usage: release-plan plan CONTEXT TOPOLOGY CHANGES OUTPUT")
	}
}
