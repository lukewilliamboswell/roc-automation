import parser.AsciiDoc
import parser.Xml
import parser.Markdown
import Html
import Sha256
import DiagramAdapter
import MarkdownExport

## Pure preparation using roc-parser's actual AsciiDoc AST and inline renderer.
## Files are inert data; diagram parsing and layout are pure and bounded.
Preparation :: [].{
	File : { path : Str, source : Str }
	Options : { entrypoint : Str, version : Str, pdf_filename : Str, site_url : Str }
	Result : { pages : List({ route : Str, source : Str, html : [MarkdownSource, Prepared({ body : Str, numbered : Bool, toc_depth : U64, remark : Str })] }), manual : Str, metadata : { title : Str, author : Str, version : Str, language : Str }, images : List(Str), diagrams : List(DiagramAdapter.Diagram) }
	prepare : List(File), Options -> Try(Result, Str)
	prepare = |files, options| {
		if files.len() > 1024 or files.any(|f| !safe_path(f.path) or (if (f.path.ends_with(".adoc")) f.source.to_utf8().len() > 262144 else f.source.to_utf8().len() > 16777216)) or files.fold(0.U64, |n, f| n + f.source.to_utf8().len()) > 33554432 {
			return Err("documentation inputs exceed path/file/size limits")
		}
		if files.any(|f| f.path.ends_with(".adoc") and f.source.contains("\r")) {
			return Err("AsciiDoc adapter requires LF source bytes")
		}
		if files.map(|f| f.path).len() != unique(files.map(|f| f.path)).len() {
			return Err("duplicate documentation source paths")
		}
		entry = files.find_first(|f| f.path == options.entrypoint).map_err(|_| "missing documentation entrypoint")?
		if entry.path.ends_with(".md") {
			return bounded(markdown(files, entry, options)?, 67108864)
		}
		if !entry.path.ends_with(".adoc") {
			return Err("entrypoint must be .adoc or .md")
		}
		raw = code_sources(files)?
		manual_doc = parse(files, entry, options, "pdf")?
		manual_normalized = identify(manual_doc.blocks, files, entry.path, [], [])?
		var $pages = []
		var $routes = []
		var $documents = []
		for file in files.keep_if(|f| f.path.ends_with(".adoc") and !f.path.contains("/")) {
			document = parse(files, file, options, "html5")?
			identified = identify(document.blocks, files, file.path, [], [])?
			route = file.path.drop_suffix(".adoc").concat(".html")
			for ref in identified.refs {
				$routes = $routes.append({ path: ref.path, local_id: ref.local_id, id: ref.id, title: ref.title, route })
			}
			$documents = $documents.append({ file, document, identified, route })
		}
		refs = manual_normalized.refs
		manual_state = initial(options, raw, refs, $routes, entry.path, "pdf", files)
		rendered_manual = blocks(manual_normalized.blocks, manual_state, 0)?
		var $images = rendered_manual.state.images
		var $diagrams = rendered_manual.state.diagrams
		for doc in $documents {
			state = { ..initial(options, raw, doc.identified.refs.concat(refs), $routes, doc.file.path, "html5", files), diagrams: $diagrams }
			result = blocks(doc.identified.blocks, state, 0)?
			html_result = html_blocks(doc.identified.blocks, { ..state, diagrams: result.state.diagrams }, 0)?
			$diagrams = result.state.diagrams
			page_attrs = attributes(files, doc.file, options)?
			numbered = page_attrs.any(|a| a.name == "sectnums")
			toc_depth = if (lookup(page_attrs, "toclevels") == "") 3 else U64.from_str(lookup(page_attrs, "toclevels")).map_err(|_| "toclevels must be an integer from 1 to 6")?
			if toc_depth < 1 or toc_depth > 6 {
				return Err("toclevels must be between 1 and 6")
			}
			$pages = $pages.append({ route: doc.route, source: result.text, html: Prepared({ body: html_result.text, numbered, toc_depth, remark: lookup(page_attrs, "revremark") }) })
			$images = $images.concat(result.state.images)
		}

		attrs = attributes(files, entry, options)?
		title = manual_doc.title
		bounded({ pages: $pages, manual: rendered_manual.text, metadata: { title, author: lookup(attrs, "author"), version: options.version, language: if (lookup(attrs, "lang") == "") "en" else lookup(attrs, "lang") }, images: unique($images), diagrams: DiagramAdapter.diagrams($diagrams) }, 67108864)
	}

	## Aggregate prepared text includes every page, its HTML when present, and
	## the combined manual. Count UTF-8 bytes after diagram adaptation in both formats.
	bounded : Result, U64 -> Try(Result, Str)
	bounded = |prepared, limit| {
		bytes = prepared.pages.fold(
			0.U64,
			|n, page| n + page.source.to_utf8().len() + (
				match page.html {
					Prepared(html) => html.body.to_utf8().len()
					MarkdownSource => 0
				}
			),
		) + prepared.manual.to_utf8().len()
		if bytes > limit {
			Err("prepared documentation exceeds output limit")
		} else Ok(prepared)
	}

}

