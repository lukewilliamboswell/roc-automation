#!/usr/bin/env roc-stable
app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.26.0-rc2/Gj3zVDr3qrQa6aTh7Z1zgUfe9NeBE6R76T5ho3xrc8af.tar.zst",
	roc: "nightly-2026-10-21-5349569",
}

import cli.Cmd
import cli.Duration
import cli.OsStr
import cli.Path
import cli.Stdout
import cli.Stderr

## Product-specific native adapter only. Shared tooling owns the lifecycle,
## bundling, candidate routing, inventories, notes, SBOMs and publication.
main! = |args| {
	result = run!(args.map(OsStr.display))
	match result {
		Ok({}) => Ok({})
		Err(reason) => {
			Stderr.line!(reason)?
			Err(Exit(1))
		}
	}
}

run! = |args| match args {
	["build", target] => {
		selected = match target {
			"linux" => { triple: "x86_64-linux-musl", folder: "x64glibc", filename: "libhost.a" }
			"macos" => { triple: "aarch64-macos", folder: "arm64mac", filename: "libhost.a" }
			"windows" => { triple: "x86_64-windows-msvc", folder: "x64win", filename: "host.lib" }
			_ => return Err("unknown native fixture target")
		}
		folder = "platform/targets/${selected.folder}"
		Path.create_all!(Path.utf8(folder)).map_err(|_| "cannot create host output directory")?
		object = "${folder}/host.o"
		archive = "${folder}/${selected.filename}"
		command!(["zig", "version"])?
		command!(["zig", "cc", "-target", selected.triple, "-O2", "-c", "host/host.c", "-o", object])?
		command!(["zig", "ar", "rcs", archive, object])?
		Path.delete!(Path.utf8(object)).map_err(|_| "cannot remove temporary object")?
		if target == "linux" {
			Path.create_all!(Path.utf8("platform/targets/x64musl")).map_err(|_| "cannot create musl output")?
			# The scalar, libc-free host has identical SysV ABI on both targets.
			Path.copy!(Path.utf8(archive), Path.utf8("platform/targets/x64musl/libhost.a")).map_err(|_| "cannot copy the shared Linux host")?
		}
		Ok({})
	}
	_ => Err("native fixture expects build TARGET")
}

command! = |argv| match argv {
	[program, .. as arguments] => {
		result = Cmd.new(OsStr.from_str(program)).args(arguments.map(OsStr.from_str)).stdout(Capture).stderr(Capture).timeout(Duration.seconds(120)).output_limit(MaxBytes(1_000_000)).manage_tree(Bool.True).run!().map_err(|_| "native command timed out or could not start")?
		if !result.stdout_bytes.is_empty() {
			Stdout.write!(Str.from_utf8_lossy(result.stdout_bytes)).map_err(|_| "cannot preserve native stdout")?
		}
		if !result.stderr_bytes.is_empty() {
			Stderr.write!(Str.from_utf8_lossy(result.stderr_bytes)).map_err(|_| "cannot preserve native stderr")?
		}
		if (result.status == Exited(0)) Ok({}) else Err("native compile/link/run failed")
	}
	_ => Err("empty native command")
}
