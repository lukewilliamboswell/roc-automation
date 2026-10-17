import parser.AsciiDoc
import parser.Xml
import parser.Markdown
import Sha256

## Pure preparation using roc-parser's actual AsciiDoc AST and inline renderer.
## Files and prepared diagrams are supplied as inert data by the caller.
Preparation :: [].{
	File : { path : Str, source : Str }
	Options : { entrypoint : Str, version : Str, pdf_filename : Str, diagrams : List({ source_sha256 : Str, href : Str }) }
	Result : { pages : List({ route : Str, source : Str }), manual : Str, metadata : { title : Str, author : Str, version : Str, language : Str }, images : List(Str) }
	prepare : List(File), Options -> Try(Result, Str)
	prepare = |files, options| {
		if files.len() > 1024 or files.any(|f| !safe_path(f.path) or (if f.path.ends_with(".adoc") f.source.to_utf8().len() > 262144 else f.source.to_utf8().len() > 16777216)) or files.fold(0.U64, |n, f| n + f.source.to_utf8().len()) > 33554432 {
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
			return markdown(files, entry, options)
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
		manual_state = initial(options, raw, refs, $routes, entry.path, "pdf")
		rendered_manual = blocks(manual_normalized.blocks, manual_state, 0)?
		var $images = rendered_manual.state.images
		for doc in $documents {
			state = initial(options, raw, doc.identified.refs.concat(refs), $routes, doc.file.path, "html5")
			result = blocks(doc.identified.blocks, state, 0)?
			$pages = $pages.append({ route: doc.route, source: result.text })
			$images = $images.concat(result.state.images)
		}
		if $pages.fold(0.U64, |n, p| n + p.source.to_utf8().len()) + rendered_manual.text.to_utf8().len() > 67108864 {
			return Err("prepared documentation exceeds output limit")
		}
		attrs = attributes(files, entry, options)?
		title = manual_doc.title
		Ok({ pages: $pages, manual: rendered_manual.text, metadata: { title, author: lookup(attrs, "author"), version: options.version, language: if lookup(attrs, "lang") == "" "en" else lookup(attrs, "lang") }, images: unique($images) })
	}
}

safe_path = |s| !s.is_empty() and !s.starts_with("/") and !s.contains("\\") and s.split_on("/").all(|p| p != "" and p != "." and p != "..") and s.to_utf8().all(|b| b >= 32 and b != 127)

unique = |values| values.fold([], |out, item| if out.contains(item) out else out.append(item))

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
				$out = $out.append(if b >= 'A' and b <= 'Z' b + 32 else b)
				$dash = False
			}
	}
	value = Str.from_utf8_lossy($out).drop_suffix("-")
	if value == "" "section" else value
}

base_attributes = |options, backend| [{ name: "docs-version", value: options.version }, { name: "backend", value: backend }, { name: "backend-${backend}", value: "" }, { name: "idprefix", value: "" }, { name: "idseparator", value: "-" }]

parse = |files, file, options, backend| {
	source = if backend == "pdf" ":backend-html5!:\n:backend-html5-doctype-article!:\n:basebackend-html!:\n${file.source}" else file.source
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
			base = if n.id == "" slug(n.title) else n.id
			local_count = $refs.keep_if(|r| r.path == $path and r.local_id == base).len()
			if n.id != "" and local_count > 0 {
				return Err("duplicate explicit heading ID ${base} in ${$path}")
			}
			local_id = if local_count == 0 base else "${base}-${(local_count + 1).to_str()}"
			var $id = local_id
			var $suffix = 2.U64
			while $used.contains($id) {
				$id = if $suffix == 2 "${slug($path.drop_suffix(".adoc"))}-${local_id}" else "${slug($path.drop_suffix(".adoc"))}-${local_id}-${($suffix - 1).to_str()}"
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

State : { options : Preparation.Options, raw : List(RawCode), refs : List(Ref), routes : List(Route), path : Str, backend : Str, images : List(Str) }

initial = |options, raw, refs, routes, path, backend| { options, raw, refs, routes, path, backend, images: [] }

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
					$state = { ..$state, path: ref.path }
				}
				_ => {}
			}
		}
		attrs = AsciiDoc.materialize_attributes(n.attrs)
		if lookup(attrs, "subs") != "" or lookup(attrs, "indent") != "" {
			return Err("custom substitution/indent modes are unsupported")
		}
		title = if n.context == "section" or n.context == "floating_title" "" else if n.title == "" "" else "**${inline(n.title, n.attrs, $state)?}**\n\n"
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
					digest = Sha256.hex(body.to_utf8())
					diagram = $state.options.diagrams.find_first(|d| d.source_sha256 == digest).map_err(|_| "unprepared/stale Mermaid diagram ${digest}")?
					$state = { ..$state, images: $state.images.append(diagram.href) }
					$text = "${$text}![Diagram](${diagram.href})\n\n"
				} else {
					candidates = $state.raw.keep_if(|r| r.normalized == normalize_code(n.source))
					values = unique(candidates.map(|r| r.source))
					if values.len() > 1 {
						return Err("ambiguous original source bytes after upstream whitespace normalization")
					}
					if values.is_empty() and n.context == "listing" {
						return Err("listing requires a source-preserving delimited original block")
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
				$text = "${$text}${table(n.source, attrs, n.attrs, $state)?}\n\n"
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
					marker = if n.context == "olist" "${$index.to_str()}. " else "- "
					label = if n.context == "dlist" "**${inline(it.title, it.attrs, $state)?}**: " else ""
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
				$text = "${$text}${if n.source == "" "" else "${inline(n.source, n.attrs, $state)?}\n\n"}${result.text}"
			}
			"thematic_break" => {
				$text = "${$text}---\n\n"
			}
			"__document_header" | "toc" => {}
			_ => return Err("unsupported AsciiDoc block context: ${n.context}")
		}
		$out = "${$out}${$text}"
	}
	Ok({ text: $out, state: $state })
}