safe_path = |s| !s.is_empty() and !s.starts_with("/") and !s.contains("\\") and s.split_on("/").all(|p| p != "" and p != "." and p != "..") and s.to_utf8().all(|b| b >= 32 and b != 127)

unique = |values| values.fold([], |out, item| if (out.contains(item)) out else out.append(item))

lookup = |attrs, name| attrs.find_first(|a| a.name == name).map_ok(|a| a.value) ?? ""

slug = |text| {
	var $out = []
	var $dash = True
	for b in text.to_utf8() {
		if b == ' ' or b == '-' or b == '.' {
			if !$dash {
				$out = $out.append('-')
				$dash = True
			}
		}
		else if b >= 128 or (b >= 'a' and b <= 'z') or (b >= 'A' and b <= 'Z') or (b >= '0' and b <= '9') or b == '_' {
			$out = $out.append(if (b >= 'A' and b <= 'Z') b + 32 else b)
			$dash = False
		}
	}
	value = Str.from_utf8_lossy($out).drop_suffix("-")
	if (value == "") "section" else value
}

base_attributes = |options, backend| [{ name: "docs-version", value: options.version }, { name: "backend", value: backend }, { name: "backend-${backend}", value: "" }, { name: "idprefix", value: "" }, { name: "idseparator", value: "-" }]

parse = |files, file, options, backend| {
	source = if (backend == "pdf") ":backend-html5!:\n:backend-html5-doctype-article!:\n:basebackend-html!:\n${file.source}" else file.source
	document = AsciiDoc.parse_with(source, { includes: files, attributes: base_attributes(options, backend), max_depth: 32, expand_includes: True }).map_err(|e| "${file.path}: ${Str.inspect(e)}")?
	# The document title is separately represented in roc-parser's AST.
	title = inline_plain(document.title)?
	attrs = AsciiDoc.attributes(base_attributes(options, backend))
	document_id = match document.blocks.first() {
		Ok(AsciiDoc.Block.Rich(first)) => lookup(AsciiDoc.materialize_attributes(first.attrs), "__document-id")
		_ => ""
	}
	root = AsciiDoc.Block.Rich({ context: "section", source: "", title, style: "", id: document_id, attrs, blocks: document.blocks, level: 0 })
	Ok({ title, blocks: [root] })
}

attributes = |files, file, options| {
	# Read document metadata from the actual first block's scoped bindings.
	document = AsciiDoc.parse_with(file.source, { includes: files, attributes: base_attributes(options, "pdf"), max_depth: 32, expand_includes: True }).map_err(|e| Str.inspect(e))?
	match document.blocks.first() {
		Ok(AsciiDoc.Block.Rich(node)) => Ok(AsciiDoc.materialize_attributes(node.attrs))
		_ => Ok(
			file.source.split_on("\n").keep_oks(
				|line| {
					if !line.starts_with(":") or !line.drop_prefix(":").contains(":") {
						return Err(Missing)
					}
					parts = line.drop_prefix(":").split_on(":")
					Ok({ name: parts.first() ?? "", value: Str.join_with(parts.drop_first(1), ":").trim() })
				},
			),
		)
	}
}

Node : { context : Str, source : Str, title : Str, style : Str, id : Str, attrs : AsciiDoc.Attributes, blocks : List(AsciiDoc.Block), level : U64 }

node = |block| match block {
	AsciiDoc.Block.Rich(n) => n
	AsciiDoc.Block.Section(n) => { context: "section", source: "", title: n.title, style: "", id: "", attrs: AsciiDoc.attributes([]), blocks: n.blocks, level: n.level }
	AsciiDoc.Block.Paragraph(s) => { context: "paragraph", source: s, title: "", style: "", id: "", attrs: AsciiDoc.attributes([]), blocks: [], level: 0 }
	AsciiDoc.Block.Listing(s) => { context: "listing", source: s, title: "", style: "", id: "", attrs: AsciiDoc.attributes([]), blocks: [], level: 0 }
	AsciiDoc.Block.Literal(s) => { context: "literal", source: s, title: "", style: "", id: "", attrs: AsciiDoc.attributes([]), blocks: [], level: 0 }
	AsciiDoc.Block.ThematicBreak => { context: "thematic_break", source: "", title: "", style: "", id: "", attrs: AsciiDoc.attributes([]), blocks: [], level: 0 }
}

Ref : { path : Str, local_id : Str, id : Str, title : Str }

Route : { path : Str, local_id : Str, id : Str, title : Str, route : Str }

