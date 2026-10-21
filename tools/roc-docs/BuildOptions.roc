## Native repository-publication options. All filesystem authority is explicit.
BuildOptions :: [].{
	Options : { mode : [Build, Prepare], markdown : Bool, workspace : Str, docs_directory : Str, entrypoint : Str, output_directory : Str, pdf_filename : Str, docs_version : Str, pdf_image_dpi : U64, title : Str, author : Str, language : Str, api_entrypoint : Str, roc_command : Str, site_url : Str }
	usage : Str
	usage = "roc-docs build|prepare --workspace DIRECTORY [--docs-directory docs] [--entrypoint index.adoc] [--output-directory .docs-out] [--pdf-filename manual.pdf] [--docs-version VERSION] [--pdf-image-dpi 72..300] [--title TITLE] [--author AUTHOR] [--language TAG] [--api-entrypoint FILE] [--roc-command EXECUTABLE] [--site-url HTTPS_URL] [--markdown true|false]"

	parse : List(Str) -> Try(Options, Str)
	parse = |args| {
		(mode, remaining) = match args {
			["build", .. as rest] => (Build, rest)
			["prepare", .. as rest] => (Prepare, rest)
			_ => return Err(usage)
		}
		initial : Options
		initial = { mode, markdown: mode == Build, workspace: ".", docs_directory: "docs", entrypoint: "index.adoc", output_directory: if (mode == Prepare) ".docs-prepared" else ".docs-out", pdf_filename: "manual.pdf", docs_version: "unreleased", pdf_image_dpi: 144, title: "", author: "", language: "", api_entrypoint: "", roc_command: "roc", site_url: "" }
		var $options = initial
		var $rest = remaining
		var $seen = []
		while !$rest.is_empty() {
			match $rest {
				[key, value, .. as tail] => {
					if $seen.contains(key) {
						return Err("duplicate option: ${key}")
					}
					$seen = $seen.append(key)
					$options = match key {
						"--markdown" => {
							..$options,
							markdown: match value {
								"true" => True
								"false" => False
								_ => return Err("markdown must be true or false")
							},
						}
						"--workspace" => { ..$options, workspace: value }
						"--docs-directory" => { ..$options, docs_directory: value }
						"--entrypoint" => { ..$options, entrypoint: value }
						"--output-directory" => { ..$options, output_directory: value }
						"--pdf-filename" => { ..$options, pdf_filename: value }
						"--docs-version" => { ..$options, docs_version: value }
						"--pdf-image-dpi" => { ..$options, pdf_image_dpi: U64.from_str(value).map_err(|_| "pdf-image-dpi must be an integer")? }
						"--title" => { ..$options, title: value }
						"--author" => { ..$options, author: value }
						"--language" => { ..$options, language: value }
						"--api-entrypoint" => { ..$options, api_entrypoint: value }
						"--site-url" => { ..$options, site_url: value }
						"--roc-command" => { ..$options, roc_command: value }
						_ => return Err("unknown option: ${key}")
					}
					$rest = tail
				}
				_ => return Err("option requires a value")
			}
		}
		options = $options
		if options.site_url != "" and (!options.site_url.starts_with("https://") or (options.site_url.drop_prefix("https://").split_on("/").first() ?? "") == "" or options.site_url.contains("@") or !safe_text(options.site_url) or options.site_url.contains("#") or options.site_url.contains("?") or options.site_url.contains(" ") or options.site_url.contains("\\")) {
			return Err("site-url must be an absolute HTTPS publication directory without query or fragment")
		}
		if options.workspace.is_empty() or !safe_text(options.workspace) {
			return Err("workspace must be a non-empty path")
		}
		for path in [options.docs_directory, options.output_directory, options.entrypoint, options.pdf_filename] {
			if !safe_path(path) {
				return Err("unsafe relative path: ${path}")
			}
		}
		if options.entrypoint.contains("/") or !(options.entrypoint.ends_with(".adoc") or options.entrypoint.ends_with(".md")) {
			return Err("entrypoint must be a top-level .adoc or .md file")
		}
		if options.pdf_filename.contains("/") or !options.pdf_filename.ends_with(".pdf") {
			return Err("pdf-filename must be a .pdf filename")
		}
		if overlaps(options.docs_directory, options.output_directory) {
			return Err("output-directory must not overlap documentation sources")
		}
		if options.api_entrypoint != "" and (!safe_path(options.api_entrypoint) or overlaps(options.api_entrypoint, options.output_directory)) {
			return Err("unsafe API entrypoint or overlapping output")
		}
		if options.pdf_image_dpi < 72 or options.pdf_image_dpi > 300 {
			return Err("pdf-image-dpi must be from 72 to 300")
		}
		for text in [options.docs_version, options.title, options.author, options.language, options.roc_command] {
			if !safe_text(text) {
				return Err("metadata and commands must be single lines")
			}
		}
		if options.language != "" and !options.language.to_utf8().all(|b| (b >= 'a' and b <= 'z') or (b >= 'A' and b <= 'Z') or (b >= '0' and b <= '9') or b == '-') {
			return Err("language must be a language tag")
		}
		if options.mode == Prepare and options.markdown {
			return Err("Markdown publication requires build")
		}
		if options.mode == Prepare and options.api_entrypoint != "" {
			return Err("API generation requires build")
		}
		Ok(options)
	}
	safe_text = |text| text.to_utf8().all(|b| b >= 32 and b != 127)
	safe_path = |path| !path.is_empty() and safe_text(path) and !path.starts_with("/") and !path.contains("\\") and !path.contains(":") and path.split_on("/").all(|part| part != "" and part != "." and part != ".." and part != ".git")
	overlaps = |a, b| a == b or a.starts_with("${b}/") or b.starts_with("${a}/")
}