indent = |s| Str.join_with(s.split_on("\n").map(|line| if line == "" "" else "    ${line}"), "\n")

table = |source, attrs, scope, state| {
	if lookup(attrs, "format") != "" or lookup(attrs, "separator") != "" {
		return Err("only PSV tables are supported by renderer adapter")
	}
	if !lookup(attrs, "cols").to_utf8().all(|b| (b >= '0' and b <= '9') or b == ',') or !lookup(attrs, "options").split_on(",").all(|o| o == "header" or o == "") or !lookup(attrs, "options").split_on(",").contains("header") or source.contains("\\|") {
		return Err("table styles/spans/escaped separators/non-header tables are unsupported")
	}
	columns = lookup(attrs, "cols").split_on(",").len()
	var $cells = []
	for line in source.split_on("\n") {
		if line.starts_with("|") {
			$cells = $cells.concat(line.drop_prefix("|").split_on("|").map(|v| v.trim()))
		} else if line.trim() != "" {
			if $cells.is_empty() {
				return Err("table continuation before cell")
			}
			last = $cells.last() ?? ""
			$cells = $cells.drop_last(1).append("${last} ${line.trim()}")
		}
	}
	if columns == 0 or $cells.is_empty() or $cells.len() % columns != 0 {
		return Err("ragged or empty PSV table")
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
	protected = protect_code(source, attrs)?
	html = AsciiDoc.render_inline_attributes(protected.text, attrs)
	xml = Xml.parse_str("<root>${html.replace_each("<br>", "<br/>").replace_each("&nbsp;", "&#160;")}</root>").map_err(|e| "unsupported AsciiDoc inline representation: ${Str.inspect(e)}")?
	nodes = match xml.root {
		Xml.Node.Element(e) => e.children
		_ => []
	}
	var $result = xml_markdown(nodes, state, False)?
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
		if entry.index % 2 == 0 {
			$text = "${$text}${entry.text}"
		}
			else {
				var $value = if entry.text.starts_with("+") and entry.text.ends_with("+") entry.text.drop_prefix("+").drop_suffix("+") else entry.text
				for attr in AsciiDoc.materialize_attributes(attrs) {
					if !attr.name.starts_with("\u(0)") and !$value.is_empty() {
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
			Xml.Node.Text(s) => if code s else escape_text(s)
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
						label = if child.starts_with("\\[") and child.ends_with("\\]") resolved.title else child
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

resolve = |href, state| {
	if href.starts_with("https://") or href.starts_with("http://") or href.starts_with("mailto:") {
		return Ok({ href, title: href })
	}
	if href.ends_with(".pdf") {
		return Ok({ href: state.options.pdf_filename, title: href })
	}
	parts = href.split_on("#")
	path = (parts.first() ?? "").replace_each(".html", ".adoc")
	anchor = if parts.len() > 1 parts.get(1) ?? "" else ""
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
		route = route_for(ref, state)?
		return Ok({ href: if route.route == state.path.replace_each(".adoc", ".html") "#${route.id}" else "${route.route}#${route.id}", title: ref.title })
	}
	if path != "" and path.ends_with(".adoc") {
		ref = state.refs.find_first(|r| r.path == path and (anchor == "" or r.local_id == anchor)).map_err(|_| "unknown AsciiDoc cross-page reference ${href}")?
		if state.backend == "pdf" {
			return Ok({ href: "#${ref.id}", title: ref.title })
		}
		route = route_for(ref, state)?
		return Ok({ href: "${route.route}#${route.id}", title: ref.title })
	}
	if !safe_path(href) {
		return Err("unsafe AsciiDoc link ${href}")
	}
	Ok({ href, title: href })
}

markdown = |files, entry, options| {
	var $images = []
	var $pages = []
	for file in files.keep_if(|f| f.path.ends_with(".md") and !f.path.contains("/")) {
		$images = $images.concat(markdown_images(Markdown.parse_str(file.source)))
		$pages = $pages.append({ route: file.path.drop_suffix(".md").concat(".html"), source: file.source })
	}
	title = entry.source.split_on("\n").find_first(|line| line.starts_with("# ")).map_ok(|line| line.drop_prefix("# ").split_on(" {#").first() ?? "Manual") ?? "Manual"
	Ok({ pages: $pages, manual: entry.source, metadata: { title, author: "", version: options.version, language: "en" }, images: $images })
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