identify : List(AsciiDoc.Block), List(Preparation.File), Str, List(Str), List(Ref) -> Try({ blocks : List(AsciiDoc.Block), used : List(Str), refs : List(Ref) }, Str)
identify = |input, files, path, used, refs| {
	var $used = used
	var $refs = refs
	var $out = []
	var $path = path
	for block in input {
		n = node(block)
		if n.context == "section" or n.context == "floating_title" {
			if n.level == 1 {
				candidates = files.keep_if(|f| f.source.split_on("\n").any(|line| line == "= ${n.title}"))
				if candidates.len() > 1 {
					return Err("ambiguous source origin for included chapter ${n.title}")
				}
				if candidates.len() == 1 {
					$path = (candidates.first() ?? { path, source: "" }).path
				}
			}
			base = if (n.id == "") slug(n.title) else n.id
			local_count = $refs.keep_if(|r| r.path == $path and r.local_id == base).len()
			if n.id != "" and local_count > 0 {
				return Err("duplicate explicit heading ID ${base} in ${$path}")
			}
			local_id = if (local_count == 0) base else "${base}-${(local_count + 1).to_str()}"
			var $id = local_id
			var $suffix = 2.U64
			while $used.contains($id) {
				$id = if ($suffix == 2) "${slug($path.drop_suffix(".adoc"))}-${local_id}" else "${slug($path.drop_suffix(".adoc"))}-${local_id}-${($suffix - 1).to_str()}"
				$suffix = $suffix + 1
			}
			$used = $used.append($id)
			$refs = $refs.append({ path: $path, local_id, id: $id, title: n.title })
			children = identify(n.blocks, files, $path, $used, $refs)?
			$used = children.used
			$refs = children.refs
			$out = $out.append(AsciiDoc.Block.Rich({ ..n, id: $id, blocks: children.blocks }))
		} else {
			children = identify(n.blocks, files, $path, $used, $refs)?
			$used = children.used
			$refs = children.refs
			$out = $out.append(AsciiDoc.Block.Rich({ ..n, blocks: children.blocks }))
		}
	}
	Ok({ blocks: $out, used: $used, refs: $refs })
}

RawCode : { normalized : Str, source : Str }

trim_source_end = |line| {
	bytes = line.to_utf8()
	var $n = bytes.len()
	while $n > 0 and [32, 9, 13, 11, 12, 0].contains(bytes.get($n - 1) ?? 0) {
		$n = $n - 1
	}
	Str.from_utf8_lossy(bytes.take_first($n))
}

normalize_code = |source| Str.join_with(source.split_on("\n").map(trim_source_end), "\n").drop_suffix("\n")

code_sources = |files| {
	var $found = []
	for file in files.keep_if(|f| f.path.ends_with(".adoc")) {
		var $delimiter = ""
		var $body = []
		for line in file.source.split_on("\n") {
			if $delimiter != "" {
				if line == $delimiter {
					source = "${Str.join_with($body, "\n")}\n"
					$found = $found.append({ normalized: normalize_code(source), source })
					$delimiter = ""
					$body = []
				} else {
					$body = $body.append(line)
				}
			} else if line.to_utf8().len() >= 4 and (line.to_utf8().all(|b| b == '-') or line.to_utf8().all(|b| b == '.')) {
				$delimiter = line
			}
		}
		if $delimiter != "" {
			return Err("unclosed source delimiter in ${file.path}")
		}
	}
	Ok($found)
}

State : { options : Preparation.Options, raw : List(RawCode), refs : List(Ref), routes : List(Route), path : Str, page : Str, backend : Str, images : List(Str), figure : U64, table_count : U64, sources : List(Preparation.File), diagrams : DiagramAdapter.Cache, ordinal : U64 }

initial = |options, raw, refs, routes, path, backend, sources| { options, raw, refs, routes, path, page: path, backend, images: [], figure: 0, table_count: 0, sources, diagrams: DiagramAdapter.empty_cache, ordinal: 0 }

