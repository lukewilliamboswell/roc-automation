import parser.Markdown
import Html
import Renderer

## Static reader-facing Markdown from the same admitted page tree as HTML.
## Rewrite actual link targets, never prose, examples or external URLs.
MarkdownExport :: [].{
	page : Str, List(Str), Str, Str -> Try(Str, Str)
	page = |source, routes, version, canonical| {
		normalized = Renderer.normalize(Markdown.parse_str(source)).map_err(|e| Str.inspect(e))?
		body = blocks(normalized, routes)?
		Ok("> Documentation version: ${text(version)}. [HTML page](${destination(canonical)})\n\n${body}")
	}
	destination : Str -> Str
	destination = |value| value.replace_each(" ", "%20").replace_each("(", "%28").replace_each(")", "%29").replace_each("<", "%3C").replace_each(">", "%3E").replace_each("\\", "%5C").replace_each("\"", "%22")
	route : Str -> Str
	route = |html| html.drop_suffix(".html").concat(".md")
	discover : Str, Str -> Str
	discover = |html, markdown| {
		alternate = "<link rel=\"alternate\" type=\"text/markdown\" href=\"${Html.escape(markdown)}\"><link rel=\"describedby\" type=\"text/plain\" href=\"llms.txt\">"
		visible = "<a href=\"${Html.escape(markdown)}\">View Markdown</a> "
		Str.join_with(Str.join_with(html.split_on("</head>"), "${alternate}</head>").split_on("<div class=\"metadata\">"), "<div class=\"metadata\">${visible}")
	}
	index : Str, Str, List({ title : Str, route : Str }), Str -> Str
	index = |title, version, pages, site_url| {
		links = pages.map(|p| "- [${text(p.title)}](${destination(url(site_url, route(p.route)))})")
		"# ${text(title)}\n\n> Documentation version: ${text(version)}. Static Markdown equivalents of the published HTML pages.\n\n## Documentation\n\n${Str.join_with(links, "\n")}\n"
	}
	url : Str, Str -> Str
	url = |site_url, path| if (site_url == "") path else "${site_url.drop_suffix("/")}/${path}"
	title : Str -> Str
	title = |source| {
		match Markdown.parse_str(source).find_first(
			|block| match block {
				Heading(_) => True
				_ => False
			},
		) {
			Ok(Heading({ content, .. })) => Html.heading(content).label
			_ => "Documentation"
		}
	}
	text : Str -> Str
	text = |value| {
		var $out = []
		for b in value.to_utf8() {
			if [92, 96, 42, 95, 123, 125, 91, 93, 60, 62, 35, 33, 38, 126].contains(b) {
				$out = $out.append(92)
			}
			$out = $out.append(b)
		}
		Str.from_utf8_lossy($out)
	}
}

href : Str, List(Str) -> Str
href = |value, routes| {
	path = (value.split_on("#").first() ?? "").split_on("?").first() ?? ""
	if routes.contains(path) {
		MarkdownExport.route(path).concat(value.drop_prefix(path))
	} else value
}

inlines : List(Markdown.Inline), List(Str) -> Try(Str, Str)
inlines = |input, routes| {
	var $out = ""
	for inline in input {
		part = match inline {
			Text(value) => MarkdownExport.text(value)
			InlineCode(value) => {
				var $fence = "`"
				while value.contains($fence) {
					$fence = "${$fence}`"
				}
				# Padding prevents a leading/trailing backtick from joining the fence.
				if (value.to_utf8().all(|b| b == ' ')) "${$fence}${value}${$fence}" else "${$fence} ${value} ${$fence}"
			}
			Strong(children) => "**${inlines(children, routes)?}**"
			Emphasis(children) => "*${inlines(children, routes)?}*"
			Link({ label, target }) => "[${inlines(label, routes)?}](${MarkdownExport.destination(href(target.href, routes))})"
			Image({ alt, target }) => "![${inlines(alt, routes)?}](${MarkdownExport.destination(target.href)})"
			HardBreak => "  \n"
			_ => return Err("unsupported Markdown export inline: ${Str.inspect(inline)}")
		}
		$out = "${$out}${part}"
	}
	Ok($out)
}

blocks : List(Markdown), List(Str) -> Try(Str, Str)
blocks = |input, routes| {
	var $out = ""
	for block in input {
		part = match block {
			Heading({ level, content }) => {
				heading = Html.heading(content)
				"<a id=\"${Html.escape(heading.id)}\"></a>\n\n${Str.join_with(List.repeat("#", Html.level_number(level).to_u64()), "")} ${inlines(Html.clean_heading(content), routes)?}\n\n"
			}
			Paragraph(content) => "${inlines(content, routes)?}\n\n"
			Code({ info, pre }) => {
				delimiter = if (info.contains("`")) "~" else "`"
				var $fence = Str.join_with(List.repeat(delimiter, 3), "")
				while pre.contains($fence) {
					$fence = "${$fence}${delimiter}"
				}
				"${$fence}${if (info == "") "" else " "}${info}\n${pre}${if (pre.ends_with("\n")) "" else "\n"}${$fence}\n\n"
			}
			Table({ header, rows, align }) => {
				var $table = ""
				# Propagate errors rather than silently discarding unsupported cell content.
				var $cells = []
				for cell in header {
					$cells = $cells.append(inlines(cell, routes)?.replace_each("|", "\\|"))
				}
				$table = "| ${Str.join_with($cells, " | ")} |\n| ${
					Str.join_with(
						align.map(
							|a| match a {
								Left => ":---"
								Right => "---:"
								Center => ":---:"
								_ => "---"
							},
						),
						" | ",
					)
				} |\n"
				for row in rows {
					var $row = []
					for cell in row {
						$row = $row.append(inlines(cell, routes)?.replace_each("|", "\\|"))
					}
					$table = "${$table}| ${Str.join_with($row, " | ")} |\n"
				}
				"${$table}\n"
			}
			ListBlock({ kind, items, .. }) => {
				var $list = ""
				var $number = match kind {
					Ordered({ start }) => start
					_ => 1
				}
				for item in items {
					if item.task != NoTask {
						return Err("unsupported Markdown export task list")
					}
					marker = match kind {
						Ordered(_) => "${$number.to_str()}. "
						_ => "- "
					}
					body = blocks(item.blocks, routes)?.trim()
					padding = Str.join_with(List.repeat(" ", marker.to_utf8().len()), "")
					lines = body.split_on("\n")
					$list = "${$list}${marker}${lines.first() ?? ""}\n${Str.join_with(lines.drop_first(1).map(|line| if (line == "") "" else "${padding}${line}"), "\n")}\n\n"
					$number = $number + 1
				}
				$list
			}
			Blockquote(children) => Str.join_with(blocks(children, routes)?.trim().split_on("\n").map(|line| "> ${line}"), "\n").concat("\n\n")
			ThematicBreak => "---\n\n"
			_ => return Err("unsupported Markdown export block: ${Str.inspect(block)}")
		}
		$out = "${$out}${part}"
	}
	Ok($out)
}
