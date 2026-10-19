import Html
import Destination
import parser.Markdown
import SyntaxHighlight
import CodeHighlight
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
		html = if (mode == PdfOnly) "" else html_document(blocks, metadata, assets)?
		if mode == HtmlOnly {
			return Ok({ html, pdf: [], pages: 0 })
		}
		fonts = register_fonts()?
		options = renderer_options(fonts)
		body = chapter_blocks(blocks, options, assets, metadata)?
		contents = if (body.is_empty()) front_matter(blocks, metadata) else front_matter(blocks, metadata).append(Pdf.page_break).concat(body)
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
		header = Pdf.region({ height: 22, slot_inset: 6, backdrop: Backdrop(rule), start: [Pdf.furniture_text([Pdf.text("${metadata.title}")])], end: if (metadata.version.is_empty()) [] else [Pdf.furniture_text([Pdf.text(metadata.version)])] })
		footer = Pdf.region({ height: 22, slot_inset: 6, backdrop: Backdrop(rule), start: if (metadata.author.is_empty()) [] else [Pdf.furniture_text([Pdf.text(metadata.author)])], end: [Pdf.furniture_text([Pdf.page_number(Decimal), Pdf.text(" / "), Pdf.total_pages(Decimal)])] })
		{ first: Pdf.first_page_template({ footer, gap: 12 }), continuation: Pdf.page_template({ header, footer, gap: 12 }) }
	}

	# Chapter boundaries follow the shallowest heading below an optional book
	# title. Scopes keep navigation, global pagination and structure in one PDF.
	chapter_blocks = |blocks, options, assets, metadata| {
		book = match blocks.first() {
			Ok(Heading({ level: One, content })) => heading(content).label == metadata.title
			_ => False
		}
		body = if (book) blocks.drop_first(1) else blocks
		levels = body.keep_oks(
			|block| match block {
				Heading({ level, .. }) => Ok(level_number(level))
				_ => Err(NotHeading)
			},
		)
		chapter_level = levels.fold(7.U8, |minimum, level| if (level < minimum) level else minimum)
		var $result = []
		var $pending = []
		var $label = ""
		for block in body {
			match block {
				Heading({ level, content }) if level_number(level) == chapter_level => {
					if !$pending.is_empty() {
						$result = $result.concat(scoped_chapter($pending, $label, options, assets, metadata)?)
					}
					$pending = []
					$label = heading(content).label
				}
				_ => {}
			}
			$pending = $pending.append(block)
		}
		$result = $result.concat(scoped_chapter($pending, $label, options, assets, metadata)?)
		# Preserve the book destination even though its title is on the cover.
		if book {
			h = match blocks.first() {
				Ok(Heading({ content, .. })) => heading(content)
				_ => { id: "", label: "" }
			}
			Ok([Pdf.destination_heading(Destination.encode(h.id), 1, h.label)].concat($result))
		} else Ok($result)
	}

	scoped_chapter = |blocks, label, options, assets, metadata| {
		contents = pdf_blocks(blocks, options, False, assets)?
		if contents.is_empty() {
			return Ok([])
		}
		if label == "" {
			return Ok(contents)
		}
		header = Pdf.region({ height: 22, slot_inset: 6, start: [Pdf.furniture_text([Pdf.reserved_width(380, Start, [Pdf.running_label])])] })
		Ok([Pdf.page_scope({ running_label: Label(label), page_templates: Templates({ opening: Pdf.page_template({ footer: chapter_footer(metadata), gap: 12 }), continuation: Pdf.page_template({ header, footer: chapter_footer(metadata), gap: 12 }) }) }, contents)])
	}

	chapter_footer = |metadata| Pdf.region({ height: 22, slot_inset: 6, start: if (metadata.author.is_empty()) [] else [Pdf.furniture_text([Pdf.text(metadata.author)])], end: [Pdf.furniture_text([Pdf.page_number(Decimal), Pdf.text(" / "), Pdf.total_pages(Decimal)])] })

	front_matter = |blocks, metadata| {
		contents = [Pdf.title(metadata.title), Pdf.spacer(18), Pdf.paragraph(if (metadata.author.is_empty()) "Project documentation" else metadata.author), Pdf.paragraph(if (metadata.version.is_empty()) "unreleased" else metadata.version), Pdf.spacer(24), Pdf.heading(1, "Contents"), Pdf.spacer(6)]
		entries = flatten_blocks(blocks).keep_oks(
			|block| match block {
				Heading({ level, content }) => Ok({ level: level_number(level), label: heading(content).label, id: heading(content).id })
				_ => Err(NotHeading)
			},
		)
		# The book title already appears above the contents. Chapters begin the
		# navigation tree; heading-level jumps still represent one parent edge.
		selected = match entries.first() {
			Ok(first) if first.level == 1 and first.label == metadata.title => entries.drop_first(1)
			_ => entries
		}
		tree = contents_items(selected, 0, 0, 0)
		if (tree.items.is_empty()) contents else contents.append(Pdf.bullet_list(tree.items))
	}

	ContentsEntry : { level : U8, label : Str, id : Str }
	contents_items : List(ContentsEntry), U64, U8, U8 -> { items : List(Pdf.ListItem), next : U64 }
	contents_items = |entries, start, parent_level, depth| {
		var $next = start
		var $items = []
		while $next < entries.len() {
			entry = entries.get($next) ?? { level: 0, label: "", id: "" }
			if entry.level <= parent_level {
				break
			}
			children = contents_items(entries, $next + 1, entry.level, depth + 1)
			$next = children.next
			# Limit displayed ancestry, rather than absolute heading numbers, so
			# a book's H2/H3/H4 chapters receive three useful contents levels.
			if depth < 3 {
				label = if (depth == 0) [Pdf.strong([Pdf.text(entry.label)])] else [Pdf.text(entry.label)]
				body = [Pdf.rich_paragraph([Pdf.inline_internal_link(label, Destination.encode(entry.id)), Pdf.text("  "), Pdf.inline_internal_link([Pdf.page_reference({ destination: Destination.encode(entry.id), width: 32 })], Destination.encode(entry.id))])]
				$items = $items.append(Pdf.list_item(if (children.items.is_empty()) body else body.append(Pdf.bullet_list(children.items))))
			}
		}
		{ items: $items, next: $next }
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
			Heading({ level, content }) => Ok({ depth: if (level_number(level) == 1) 0 else level_number(level).to_u64() - 2, destination: Destination.encode(heading(content).id), open: True, title: heading(content).label })
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
					$result = $result.append(Pdf.destination_heading(Destination.encode(h.id), level_number(level), h.label)).append(Pdf.spacer(6))
				}
				Paragraph([Image({ alt, target })]) => {
					asset = image_asset(assets, target.href)?
					image = Image.Source.rgb8({ dimensions: asset.dimensions, pixels: asset.pixels, row_stride: asset.dimensions.width.to_u64() * 3, alpha: NoAlpha })
					drawing = Scene.Drawing.empty.image(image, { origin: { x: 0, y: 0 }, size: asset.size })
					caption = Pdf.no_caption
					$result = $result.append(Pdf.figure({ drawing, alt: plain(alt), caption, fit: ScaleToFit({ minimum_percent: 25 }) }))
					if !in_list {
						$result = $result.append(Pdf.spacer(6))
					}
				}
				Paragraph(inlines) => {
					paragraph = Pdf.rich_paragraph(pdf_inlines(inlines)?)
					followed_by_image = match blocks.get(entry.index + 1) {
						Ok(Paragraph([Image(_)])) => True
						_ => False
					}
					$result = $result.append(if (followed_by_image) Pdf.keep_with_next(Preferred, paragraph) else paragraph)
					if !in_list and !followed_by_image {
						$result = $result.append(Pdf.spacer(6))
					}
				}
				Code({ info, pre }) => {
					$result = $result.append(code_panel(info, pre, options)?)
					if !in_list {
						$result = $result.append(Pdf.spacer(6))
					}
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
								width: if (index == align.len() - 1) Share(1) else Content,
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
					$result = $result.append(table)
					if !in_list {
						$result = $result.append(Pdf.spacer(6))
					}
				}
				ListBlock({ kind, items, .. }) => {
					if items.any(|item| item.task != NoTask) {
						return Err(UnsupportedMarkdown("task list"))
					}
					var $items = []
					for item in items {
						children = pdf_blocks(item.blocks, options, True, assets)?
						# rc6 requires a paragraph before rich item children.
						body = match item.blocks.first() {
							Ok(Paragraph(content)) if !contains_image(content) => children
							_ => [Pdf.paragraph(" ")].concat(children)
						}
						$items = $items.append(Pdf.list_item(body))
					}
					$result = $result.append(
						match kind {
							Unordered => Pdf.bullet_list($items)
							Ordered({ start }) => Pdf.numbered_list({ start: start }, $items)
						},
					)
				}

				Blockquote(children) => {
					$result = $result.append(Pdf.callout({ name: "Note", contents: pdf_blocks(children, options, True, assets)?, padding: 10, panel: { fill: Fill(rgb(247, 249, 252)), border: Border({ color: rgb(173, 192, 215), width: 0.6 }) } }))
					if !in_list {
						$result = $result.append(Pdf.spacer(6))
					}
				}
				ThematicBreak => {
					$result = $result.append(Pdf.spacer(12))
				}
				_ => return Err(UnsupportedMarkdown("block: ${Str.inspect(block)}"))
			}
		}
		Ok($result)
	}

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
		foregrounds = SyntaxHighlight.foregrounds(SyntaxHighlight.light, CodeHighlight.highlight(language, source))
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
					if (target.href.starts_with("#")) Pdf.inline_internal_link(pdf_inlines(label)?, Destination.encode(target.href.drop_prefix("#"))) else Pdf.inline_link(pdf_inlines(label)?, target.href)
				}
				HardBreak => Pdf.line_break
				_ => return Err(UnsupportedMarkdown("inline: ${Str.inspect(inline)}"))
			}
			$result = $result.append(value)
		}
		Ok($result)
	}

	html_document = |blocks, metadata, assets| {
		html_assets = assets.map(|a| { href: a.href, name: a.name })
		body = Html.blocks(blocks, html_assets)?
		Ok(Html.document(body, blocks, metadata, False, 3, ""))
	}
}