blocks : List(AsciiDoc.Block), State, U64 -> Try({ text : Str, state : State }, Str)
blocks = |input, state, depth| {
	if depth > 64 {
		return Err("AsciiDoc nesting exceeds adapter limit")
	}
	var $state = state
	var $out = ""
	for block in input {
		n = node(block)
		if n.context == "section" and n.level == 1 {
			match $state.refs.find_first(|r| r.id == n.id) {
				Ok(ref) => {
					$state = { ..$state, path: ref.path, ordinal: if ($state.path == ref.path) $state.ordinal else 0 }
				}
				_ => {}
			}
		}
		attrs = AsciiDoc.materialize_attributes(n.attrs)
		if lookup(attrs, "subs") != "" or lookup(attrs, "indent") != "" {
			return Err("${source_location(n.source, $state)}: custom substitution/indent modes are unsupported")
		}
		title = if (n.context == "section" or n.context == "floating_title") "" else if (n.title == "") "" else "**${inline(n.title, n.attrs, $state)?}**\n\n"
		var $text = title
		match n.context {
			"section" | "floating_title" => {
				if n.level > 5 {
					return Err("heading exceeds six Markdown levels")
				}
				hashes = Str.join_with(List.repeat("#", n.level + 1), "")
				result = blocks(n.blocks, $state, depth + 1)?
				$state = result.state
				$text = "${hashes} ${inline(n.title, n.attrs, $state)?} {#${n.id}}\n\n${result.text}"
			}
			"paragraph" => {
				$text = "${$text}${inline(n.source, n.attrs, $state)?}\n\n"
			}
			"listing" | "literal" => {
				if n.style == "mermaid" or lookup(attrs, "1") == "mermaid" {
					body = "${n.source}\n"
					rendered = DiagramAdapter.admit({ source: body, path: $state.path, ordinal: $state.ordinal + 1, cache: $state.diagrams }).map_err(DiagramAdapter.message)?
					diagram = rendered.diagram
					$state = { ..$state, diagrams: rendered.cache, ordinal: $state.ordinal + 1 }
					$state = { ..$state, images: $state.images.append(DiagramAdapter.href(diagram)) }
					$text = "${$text}![${escape_text(if (n.title == "") "Diagram" else n.title).replace_each("&", "\\&")}](${DiagramAdapter.href(diagram)})\n\n"
				} else {
					candidates = $state.raw.keep_if(|r| r.normalized == normalize_code(n.source))
					values = unique(candidates.map(|r| r.source))
					if values.len() > 1 {
						return Err("ambiguous original source bytes after upstream whitespace normalization")
					}
					if values.is_empty() and n.context == "listing" {
						return Err("${source_location(n.source, $state)}: listing requires a source-preserving delimited original block")
					}
					source = values.first() ?? "${n.source}\n"
					language = lookup(attrs, "2")
					var $fence = "```"
					while source.contains($fence) {
						$fence = "${$fence}`"
					}
					$text = "${$text}${$fence}${language}\n${source}${$fence}\n\n"
				}
			}
			"table" => {
				$text = "${$text}${table(n.source, attrs, n.attrs, $state).map_err(|e| "${source_location(n.source, $state)}: table: ${e}")?}\n\n"
			}
			"image" => {
				if !safe_path(n.source) {
					return Err("unsafe local image path")
				}
				alt = lookup(attrs, "1")
				$state = { ..$state, images: $state.images.append(n.source) }
				$text = "${$text}![${alt}](${n.source})\n\n"
			}
			"ulist" | "olist" | "dlist" => {
				var $index = 1.U64
				for item in n.blocks {
					it = node(item)
					if it.context != "list_item" {
						return Err("unexpected AsciiDoc list child")
					}
					marker = if (n.context == "olist") "${$index.to_str()}. " else "- "
					label = if (n.context == "dlist") "**${inline(it.title, it.attrs, $state)?}**: " else ""
					body = inline(it.source, it.attrs, $state)?
					child = blocks(it.blocks, $state, depth + 1)?
					$state = child.state
					$text = "${$text}${marker}${label}${body}\n"
					if child.text != "" {
						$text = "${$text}\n${indent(child.text)}\n"
					}
					$index = $index + 1
				}
				$text = "${$text}\n"
			}
			"sidebar" | "open" | "example" | "quote" | "admonition" => {
				result = blocks(n.blocks, $state, depth + 1)?
				$state = result.state
				label = if (n.context == "admonition" and n.style != "") "**${n.style}**\n\n" else ""
				body = "${label}${$text}${if (n.source == "") "" else "${inline(n.source, n.attrs, $state)?}\n\n"}${result.text}"
				$text = if n.context == "sidebar" or n.context == "admonition" {
					Str.join_with(body.trim().split_on("\n").map(|line| "> ${line}"), "\n").concat("\n\n")
				} else body
			}
			"thematic_break" => {
				$text = "${$text}---\n\n"
			}
			"__document_header" | "toc" => {}
			_ => return Err("${source_location(n.source, $state)}: unsupported AsciiDoc block context: ${n.context}")
		}
		$out = "${$out}${$text}"
	}
	Ok({ text: $out, state: $state })
}

indent = |s| Str.join_with(s.split_on("\n").map(|line| if (line == "") "" else "    ${line}"), "\n")

source_location = |source, state| {
	file = state.sources.find_first(|f| f.path == state.path) ?? { path: state.path, source: "" }
	if source == "" {
		return state.path
	}
	parts = file.source.split_on(source)
	if parts.len() != 2 {
		return state.path
	}
	line = (parts.first() ?? "").split_on("\n").len()
	"${state.path}:${line.to_str()}"
}

table_widths = |source, attrs| {
	cols = lookup(attrs, "cols")
	var $widths = []
	if cols == "" {
		first = source.split_on("\n").find_first(|line| line.trim() != "") ?? ""
		$widths = List.repeat(1.U64, first.split_on("|").len() - 1)
	} else {
		for value in cols.split_on(",") {
			width = if value.trim() == "" {
				1.U64
			} else {
				U64.from_str(value.trim()).map_err(|_| "column weights must be positive integers")?
			}
			if width == 0 or width > 10000 {
				return Err("column weights must be from 1 to 10000")
			}
			$widths = $widths.append(width)
		}
	}
	if $widths.is_empty() or $widths.len() > 64 {
		return Err("tables require from 1 to 64 columns")
	}
	Ok($widths)
}

