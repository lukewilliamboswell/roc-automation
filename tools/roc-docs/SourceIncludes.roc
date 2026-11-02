import cli.Path
import BuildFiles
import BuildOptions
import Sha256
import parser.AsciiDoc

## Expand checked local listings before parsing, keeping their original bytes
## available to Preparation's source-fidelity oracle. Never execute examples.
SourceIncludes :: [].{
	File : { path : Str, source : Str }
	Origin : { document : Str, line : U64, path : Str, options : Str, source_sha256 : Str, selected_sha256 : Str }
	expand! : Path, Str, Str, List(File) => Try({ files : List(File), origins : List(Origin) }, Str)
	expand! = |workspace, docs_directory, output_directory, files| {
		var $out = []
		var $origins = []
		var $total = 0.U64
		var $count = 0.U64
		for file in files {
			if !file.path.ends_with(".adoc") {
				$out = $out.append(file)
			} else {
				var $delimiter = ""
				var $lines = []
				for entry in file.source.split_on("\n").map_with_index(|line, index| { line, index }) {
					line = entry.line
					location = "${file.path}:${(entry.index + 1).to_str()}"
					if $delimiter != "" and line.starts_with("include::") {
						$count = $count + 1
						if $count > 1024 {
							return Err("${location}: listing include count exceeds limit")
						}
						parts = line.drop_prefix("include::").split_on("[")
						if parts.len() != 2 or !line.ends_with("]") {
							return Err("${location}: malformed listing include")
						}
						target = parts.first() ?? ""
						settings = (parts.get(1) ?? "").drop_suffix("]")
						if target.contains(":") or target.contains("{") or target.starts_with("/") or target.contains("\\") {
							return Err("${location}: listing include must be a literal local path")
						}
						base = "${docs_directory}/${file.path}".split_on("/").drop_last(1)
						var $path = base
						for part in target.split_on("/") {
							if part == ".." {
								if $path.is_empty() {
									return Err("${location}: include escapes workspace")
								}
								$path = $path.drop_last(1)
							} else if part != "." and part != "" {
								$path = $path.append(part)
							}
						}
						relative = Str.join_with($path, "/")
						if BuildOptions.overlaps(relative, output_directory) {
							return Err("${location}: listing source overlaps publication output")
						}
						absolute = BuildFiles.inside!(workspace, relative).map_err(|e| "${location}: ${e}")?
						bytes = BuildFiles.read!(absolute, 1048576).map_err(|e| "${location}: ${e}")?
						$total = $total + bytes.len()
						if $total > 16777216 {
							return Err("${location}: listing includes exceed aggregate byte limit")
						}
						source = Str.from_utf8(bytes).map_err(|_| "${location}: include must be UTF-8")?
						selected = select(source, settings).map_err(|e| "${location}: ${relative}: ${e}")?
						if selected.split_on("\n").any(|l| l == $delimiter or l.starts_with("include::")) {
							return Err("${location}: include contains a closing delimiter or nested include")
						}
						$origins = $origins.append({ document: file.path, line: entry.index + 1, path: relative, options: settings, source_sha256: Sha256.hex(bytes), selected_sha256: Sha256.hex(selected.to_utf8()) })
						$lines = $lines.append(selected.drop_suffix("\n"))
					} else {
						if line == $delimiter {
							$delimiter = ""
						}
						else if $delimiter == "" and line.to_utf8().len() >= 4 and (line.to_utf8().all(|b| b == '-') or line.to_utf8().all(|b| b == '.')) {
							$delimiter = line
						}
						$lines = $lines.append(line)
					}
				}
				$out = $out.append({ path: file.path, source: Str.join_with($lines, "\n") })
			}
		}
		Ok({ files: $out, origins: $origins })
	}

	## Validate the renderer's narrower selector profile before using the pinned
	## parser's public selector. Its compatibility fallbacks must never turn an
	## invalid or conflicting selection into an apparently complete example.
	select : Str, Str -> Try(Str, Str)
	select = |source, settings| {
		if source.contains("\r") {
			return Err("listing include requires LF bytes")
		}
		var $tag = ""
		var $lines = ""
		var $dedent = False
		var $line_count = 0.U64
		for entry in settings.split_on(",") {
			option = entry.trim()
			if option.starts_with("tag=") and $tag == "" and $lines == "" {
				$tag = option.drop_prefix("tag=")
				if $tag == "" or !$tag.to_utf8().all(|b| (b >= 'a' and b <= 'z') or (b >= 'A' and b <= 'Z') or (b >= '0' and b <= '9') or b == '-' or b == '_') {
					return Err("invalid include tag")
				}
			} else if option.starts_with("lines=") and $lines == "" and $tag == "" {
				$lines = option.drop_prefix("lines=")
				parts = $lines.split_on("..")
				if parts.len() > 2 or !parts.all(|part| part != "" and part.to_utf8().all(|b| b >= '0' and b <= '9')) {
					return Err("include lines must be one positive line or a closed inclusive range")
				}
				first = U64.from_str(parts.first() ?? "").map_err(|_| "include line number exceeds limit")?
				last = U64.from_str(parts.last() ?? "").map_err(|_| "include line number exceeds limit")?
				if first == 0 or last < first {
					return Err("include lines require 1 <= first <= last")
				}
				$line_count = last - first + 1
			} else if option == "indent=0" and !$dedent {
				$dedent = True
			} else if option != "" or settings != "" {
				return Err("unsupported, conflicting or duplicate listing include option: ${option}")
			}
		}
		# Retain the stricter single-region tag contract: the parser also
		# accepts repeated regions, which can hide omitted surrounding source.
		if $tag != "" {
			var $active = False
			var $seen = False
			var $tagged = []
			for line in source.drop_suffix("\n").split_on("\n") {
				if line.trim().ends_with("tag::${$tag}[]") {
					if $active or $seen {
						return Err("duplicate or nested selected tag")
					}
					$active = True
					$seen = True
				} else if line.trim().ends_with("end::${$tag}[]") {
					if !$active {
						return Err("unmatched selected tag end")
					}
					$active = False
				} else if $active {
					$tagged = $tagged.append(line)
				}
			}
			if !$seen or $active {
				return Err("missing or unclosed selected tag")
			}
			# Public tag selection removes unrelated marker lines. Keep accepted
			# tagged listings byte-compatible, including their legacy dedent.
			if $dedent {
				width = $tagged.keep_if(|line| line.trim() != "").fold(
					1048576.U64,
					|n, line| {
						spaces = line.to_utf8().find_first_index(|b| b != ' ' and b != '\t') ?? line.to_utf8().len()
						if (spaces < n) spaces else n
					},
				)
				if width != 1048576 {
					$tagged = $tagged.map(|line| if (line.trim() == "") "" else Str.from_utf8_lossy(line.to_utf8().drop_first(width)))
				}
			}
			return Ok(Str.join_with($tagged, "\n").concat("\n"))
		}
		selection = AsciiDoc.select_include(source, settings)
		if !selection.problems.is_empty() {
			return Err("invalid listing include selection: ${Str.inspect(selection.problems)}")
		}
		if $lines != "" and selection.lines.len() != $line_count {
			return Err("include line selection extends beyond source")
		}
		Ok(selection.text.concat("\n"))
	}
}
