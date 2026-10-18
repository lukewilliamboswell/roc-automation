import parser.Markdown
import SyntaxHighlight
import CodeHighlight
import "site.css" as stylesheet : Str
import "site.js" as behavior : Str

## Shared HTML layout. Document content is escaped or rendered from validated
## syntax trees; callers never pass authored HTML through this boundary.
Html :: [].{
	Asset : { href : Str, name : Str }
	Entry : { level : U64, id : Str, label : Str, number : Str }
	safe_link = |href| !href.to_utf8().any(|b| b < 32 or b == 127) and (href.starts_with("https://") or href.starts_with("http://") or href.starts_with("mailto:") or href.starts_with("#") or (!href.is_empty() and !href.starts_with("/") and !href.contains(":") and !href.contains("\\") and href.split_on("/").all(|part| part != ".." and part != ".")))
	image_asset = |assets, href| assets.find_first(|a| a.href == href).map_err(|_| UnsupportedImage(href))
	heading = |content| {
		text = plain(content)
		parts = text.split_on(" {#")
		match parts {
			[label, suffix] if suffix.ends_with("}") => { label, id: suffix.drop_suffix("}") }
			_ => { label: text, id: slug(text) }
		}
	}

	clean_heading = |content| {
		h = heading(content)
		match content.last() {
			Ok(Text(text)) => content.drop_last(1).append(Text(text.drop_suffix(" {#${h.id}}")))
			_ => content
		}
	}

	plain : List(Markdown.Inline) -> Str
	plain = |inlines| Str.join_with(
		inlines.map(
			|inline| match inline {
				Text(text) | InlineCode(text) => text
				Strong(children) | Emphasis(children) | Strikethrough(children) => plain(children)
				Link({ label, .. }) => plain(label)
				Image({ alt, .. }) => plain(alt)
				HardBreak => " "
				HtmlInline(_) => ""
			},
		),
		"",
	)

	slug : Str -> Str
	slug = |text| {
		var $bytes = []
		for byte in Str.to_utf8(text) {
			if byte >= 'A' and byte <= 'Z' {
				$bytes = $bytes.append(byte + 32)
			} else if (byte >= 'a' and byte <= 'z') or (byte >= '0' and byte <= '9') or byte >= 128 {
				$bytes = $bytes.append(byte)
			} else if byte == ' ' or byte == '-' {
				$bytes = $bytes.append('-')
			}
		}
		Str.from_utf8_lossy($bytes)
	}

	flatten_blocks : List(Markdown) -> List(Markdown)
	flatten_blocks = |input| {
		var $result = []
		for block in input {
			$result = $result.append(block)
			match block {
				Blockquote(children) => {
					$result = $result.concat(flatten_blocks(children))
				}
				ListBlock({ items, .. }) => {
					for item in items {
						$result = $result.concat(flatten_blocks(item.blocks))
					}
				}
				_ => {}
			}
		}
		$result
	}

	level_number : Markdown.Level -> U8
	level_number = |level| match level {
		One => 1
		Two => 2
		Three => 3
		Four => 4
		Five => 5
		Six => 6
	}

	escape : Str -> Str
	escape = |text| {
		amp = Str.join_with(Str.split_on(text, "&"), "&amp;")
		lt = Str.join_with(Str.split_on(amp, "<"), "&lt;")
		gt = Str.join_with(Str.split_on(lt, ">"), "&gt;")
		Str.join_with(Str.split_on(gt, "\""), "&quot;")
	}

	inlines : List(Markdown.Inline), List(Asset) -> Try(Str, _)
	inlines = |input, assets| {
		var $result = ""
		for inline in input {
			part = match inline {
				Text(text) => escape(text)
				InlineCode(text) => "<code>${escape(text)}</code>"
				Strong(children) => "<strong>${inlines(children, assets)?}</strong>"
				Emphasis(children) => "<em>${inlines(children, assets)?}</em>"
				Link({ label, target }) => {
					if !safe_link(target.href) {
						return Err(UnsupportedLink(target.href))
					}
					"<a href=\"${escape(target.href)}\">${inlines(label, assets)?}</a>"
				}
				Image({ alt, target }) => {
					asset = image_asset(assets, target.href)?
					"<img src=\"${escape(asset.name)}\" alt=\"${escape(plain(alt))}\">"
				}
				HardBreak => "<br>"
				_ => return Err(UnsupportedMarkdown("inline: ${Str.inspect(inline)}"))
			}
			$result = "${$result}${part}"
		}
		Ok($result)
	}

	source = |language, source| {
		Str.join_with(
			CodeHighlight.highlight(language, source).map(
				|span| {
					text = escape(SyntaxHighlight.text(source, span))
					if span.category == Trivia text else "<span class=\"syntax-${SyntaxHighlight.name(span.category)}\">${text}</span>"
				},
			),
			"",
		)
	}

	highlight_styles = || Str.join_with(
		[CommentText, Name, TypeName, ReservedWord, NumericLiteral, StringText, Symbol, InvalidText].map(
			|category| {
				color = SyntaxHighlight.color(SyntaxHighlight.light, category)
				".syntax-${SyntaxHighlight.name(category)}{color:rgb(${color.red.to_str()},${color.green.to_str()},${color.blue.to_str()})}"
			},
		),
		"",
	)

	blocks : List(Markdown), List(Asset) -> Try(Str, _)
	blocks = |input, assets| {
		var $result = ""
		for block in input {
			part = match block {
				Heading({ level, content }) => {
					heading_html(level_number(level).to_u64(), heading(content).id, inlines(clean_heading(content), assets)?)
				}
				Paragraph(content) => "<p>${inlines(content, assets)?}</p>"
				Code({ info, pre }) => "<pre><code data-lang=\"${escape(info)}\" class=\"language-${escape(info)}\">${source(info, pre)}</code></pre>"
				Table({ header, rows, .. }) => {
					var $head = ""
					for cell in header {
						$head = "${$head}<th scope=\"col\">${inlines(cell, assets)?}</th>"
					}
					var $body = ""
					for row in rows {
						var $cells = ""
						for cell in row {
							$cells = "${$cells}<td>${inlines(cell, assets)?}</td>"
						}
						$body = "${$body}<tr>${$cells}</tr>"
					}
					"<div class=\"table-scroll\"><table><thead><tr>${$head}</tr></thead><tbody>${$body}</tbody></table></div>"
				}
				ListBlock({ kind, items, .. }) => {
					tag = match kind {
						Unordered => "ul"
						Ordered(_) => "ol"
					}
					start = match kind {
						Ordered({ start: value }) => " start=\"${value.to_str()}\""
						_ => ""
					}
					var $items = ""
					for item in items {
						if item.task != NoTask {
							return Err(UnsupportedMarkdown("task list"))
						}
						$items = "${$items}<li>${blocks(item.blocks, assets)?}</li>"
					}
					"<${tag}${start}>${$items}</${tag}>"
				}
				Blockquote(children) => "<blockquote>${blocks(children, assets)?}</blockquote>"
				ThematicBreak => "<hr>"
				_ => return Err(UnsupportedMarkdown("block: ${Str.inspect(block)}"))
			}
			$result = "${$result}${part}\n"
		}
		Ok($result)
	}

	heading_html = |level, id, label| "<h${level.to_str()} id=\"${escape(id)}\">${label}<a class=\"anchor\" href=\"#${escape(id)}\" aria-label=\"Link to ${escape(id)}\">§</a></h${level.to_str()}>"

	entries = |input, numbered| {
		headings = flatten_blocks(input).keep_oks(
			|block| match block {
				Heading({ level, content }) => Ok({ level: level_number(level).to_u64(), content })
				_ => Err(NotHeading)
			},
		)
		omit_title = headings.keep_if(|h| h.level == 1).len() == 1 and (headings.first().map_ok(|h| h.level == 1) ?? False)
		selected = if omit_title headings.drop_first(1) else headings
		base = selected.fold(6.U64, |lowest, h| if h.level < lowest h.level else lowest)
		var $counts = List.repeat(0.U64, 6)
		var $entries = []
		for h in selected {
			$counts = $counts.map_with_index(|value, index| if index + 1 > h.level 0 else if index + 1 == h.level value + 1 else value)
			number = if numbered Str.join_with($counts.drop_first(base - 1).take_first(h.level - base + 1).map(U64.to_str), ".").concat(". ") else ""
			info = heading(h.content)
			$entries = $entries.append({ level: h.level, id: info.id, label: info.label, number })
		}
		$entries
	}

	# Tree construction follows actual heading ancestry, including skipped levels.
	toc : List(Entry) -> Str
	toc = |items| {
		var $rest = items
		var $result = ""
		while !$rest.is_empty() {
			first = $rest.first() ?? { level: 1, id: "", label: "", number: "" }
			var $length = 1.U64
			while $length < $rest.len() and (($rest.get($length).map_ok(|e| e.level) ?? 0) > first.level) {
				$length = $length + 1
			}
			children = $rest.drop_first(1).take_first($length - 1)
			$rest = $rest.drop_first($length)
			nested = if children.is_empty() "" else "<ul>${toc(children)}</ul>"
			$result = "${$result}<li><a href=\"#${escape(first.id)}\">${escape(first.number)}${escape(first.label)}</a>${nested}</li>"
		}
		$result
	}

	document = |body, input, metadata, numbered, toc_depth, remark| {
		items = entries(input, numbered)
		base = items.fold(6.U64, |lowest, e| if e.level < lowest e.level else lowest)
		selected = items.keep_if(|e| e.level < base + toc_depth)
		var $body = body
		for entry in items {
			if entry.number != "" {
				prefix = "<h${entry.level.to_str()} id=\"${escape(entry.id)}\">"
				$body = $body.replace_each(prefix, "${prefix}<span class=\"section-number\">${escape(entry.number)}</span>")
			}
		}
		main_id = chrome_id("content", body)
		toc_id = chrome_id("toc", body)
		var $page_title = metadata.title
		var $title = ""
		match input.first() {
			Ok(Heading({ level: One, content })) => {
				h = heading(content)
				$page_title = if h.label == metadata.title h.label else "${h.label} — ${metadata.title}"
				candidate = heading_html(1.U64, h.id, inlines(clean_heading(content), []) ?? escape(h.label))
				if $body.starts_with(candidate) {
					$title = candidate
					$body = $body.drop_prefix(candidate)
				}
			}
			_ => {}
		}
		header_id = chrome_id("header", body)
		footer_id = chrome_id("footer", body)
		footer_text_id = chrome_id("footer-text", body)
		author_id = chrome_id("author", body)
		version_id = chrome_id("revnumber", body)
		remark_id = chrome_id("revremark", body)
		toc_title_id = chrome_id("toctitle", body)
		preamble_id = chrome_id("preamble", body)
		if $title != "" {
			match items.first() {
				Ok(first) => {
					marker = "<h${first.level.to_str()} id=\"${escape(first.id)}\">"
					parts = $body.split_on(marker)
					if parts.len() > 1 {
						$body = "<div id=\"${preamble_id}\">${parts.first() ?? ""}</div>${marker}${Str.join_with(parts.drop_first(1), marker)}"
					}
				}
				Err(_) => {
					$body = "<div id=\"${preamble_id}\">${$body}</div>"
				}
			}
		}
		version = if metadata.version.is_empty() "" else "<span id=\"${version_id}\">${escape(metadata.version)}</span>"
		author = if metadata.author.is_empty() "" else "<span id=\"${author_id}\">${escape(metadata.author)}</span>"
		description = if remark == "" "" else "<span id=\"${remark_id}\">${escape(remark)}</span>"
		navigation = if selected.is_empty() "" else "<nav id=\"${toc_id}\" class=\"toc\" aria-label=\"Contents\"><details open><summary id=\"${toc_title_id}\">Contents</summary><ul>${toc(selected)}</ul></details></nav>"
		"<!doctype html><html lang=\"${escape(metadata.language)}\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>${escape($page_title)}</title><style>${stylesheet}${highlight_styles()}</style></head><body class=\"${if selected.is_empty() "no-toc" else "with-toc"}\"><a class=\"skip-link\" href=\"#${main_id}\">Skip to content</a><header id=\"${header_id}\">${$title}<div class=\"metadata\">${author}${version}${description}<a href=\"${escape(metadata.pdf_filename)}\">Download PDF</a></div></header>${navigation}<main id=\"${main_id}\" tabindex=\"-1\">${$body}</main><footer id=\"${footer_id}\"><span id=\"${footer_text_id}\">${escape(metadata.author)}</span></footer><script>${behavior}</script></body></html>"
	}

	## Prefer a separately validated vector image; retain the original PNG as
	## fallback and as the PDF's source. No SVG markup enters the page DOM.
	vector_images = |html, images| {
		var $html = html
		for image in images {
			if image.svg_href != "" {
				prefix = "<img src=\"${escape(image.href)}\""
				parts = $html.split_on(prefix)
				var $next = parts.first() ?? ""
				for tail in parts.drop_first(1) {
					pieces = tail.split_on(">")
					$next = "${$next}<picture><source type=\"image/svg+xml\" srcset=\"${escape(image.svg_href.replace_each("%", "%25").replace_each(" ", "%20").replace_each(",", "%2C").replace_each("#", "%23").replace_each("?", "%3F"))}\">${prefix}${pieces.first() ?? ""}></picture>${Str.join_with(pieces.drop_first(1), ">")}"
				}
				$html = $next
			}
		}
		$html
	}

	chrome_id = |base, body| {
		var $id = base
		while body.contains("id=\"${$id}\"") {
			$id = "${$id}-navigation"
		}
		$id
	}

}