table = |source, attrs, scope, state| {
	if lookup(attrs, "format") != "" or lookup(attrs, "separator") != "" {
		return Err("only PSV tables are supported by renderer adapter")
	}
	if !lookup(attrs, "cols").to_utf8().all(|b| (b >= '0' and b <= '9') or b == ',' or b == ' ') or !lookup(attrs, "options").split_on(",").all(|o| o == "header" or o == "") or !lookup(attrs, "options").split_on(",").contains("header") or source.contains("\\|") {
		return Err("table styles/spans/escaped separators/non-header tables are unsupported")
	}
	columns = table_widths(source, attrs)?.len()
	var $cells = []
	for entry in source.split_on("\n").map_with_index(|line, index| { line, index }) {
		parts = entry.line.split_on("|")
		prefix = (parts.first() ?? "").trim()
		starts_with_digit = prefix.to_utf8().first().map_ok(|b| b >= '0' and b <= '9') ?? False
		if parts.len() > 1 and prefix != "" and !prefix.contains(" ") and (starts_with_digit or ["a", "h", "e", "s", "l", "m", "v", "d", "<", ">", "^"].contains(prefix)) {
			return Err("table cell styles and spans are unsupported")
		}
		if prefix != "" {
			if $cells.is_empty() {
				return Err("${state.path}: table line ${(entry.index + 1).to_str()}: continuation before first cell")
			}
			last = $cells.last() ?? ""
			$cells = $cells.drop_last(1).append("${last} ${prefix}")
		}
		if parts.len() > 1 {
			$cells = $cells.concat(parts.drop_first(1).map(|v| v.trim()))
		}
	}
	if columns == 0 or $cells.is_empty() or $cells.len() % columns != 0 {
		return Err("${state.path}: ragged or empty PSV table: ${$cells.len().to_str()} cells for ${columns.to_str()} columns")
	}
	var $out = ""
	var $offset = 0.U64
	while $offset < $cells.len() {
		var $row = []
		for value in $cells.drop_first($offset).take_first(columns) {
			$row = $row.append(inline(value, scope, state)?)
		}
		$out = "${$out}| ${Str.join_with($row, " | ")} |\n"
		if $offset == 0 {
			$out = "${$out}| ${Str.join_with(List.repeat("---", columns), " | ")} |\n"
		}
		$offset = $offset + columns
	}
	Ok($out)
}

