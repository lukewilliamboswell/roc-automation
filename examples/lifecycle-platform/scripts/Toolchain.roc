import pf.Config
import pf.Recipes
import pf.System

zig_version = "0.16.0"

Toolchain :: [].{
	programs : List(Config.Program)
	programs = [
		Fetch("zig", zig),
		Fetch("roc-stable", Recipes.roc_nightly("nightly-2026-10-21-5349569")),
	]
}

zig : System -> Config.Download
zig = |system| {
	base = "https://ziglang.org/download/${zig_version}"
	match system {
		"x86_64-linux" => {
			name = "zig-x86_64-linux-${zig_version}"
			Archive("${base}/${name}.tar.xz", TarXz, "${name}/zig")
		}
		"aarch64-darwin" => {
			name = "zig-aarch64-macos-${zig_version}"
			Archive("${base}/${name}.tar.xz", TarXz, "${name}/zig")
		}
		"x86_64-windows" => {
			name = "zig-x86_64-windows-${zig_version}"
			Archive("${base}/${name}.zip", Zip, "${name}/zig.exe")
		}
		_ => Unavailable
	}
}
