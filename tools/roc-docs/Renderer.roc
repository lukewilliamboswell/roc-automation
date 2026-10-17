import parser.Markdown
import SyntaxHighlight
import pdf.Color
import pdf.Theme
import pdf.Document
import pdf.Font
import pdf.Image
import pdf.Layout
import pdf.Pdf
import pdf.Scene
import "fonts/NotoSans-Regular.ttf" as regular_bytes : List(U8)
import "fonts/NotoSans-Italic.ttf" as italic_bytes : List(U8)
import "fonts/NotoSans-Bold.ttf" as bold_bytes : List(U8)
import "fonts/SourceCodePro-Regular.ttf" as mono_bytes : List(U8)

## Pure reusable Markdown-to-HTML/PDF rendering with embedded licensed fonts.
Renderer :: [].{

	Metadata : { title : Str, author : Str, version : Str, language : Str, pdf_filename : Str }
	Asset : { href : Str, name : Str, dimensions : Image.Dimensions, pixels : List(U8), png : List(U8), size : Layout.Size }
	safe_path = |path| safe_text(path) and !path.is_empty() and !path.starts_with("/") and !path.contains("\\") and path.split_on("/").all(|part| part != "" and part != "." and part != "..")
	safe_text = |text| text.to_utf8().all(|byte| byte >= 32 and byte != 127)
	safe_link = |href| safe_text(href) and (href.starts_with("https://") or href.starts_with("http://") or href.starts_with("mailto:") or href.starts_with("#") or (safe_path(href) and !href.contains(":")))
	image_asset = |assets, href| match assets.find_first(|asset| asset.href == href) {
		Ok(asset) => Ok(asset)
		Err(_) => Err(UnsupportedImage(href))
	}

	## Render both formats before the caller writes any output. Runtime assets are
	## explicit prepared RGB8; no repository content is embedded in this module.
	render = |source, metadata, assets, mode| {
		blocks = normalize(Markdown.parse_str(source))?
		html = if mode == PdfOnly "" else html_document(blocks, metadata, assets)?
		if mode == HtmlOnly {
			return Ok({ html, pdf: [], pages: 0 })
		}
		fonts = register_fonts()?
		options = renderer_options(fonts)
		body = pdf_blocks(blocks, options, False, assets)?
		contents = if body.is_empty() front_matter(blocks, metadata) else front_matter(blocks, metadata).append(Pdf.page_break).concat(body)
		document = Pdf.document({ contents, language: metadata.language, title: metadata.title, outline: outline(blocks), page_templates: Templates(page_templates(metadata)) })
		prepared = Pdf.prepare_with_report(document, options).map_err(|error| PdfRejected(error))?
		bytes = Pdf.to_bytes_prepared(prepared.prepared).map_err(|error| PdfRejected(error))?
		Ok({ html, pdf: bytes, pages: prepared.report.facts.pages.len() })
	}

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

	## IDs are stable and unique; duplicate implicit labels gain numeric suffixes.
	## Explicit duplicates reject rather than silently redirect links.
	normalize = |blocks| {
		normalized = normalize_walk(blocks, [], 0, 0)?
		Ok(normalized.blocks)
	}

	normalize_walk : List(Markdown), List(Str), U64, U64 -> Try({ blocks : List(Markdown), used : List(Str), count : U64 }, _)
	normalize_walk = |blocks, used, depth, count| {
		if depth > 64 or count + blocks.len() > 100000 {
			return Err(DocumentTooComplex)
		}
		var $used = used
		var $count = count + blocks.len()
		var $result = []
		for block in blocks {
			next = match block {
				Heading({ level, content }) => {
					validate_inlines(content, 0)?
					h = heading(content)
					explicit = plain(content).ends_with("}") and plain(content).contains(" {#")
					if h.id.is_empty() or !h.id.to_utf8().all(|b| b >= 128 or (b >= 'a' and b <= 'z') or (b >= 'A' and b <= 'Z') or (b >= '0' and b <= '9') or b == '-' or b == '_' or b == '.' or b == ':') {
						return Err(InvalidHeadingId(h.id))
					}
					if explicit and $used.contains(h.id) {
						return Err(DuplicateHeadingId(h.id))
					}
					var $id = h.id
					var $suffix = 2.U64
					while $used.contains($id) {
						$id = "${h.id}-${$suffix.to_str()}"
						$suffix = $suffix + 1
					}
					$used = $used.append($id)
					Heading({ level, content: clean_heading(content).append(Text(" {#${$id}}")) })
				}
				Paragraph(content) => {
					validate_inlines(content, 0)?
					block
				}
				Blockquote(children) => {
					nested = normalize_walk(children, $used, depth + 1, $count)?
					$used = nested.used
					$count = nested.count
					Blockquote(nested.blocks)
				}
				ListBlock({ kind, loose, items }) => {
					var $items = []
					for item in items {
						nested = normalize_walk(item.blocks, $used, depth + 1, $count)?
						$used = nested.used
						$count = nested.count
						$items = $items.append({ ..item, blocks: nested.blocks })
					}
					ListBlock({ kind, loose, items: $items })
				}
				Table({ header, rows, .. }) => {
					for row in [header].concat(rows) {
						for cell in row {
							validate_inlines(cell, 0)?
						}
					}
					block
				}
				_ => block
			}
			$result = $result.append(next)
		}
		Ok({ blocks: $result, used: $used, count: $count })
	}
	validate_inlines : List(Markdown.Inline), U64 -> Try({}, [DocumentTooComplex])
	validate_inlines = |inlines, depth| {
		if depth > 64 {
			return Err(DocumentTooComplex)
		}
		for inline in inlines {
			match inline {
				Strong(children) | Emphasis(children) | Strikethrough(children) => {
					validate_inlines(children, depth + 1)?
				}
				Link({ label, .. }) => {
					validate_inlines(label, depth + 1)?
				}
				Image({ alt, .. }) => {
					validate_inlines(alt, depth + 1)?
				}
				_ => {}
			}
		}
		Ok({})
	}

	register_fonts = || {
		latin : List(Font.Script)
		latin = ["Latn"]
		add = |registry, bytes| registry.register(bytes, { provision: BuiltIn, scripts: latin }, Font.ValidationLimits.default).map_err(|error| FontRejected(error))
		regular = add(Font.Registry.empty, regular_bytes)?
		bold = add(regular.registry, bold_bytes)?
		italic = add(bold.registry, italic_bytes)?
		mono = add(italic.registry, mono_bytes)?
		Ok({ regular: regular.face, bold: bold.face, italic: italic.face, mono: mono.face, registry: mono.registry })
	}

	rgb = |red, green, blue| Color.srgb8({ red, green, blue })

	renderer_options = |fonts| {
		options : Pdf.Options
		options = {
			fonts: Registered(fonts.registry),
			page_size: A4,
			theme: {
				face: fonts.regular,
				body: { color: rgb(43, 49, 61), size: 11, leading: 15 },
				paragraph_spacing: 0,
				page_margin: { top: 40, right: 62, bottom: 38, left: 62 },
				title: { face: Face(fonts.bold), color: rgb(23, 53, 90), size: 34, leading: 42 },
				headings: {
					all: { face: Face(fonts.bold), color: rgb(23, 53, 90), size: 13, leading: 18 },
					h1: Own({ face: Face(fonts.bold), color: rgb(23, 53, 90), size: 20, leading: 27 }),
					h2: Own({ face: Face(fonts.bold), color: rgb(23, 53, 90), size: 14, leading: 21 }),
				},
				inline: { code: { font: Face(fonts.mono), scale: Percent(82), color: Themed(rgb(39, 62, 94)) }, strong: { font: Face(fonts.bold) }, emphasis: { font: Face(fonts.italic) } },
				link: { color: Themed(rgb(22, 91, 160)), underline: Underline({ offset: 1, thickness: 0.4 }) },
				table: {
					cell_padding: 7,
					row_gap: 8,
					header_fill: Fill(rgb(229, 237, 247)),
					header_color: Themed(rgb(23, 53, 90)),
					body_fills: { odd: Fill(rgb(247, 249, 252)) },
					rule: Rule({ color: rgb(173, 192, 215), width: 0.6 }),
					body_rule: Rule({ color: rgb(225, 231, 238), width: 0.3 }),
				},
			},
		}
		options
	}

	page_templates = |metadata| {
		rule = Scene.Drawing.empty.rectangle({ origin: { x: 0, y: 0 }, size: { width: 471, height: 0.5 } }, rgb(202, 213, 228))
		header = Pdf.region({ height: 22, slot_inset: 6, backdrop: Backdrop(rule), start: [Pdf.furniture_text([Pdf.text("${metadata.title}")])], end: if metadata.version.is_empty() [] else [Pdf.furniture_text([Pdf.text(metadata.version)])] })
		footer = Pdf.region({ height: 22, slot_inset: 6, backdrop: Backdrop(rule), start: if metadata.author.is_empty() [] else [Pdf.furniture_text([Pdf.text(metadata.author)])], end: [Pdf.furniture_text([Pdf.page_number(Decimal), Pdf.text(" / "), Pdf.total_pages(Decimal)])] })
		{ first: Pdf.first_page_template({ footer, gap: 12 }), continuation: Pdf.page_template({ header, footer, gap: 12 }) }
	}

	front_matter = |blocks, metadata| {
		var $contents = [Pdf.title(metadata.title), Pdf.spacer(18), Pdf.paragraph(if metadata.author.is_empty() "Project documentation" else metadata.author), Pdf.paragraph(if metadata.version.is_empty() "unreleased" else metadata.version), Pdf.spacer(24), Pdf.heading(1, "Contents"), Pdf.spacer(6)]
		for block in flatten_blocks(blocks) {
			match block {
				Heading({ level, content }) if level_number(level) <= 3 => {
					h = heading(content)
					$contents = $contents.append(Pdf.rich_paragraph([Pdf.inline_internal_link([Pdf.text(h.label)], h.id)])).append(Pdf.spacer(6))
				}
				_ => {}
			}
		}
		$contents
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

	outline : List(Markdown) -> List(Document.OutlineEntry)
	outline = |blocks| flatten_blocks(blocks).keep_oks(
		|block| match block {
			Heading({ level, content }) => Ok({ depth: if level_number(level) == 1 0 else level_number(level).to_u64() - 2, destination: heading(content).id, open: True, title: heading(content).label })
			_ => Err(NotHeading)
		},
	)

	flatten_blocks : List(Markdown) -> List(Markdown)
	flatten_blocks = |blocks| {
		var $result = []
		for block in blocks {
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

	pdf_blocks : List(Markdown), Pdf.Options, Bool, List(Asset) -> Try(List(Document.Block), _)
	pdf_blocks = |blocks, options, in_list, assets| {
		var $result = []
		for entry in blocks.map_with_index(|block, index| { block, index }) {
			block = entry.block
			match block {
				Heading({ level, content }) => {
					h = heading(content)
					if level_number(level) > 1 {
						$result = $result.append(Pdf.spacer(12))
					}
					$result = $result.append(Pdf.destination_heading(h.id, level_number(level), h.label)).append(Pdf.spacer(6))
				}
				Paragraph([Image({ alt, target })]) => {
					asset = image_asset(assets, target.href)?
					image = Image.Source.rgb8({ dimensions: asset.dimensions, pixels: asset.pixels, row_stride: asset.dimensions.width.to_u64() * 3, alpha: NoAlpha })
					drawing = Scene.Drawing.empty.image(image, { origin: { x: 0, y: 0 }, size: asset.size })
					caption = Pdf.no_caption
					$result = $result.append(Pdf.figure({ drawing, alt: plain(alt), caption })).append(Pdf.spacer(6))
				}
				Paragraph(inlines) => {
					paragraph = Pdf.rich_paragraph(pdf_inlines(inlines)?)
					followed_by_image = match blocks.get(entry.index + 1) {
						Ok(Paragraph([Image(_)])) => True
						_ => False
					}
					$result = $result.append(if followed_by_image Pdf.keep_with_next(Preferred, paragraph) else paragraph)
					if !in_list and !followed_by_image {
						$result = $result.append(Pdf.spacer(6))
					}
				}
				Code({ info, pre }) => {
					$result = $result.append(code_panel(info, pre, options)?)
					$result = $result.append(Pdf.spacer(6))
				}
				Table({ header, rows, align }) => {
					var $headers = []
					for cell in header {
						$headers = $headers.append(Pdf.header_cell(Column, [Pdf.strong(pdf_inlines(cell)?)]))
					}
					var $rows = []
					for row in rows {
						var $cells = []
						for cell in row {
							$cells = $cells.append(Pdf.cell(pdf_inlines(cell)?))
						}
						$rows = $rows.append(Pdf.row($cells))
					}
					table = Pdf.table({
						caption: Pdf.no_caption,
						columns: align.map_with_index(
							|alignment, index| {
								width: if index == align.len() - 1 Share(1) else Content,
								align: match alignment {
									Right => End
									Center => Center
									_ => Start
								},
							},
						),
						header_rows: [Pdf.row($headers)],
						row_split: SplitRows,
						body_rows: $rows,
					})
					$result = $result.append(table).append(Pdf.spacer(6))
				}
				ListBlock({ kind, items, .. }) => {
					if items.any(|item| item.task != NoTask) {
						return Err(UnsupportedMarkdown("task list"))
					}
					if !items.all(|item| list_compatible(item.blocks)) {
						# roc-pdf's list item accepts paragraphs/nested lists only. Keep every
						# complex item's visible marker and flow blocks rather than dropping
						# source, figures or tables. HTML retains its original list structure.
						for item_entry in items.map_with_index(|item, index| { item, index }) {
							marker = match kind {
								Unordered => "• "
								Ordered({ start }) => "${(start + item_entry.index).to_str()}. "
							}
							marked = match item_entry.item.blocks {
								[Paragraph(content), .. as tail] if !contains_image(content) => [Paragraph([Text(marker)].concat(content))].concat(tail)
								_ => [Paragraph([Text(marker)])].concat(item_entry.item.blocks)
							}
							$result = $result.concat(pdf_blocks(marked, options, False, assets)?)
						}
					} else {
						var $items = []
						for item in items {
							if item.task != NoTask {
								return Err(UnsupportedMarkdown("task list"))
							}
							$items = $items.append(Pdf.list_item(pdf_blocks(item.blocks, options, True, assets)?))
						}
						$result = $result.append(
							match kind {
								Unordered => Pdf.bullet_list($items)
								Ordered({ start }) => Pdf.numbered_list(
									{ start: start },
									$items,
								)
							},
						)
					}
				}

				Blockquote(children) => {
					$result = $result.concat(pdf_blocks(children, options, in_list, assets)?)
				}
				ThematicBreak => {
					$result = $result.append(Pdf.spacer(12))
				}
				_ => return Err(UnsupportedMarkdown("block: ${Str.inspect(block)}"))
			}
		}
		Ok($result)
	}

	list_compatible : List(Markdown) -> Bool
	list_compatible = |blocks| blocks.all(
		|block| match block {
			Paragraph(content) => !contains_image(content)
			ListBlock({ items, .. }) => items.all(|item| list_compatible(item.blocks))
			_ => False
		},
	)
	contains_image : List(Markdown.Inline) -> Bool
	contains_image = |inlines| inlines.any(
		|inline| match inline {
			Image(_) => True
			Strong(children) | Emphasis(children) | Strikethrough(children) => contains_image(children)
			Link({ label, .. }) => contains_image(label)
			_ => False
		},
	)

	# Native source layout owns whitespace, wrapping and page continuation.
	code_panel : Str, Str, Pdf.Options -> Try(Document.Block, _)
	code_panel = |language, source, options| {
		face = match options.theme.inline.code.font {
			Face(value) => value
			_ => return Err(UnsupportedMarkdown("source blocks require a registered code face"))
		}
		foregrounds = if language == "roc" SyntaxHighlight.foregrounds(SyntaxHighlight.light, SyntaxHighlight.highlight(source)) else []
		Ok(
			Pdf.source_block({
				text: source,
				face,
				size: 9.02,
				leading: 15,
				padding: 10,
				background: Background(rgb(244, 246, 249)),
				color: Color.srgb8(SyntaxHighlight.light.text),
				foregrounds: foregrounds.map(|span| { start: span.start, end: span.end, color: Color.srgb8(span.color) }),
			}),
		)
	}

	pdf_inlines : List(Markdown.Inline) -> Try(List(Pdf.Inline), _)
	pdf_inlines = |inlines| {
		var $result = []
		for inline in inlines {
			value = match inline {
				Text(text) => Pdf.text(Str.join_with(Str.split_on(text, "\n"), " "))
				InlineCode(text) => Pdf.code(text)
				Strong(children) => Pdf.strong(pdf_inlines(children)?)
				Emphasis(children) => Pdf.emphasis(pdf_inlines(children)?)
				Link({ label, target }) => {
					if !safe_link(target.href) {
						return Err(UnsupportedLink(target.href))
					}
					if target.href.starts_with("#") Pdf.inline_internal_link(pdf_inlines(label)?, target.href.drop_prefix("#")) else Pdf.inline_link(pdf_inlines(label)?, target.href)
				}
				HardBreak => Pdf.line_break
				_ => return Err(UnsupportedMarkdown("inline: ${Str.inspect(inline)}"))
			}
			$result = $result.append(value)
		}
		Ok($result)
	}

	escape : Str -> Str
	escape = |text| {
		amp = Str.join_with(Str.split_on(text, "&"), "&amp;")
		lt = Str.join_with(Str.split_on(amp, "<"), "&lt;")
		gt = Str.join_with(Str.split_on(lt, ">"), "&gt;")
		Str.join_with(Str.split_on(gt, "\""), "&quot;")
	}

	html_inlines : List(Markdown.Inline), List(Asset) -> Try(Str, _)
	html_inlines = |inlines, assets| {
		var $result = ""
		for inline in inlines {
			part = match inline {
				Text(text) => escape(text)
				InlineCode(text) => "<code>${escape(text)}</code>"
				Strong(children) => "<strong>${html_inlines(children, assets)?}</strong>"
				Emphasis(children) => "<em>${html_inlines(children, assets)?}</em>"
				Link({ label, target }) => {
					if !safe_link(target.href) {
						return Err(UnsupportedLink(target.href))
					}
					"<a href=\"${escape(target.href)}\">${html_inlines(label, assets)?}</a>"
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

	html_source = |language, source| {
		if language != "roc" {
			return escape(source)
		}
		Str.join_with(
			SyntaxHighlight.highlight(source).map(
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

	html_blocks : List(Markdown), List(Asset) -> Try(Str, _)
	html_blocks = |blocks, assets| {
		var $result = ""
		for block in blocks {
			part = match block {
				Heading({ level, content }) => {
					n = level_number(level).to_str()
					"<h${n} id=\"${heading(content).id}\">${html_inlines(clean_heading(content), assets)?}</h${n}>"
				}
				Paragraph(content) => "<p>${html_inlines(content, assets)?}</p>"
				Code({ info, pre }) => "<pre><code data-lang=\"${escape(info)}\" class=\"language-${escape(info)}\">${html_source(info, pre)}</code></pre>"
				Table({ header, rows, .. }) => {
					var $head = ""
					for cell in header {
						$head = "${$head}<th>${html_inlines(cell, assets)?}</th>"
					}
					var $body = ""
					for row in rows {
						var $cells = ""
						for cell in row {
							$cells = "${$cells}<td>${html_inlines(cell, assets)?}</td>"
						}
						$body = "${$body}<tr>${$cells}</tr>"
					}
					"<table><thead><tr>${$head}</tr></thead><tbody>${$body}</tbody></table>"
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
						$items = "${$items}<li>${html_blocks(item.blocks, assets)?}</li>"
					}
					"<${tag}${start}>${$items}</${tag}>"
				}
				Blockquote(children) => "<blockquote>${html_blocks(children, assets)?}</blockquote>"
				ThematicBreak => "<hr>"
				_ => return Err(UnsupportedMarkdown("block: ${Str.inspect(block)}"))
			}
			$result = "${$result}${part}\n"
		}
		Ok($result)
	}

	html_document = |blocks, metadata, assets| {
		content = html_blocks(blocks, assets)?
		navigation = Str.join_with(outline(blocks).map(|entry| "<li><a href=\"#${entry.destination}\">${escape(entry.title)}</a></li>"), "")
		Ok("<!doctype html><html lang=\"${escape(metadata.language)}\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>${escape(metadata.title)}</title><style>body{font:17px/1.65 system-ui,sans-serif;color:#203047;max-width:900px;margin:3rem auto;padding:0 1.5rem}a{color:#125bb3}h1,h2,h3{line-height:1.2;scroll-margin-top:2rem}h2{margin-top:3rem;border-bottom:1px solid #d6e0eb;padding-bottom:.5rem}nav{background:#edf3fa;padding:1rem 1.5rem;border-radius:8px}nav ul{columns:2}code{font-family:ui-monospace,monospace;background:#edf3fa;padding:.1rem .25rem}pre{background:#edf3fa;padding:1rem;overflow:auto;border-radius:8px;tab-size:4}pre code{padding:0}${highlight_styles()}table{border-collapse:collapse;width:100%;font-size:.92em}td,th{border:1px solid #d6e0eb;padding:.6rem;text-align:left}th{background:#edf3fa}img{max-width:100%;height:auto}footer{border-top:1px solid #d6e0eb;margin-top:3rem;padding-top:1rem}</style></head><body><p>${escape(metadata.version)} · <a href=\"${escape(metadata.pdf_filename)}\">Download PDF</a></p><nav aria-label=\"Contents\"><ul>${navigation}</ul></nav><main>${content}</main><footer>${escape(metadata.author)}</footer></body></html>")
	}
}