inline = |source, attrs, state| {
	location = source_location(source, state)
	protected = protect_code(source, attrs).map_err(|e| "${location}: inline: ${e}")?
	html = AsciiDoc.render_inline_attributes(protected.text, attrs)
	xml = Xml.parse_str("<root>${html.replace_each("<br>", "<br/>").replace_each("&nbsp;", "&#160;")}</root>").map_err(|e| "${location}: unsupported AsciiDoc inline representation: ${Str.inspect(e)}")?
	nodes = match xml.root {
		Xml.Node.Element(e) => e.children
		_ => []
	}
	var $result = xml_markdown(nodes, state, False).map_err(|e| "${location}: inline: ${e}")?
	for code in protected.codes {
		var $fence = "`"
		while code.source.contains($fence) {
			$fence = "${$fence}`"
		}
		$result = $result.replace_each(code.marker, "${$fence}${code.source}${$fence}")
	}
	Ok($result)
}

# rc2's inline HTML renderer misnests strong markup inside monospace backticks.
# Preserve monospace spans before invoking it; only ordinary attributes expand.
protect_code : Str, AsciiDoc.Attributes -> Try({ text : Str, codes : List({ marker : Str, source : Str }) }, Str)
protect_code = |source, attrs| {
	var $prefix = "ROCINLINECODETOKEN"
	while source.contains($prefix) {
		$prefix = "${$prefix}X"
	}
	parts = source.split_on("`")
	if parts.len() % 2 == 0 {
		return Err("unbalanced AsciiDoc monospace delimiter")
	}
	var $codes = []
	var $text = ""
	for entry in parts.map_with_index(|text, index| { text, index }) {
		if entry.index % 2.U64 == 0.U64 {
			$text = "${$text}${entry.text}"
		}
		else {
			literal = entry.text.starts_with("+") and entry.text.ends_with("+")
			var $value = if (literal) entry.text.drop_prefix("+").drop_suffix("+") else entry.text
			for attr in AsciiDoc.materialize_attributes(attrs) {
				if !literal and !attr.name.starts_with("\u(0)") and !$value.is_empty() {
					pattern = "{${attr.name}}"
					occurrences = $value.split_on(pattern).len() - 1
					expanded = $value.to_utf8().len() + occurrences * attr.value.to_utf8().len()
					if expanded > 1048576 {
						return Err("inline code attribute expansion exceeds resource limit")
					}
					$value = $value.replace_each(pattern, attr.value)
				}
			}
			marker = "${$prefix}${entry.index.to_str()}END"
			$codes = $codes.append({ marker, source: $value })
			$text = "${$text}${marker}"
		}
	}
	Ok({ text: $text, codes: $codes })
}

inline_plain = |html| {
	xml = Xml.parse_str("<root>${html.replace_each("&nbsp;", "&#160;")}</root>").map_err(|_| "invalid rendered document title")?
	Ok(xml.root.text())
}

escape_text = |s| s.replace_each("\\", "\\\\").replace_each("*", "\\*").replace_each("_", "\\_").replace_each("[", "\\[").replace_each("]", "\\]").replace_each("<", "\\<").replace_each(">", "\\>")

xml_markdown : List(Xml.Node), State, Bool -> Try(Str, Str)
xml_markdown = |nodes, state, code| {
	var $out = ""
	for item in nodes {
		value = match item {
			Xml.Node.Text(s) => if (code) s else escape_text(s)
			Xml.Node.Element(e) => {
				child = xml_markdown(e.children, state, e.name == "code")?
				match e.name {
					"strong" => "**${child}**"
					"em" => "*${child}*"
					"code" => {
						var $fence = "`"
						while child.contains($fence) {
							$fence = "${$fence}`"
						}
						"${$fence}${child}${$fence}"
					}
					"a" => {
						href = lookup(e.attributes, "href")
						resolved = resolve(href, state)?
						label = if (child.starts_with("\\[") and child.ends_with("\\]")) resolved.title else child
						"[${label}](${resolved.href})"
					}
					"br" => "  \n"
					_ => return Err("unsupported AsciiDoc inline element: ${e.name}")
				}
			}
		}
		$out = "${$out}${value}"
	}
	Ok($out)
}

resolve : Str, State -> Try({ href : Str, title : Str }, Str)
resolve = |href, state| {
	if href.starts_with("https://") or href.starts_with("http://") or href.starts_with("mailto:") {
		return Ok({ href, title: href })
	}
	parts = href.split_on("#")
	authored = parts.first() ?? ""
	path = if (authored.ends_with(".html") and state.routes.any(|r| r.path == authored.drop_suffix(".html").concat(".adoc"))) authored.drop_suffix(".html").concat(".adoc") else authored
	anchor = if (parts.len() > 1) parts.get(1) ?? "" else ""
	if path == "" and anchor != "" {
		preferred : Try(Ref, [NotFound])
		preferred = match state.refs.find_first(|r| r.path == state.path and r.local_id == anchor) {
			Ok(v) => Ok(v)
			Err(_) => state.refs.find_first(|r| r.local_id == anchor)
		}
		ref = preferred.map_err(|_| "unknown AsciiDoc anchor ${href}")?
		if state.backend == "pdf" {
			return Ok({ href: "#${ref.id}", title: ref.title })
		}
		return html_reference(ref, state)
	}
	if path != "" and path.ends_with(".adoc") {
		ref = match state.refs.find_first(|r| r.path == path and (anchor == "" or r.local_id == anchor)) {
			Ok(found) => found
			Err(_) => {
				route = state.routes.find_first(|r| r.path == path and (anchor == "" or r.local_id == anchor)).map_err(|_| "${state.path}: unknown publication reference ${href}")?
				if state.backend == "html5" {
					return Ok({ href: "${route.route}#${route.id}", title: route.title })
				}
				if state.options.site_url != "" {
					return Ok({ href: "${state.options.site_url.drop_suffix("/")}/${route.route}#${route.id}", title: route.title })
				}
				return Err("${state.path}: PDF reference ${href} needs --site-url because the chapter is outside the manual")
			}
		}
		if state.backend == "pdf" {
			return Ok({ href: "#${ref.id}", title: ref.title })
		}
		return html_reference(ref, state)
	}
	if !safe_path(path) {
		return Err("${state.path}: unsafe AsciiDoc link ${href}")
	}
	# Only links to the selected manual name are rewritten as its download.
	# Other PDFs and API/download routes retain their authored identity.
	local = href
	if path == state.options.pdf_filename and state.backend == "pdf" {
		root = state.refs.first().map_err(|_| "manual download has no document destination")?
		return Ok({ href: "#${root.id}", title: href })
	}
	if state.backend == "pdf" {
		if state.options.site_url == "" {
			return Err("${state.path}: PDF link ${href} needs --site-url or an absolute URL")
		}
		return Ok({ href: "${state.options.site_url.drop_suffix("/")}/${local}", title: href })
	}
	Ok({ href: local, title: href })
}

markdown = |files, entry, options| {
	var $images = []
	var $pages = []
	var $diagrams = DiagramAdapter.empty_cache
	var $manual = entry.source
	for file in files.keep_if(|f| f.path.ends_with(".md") and !f.path.contains("/")) {
		adapted = DiagramAdapter.markdown({ blocks: Markdown.parse_str(file.source), path: file.path, ordinal: 0, cache: $diagrams, depth: 0 }).map_err(DiagramAdapter.message)?
		$diagrams = adapted.cache
		prepared_source = if (adapted.ordinal == 0) file.source else MarkdownExport.serialize(adapted.blocks)?
		if file.path == entry.path {
			$manual = prepared_source
		}
		$images = $images.concat(markdown_images(adapted.blocks))
		$pages = $pages.append({ route: file.path.drop_suffix(".md").concat(".html"), source: prepared_source, html: MarkdownSource })
	}
	title = entry.source.split_on("\n").find_first(|line| line.starts_with("# ")).map_ok(|line| line.drop_prefix("# ").split_on(" {#").first() ?? "Manual") ?? "Manual"
	Ok({ pages: $pages, manual: $manual, metadata: { title, author: "", version: options.version, language: "en" }, images: unique($images), diagrams: DiagramAdapter.diagrams($diagrams) })
}

markdown_images : List(Markdown) -> List(Str)
markdown_images = |blocks_in| blocks_in.fold(
	[],
	|found, block| match block {
		Paragraph(items) | Heading({ content: items, .. }) => found.concat(inline_images(items))
		Blockquote(children) => found.concat(markdown_images(children))
		ListBlock({ items, .. }) => items.fold(found, |out, item| out.concat(markdown_images(item.blocks)))
		Table({ header, rows, .. }) => header.concat(rows.fold([], |out, row| out.concat(row))).fold(found, |out, cell| out.concat(inline_images(cell)))
		_ => found
	},
)

inline_images : List(Markdown.Inline) -> List(Str)
inline_images = |items| items.fold(
	[],
	|out, item| match item {
		Image({ target, .. }) => out.append(target.href)
		Strong(children) | Emphasis(children) | Strikethrough(children) | Link({ label: children, .. }) => out.concat(inline_images(children))
		_ => out
	},
)

route_for = |ref, state| {
	candidates = state.routes.keep_if(|r| r.path == ref.path and r.local_id == ref.local_id)
	preferred = candidates.find_first(|r| r.route == ref.path.drop_suffix(".adoc").concat(".html"))
	match preferred {
		Ok(route) => Ok(route)
		Err(_) => match candidates.find_first(|r| r.route != state.options.entrypoint.drop_suffix(".adoc").concat(".html")) {
			Ok(route) => Ok(route)
			Err(_) => candidates.first().map_err(|_| "unroutable AsciiDoc reference ${ref.local_id}")
		}
	}
}

# HTML retains the AsciiDoc structure that ordinary Markdown cannot express.
# Inline conversion still passes through the same validated link/escape path.
html_inline = |value, scope, state| {
	prepared = inline(value, scope, state)?
	match Markdown.parse_str(prepared) {
		[Paragraph(content)] => Html.inlines(content, []).map_err(|e| Str.inspect(e))
		[] => Ok("")
		_ => Err("unexpected block in inline AsciiDoc content")
	}
}

html_blocks : List(AsciiDoc.Block), State, U64 -> Try({ text : Str, state : State }, Str)
html_blocks = |input, state, depth| {
	if depth > 64 {
		return Err("AsciiDoc HTML nesting exceeds adapter limit")
	}
	var $state = state
	var $out = ""
	for block in input {
		n = node(block)
		if n.context == "section" and n.level == 1 {
			match $state.refs.find_first(|r| r.id == n.id) {
				Ok(ref) => {
					$state = { ..$state, path: ref.path, ordinal: if ($state.path == ref.path) $state.ordinal else 0 }
				}
				_ => {}
			}
		}
		attrs = AsciiDoc.materialize_attributes(n.attrs)
		title = html_inline(n.title, n.attrs, $state)?
		block_title = if (title == "") "" else "<div class=\"block-title\">${title}</div>"
		var $text = ""
		match n.context {
			"section" | "floating_title" => {
				var $occurrences = 0.U64
				var $found = False
				for ref in $state.refs {
					if !$found and ref.title == n.title {
						$occurrences = $occurrences + 1
					}
					if ref.id == n.id {
						$found = True
					}
				}
				legacy = "${slug(n.title)}-${$occurrences.to_str()}"
				alias = if ($occurrences > 1 and legacy != n.id and !$state.refs.any(|r| r.id == legacy)) "<span class=\"anchor-alias\" id=\"${Html.escape(legacy)}\"></span>" else ""
				child = html_blocks(n.blocks, $state, depth + 1)?
				$state = child.state
				$text = "${alias}${Html.heading_html(n.level + 1, n.id, title)}\n${child.text}"
			}
			"paragraph" => {
				$text = "${block_title}<p>${html_inline(n.source, n.attrs, $state)?}</p>"
			}
			"listing" | "literal" => {
				prepared = blocks([block], $state, depth + 1)?
				$state = prepared.state
				if n.style == "mermaid" or lookup(attrs, "1") == "mermaid" {
					digest = Sha256.hex("${n.source}\n".to_utf8())
					diagram = DiagramAdapter.find_digest($state.diagrams, digest).map_err(|_| "missing admitted diagram")?
					$state = { ..$state, figure: $state.figure + (if (title == "") 0 else 1) }
					caption = if (title == "") "" else "<figcaption>Figure ${$state.figure.to_str()}. ${title}</figcaption>"
					$text = "<figure class=\"diagram\"><img src=\"${Html.escape(DiagramAdapter.href(diagram))}\" alt=\"${Html.escape(if (n.title == "") "Diagram" else n.title)}\">${caption}</figure>"
				} else {
					code = Markdown.parse_str(prepared.text).keep_oks(
						|b| match b {
							Code(c) => Ok(c)
							_ => Err(NotCode)
						},
					).first().map_err(|_| "missing prepared source block")?
					caption = if (title == "") "" else "<div class=\"listing-caption\">${title}</div>"
					$text = "<div class=\"listing\">${caption}<pre><code data-lang=\"${Html.escape(code.info)}\" class=\"language-${Html.escape(code.info)}\">${Html.source(code.info, code.pre)}</code></pre></div>"
				}
			}
			"table" => {
				markdown_table = table(n.source, attrs, n.attrs, $state).map_err(|e| "${source_location(n.source, $state)}: table: ${e}")?
				widths = table_widths(n.source, attrs)?
				total = widths.fold(0.U64, |sum, width| sum + width)
				cols = Str.join_with(
					widths.map(
						|width| {
							percent = width * 10000 // total
							"<col style=\"width:${(percent // 100).to_str()}.${if (percent % 100 < 10) "0" else ""}${(percent % 100).to_str()}%\">"
						},
					),
					"",
				)
				rendered = Html.blocks(Markdown.parse_str(markdown_table), []).map_err(|e| Str.inspect(e))?
				$state = { ..$state, table_count: $state.table_count + (if (title == "") 0 else 1) }
				caption = if (title == "") "" else "<caption>Table ${$state.table_count.to_str()}. ${title}</caption>"
				$text = rendered.replace_each("<table>", "<table>${caption}<colgroup>${cols}</colgroup>")
			}
			"image" => {
				if !safe_path(n.source) {
					return Err("unsafe local image path")
				}
				$state = { ..$state, images: $state.images.append(n.source), figure: $state.figure + (if (title == "") 0 else 1) }
				caption = if (title == "") "" else "<figcaption>Figure ${$state.figure.to_str()}. ${title}</figcaption>"
				$text = "<figure><img src=\"${Html.escape(n.source)}\" alt=\"${Html.escape(lookup(attrs, "1"))}\">${caption}</figure>"
			}
			"ulist" | "olist" | "dlist" => {
				tag = if (n.context == "dlist") "dl" else if (n.context == "olist") "ol" else "ul"
				$text = "${block_title}<${tag}>"
				for item in n.blocks {
					it = node(item)
					if it.context != "list_item" {
						return Err("unexpected list child")
					}
					child = html_blocks(it.blocks, $state, depth + 1)?
					$state = child.state
					body = html_inline(it.source, it.attrs, $state)?
					if n.context == "dlist" {
						$text = "${$text}<dt>${html_inline(it.title, it.attrs, $state)?}</dt><dd><p>${body}</p>${child.text}</dd>"
					} else {
						$text = "${$text}<li><p>${body}</p>${child.text}</li>"
					}
				}
				$text = "${$text}</${tag}>"
			}
			"sidebar" | "open" | "example" | "quote" | "admonition" => {
				child = html_blocks(n.blocks, $state, depth + 1)?
				$state = child.state
				tag = if (n.context == "quote") "blockquote" else if (n.context == "sidebar" or n.context == "admonition") "aside" else "div"
				(style, name) = match n.style {
					"WARNING" => ("warning", "Warning")
					"CAUTION" => ("caution", "Caution")
					"IMPORTANT" => ("important", "Important")
					"TIP" => ("tip", "Tip")
					_ => ("note", "Note")
				}
				label = if (n.context == "admonition" and title == "") "<div class=\"block-title\">${name}</div>" else block_title
				body = if (n.source == "") "" else "<p>${html_inline(n.source, n.attrs, $state)?}</p>"
				$text = "<${tag} class=\"${n.context} ${style}\">${label}${body}${child.text}</${tag}>"
			}
			"thematic_break" => {
				$text = "<hr>"
			}
			"__document_header" | "toc" => {}
			_ => return Err("${source_location(n.source, $state)}: unsupported AsciiDoc HTML block: ${n.context}")
		}
		$out = "${$out}${$text}\n"
	}
	Ok({ text: $out, state: $state })
}

# A page may contain several included chapters. Resolve against the rendered
# page identity, not the current chapter's source path, before routing elsewhere.
html_reference : Ref, State -> Try({ href : Str, title : Str }, Str)
html_reference = |ref, state| {
	local = state.routes.find_first(|r| r.path == ref.path and r.local_id == ref.local_id and r.route == state.page.drop_suffix(".adoc").concat(".html"))
	match local {
		Ok(route) => Ok({ href: "#${route.id}", title: ref.title })
		Err(_) => {
			route = route_for(ref, state)?
			Ok({ href: "${route.route}#${route.id}", title: ref.title })
		}
	}
}
