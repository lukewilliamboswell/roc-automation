app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.26.0-rc2/Gj3zVDr3qrQa6aTh7Z1zgUfe9NeBE6R76T5ho3xrc8af.tar.zst",
	deflate: "https://github.com/lukewilliamboswell/roc-deflate/releases/download/0.4.0-rc2/BE186Swhzh5dYZjaECgLSQFb17wiLtq4UkxTmiFAPWze.tar.zst",
	font: "https://github.com/lukewilliamboswell/roc-font/releases/download/0.1.0-rc2/A14BmicBaf6dm4K9kdYH9j8Y6BoAZrpUmxWh9sojsFy5.tar.zst",
	mermaid: "https://github.com/lukewilliamboswell/roc-graph-layout/releases/download/0.3.0-rc3/mermaid-A9th2FTnD4nQrV1nkr9mMZFp6JkK4aNzt7Gi6HAGHWrr.tar.zst",
	parser: "https://github.com/lukewilliamboswell/roc-parser/releases/download/3.0.0/6xsRdRqenJrAb4wG7HaST4Xh349vD5sHAJDsKTK6E3dr.tar.zst",
	pdf: "https://github.com/lukewilliamboswell/roc-pdf/releases/download/0.2.0/2hTpvYoKFHy8C6VeEgiv4FgWkP4oNTVupc1XCN8UwdiZ.tar.zst",
}

import cli.OsStr
import cli.Path
import cli.Stdout
import cli.Stderr
import Renderer
import WebAssets
import BuildFiles
import Png
import RepositoryBuild
import ImageAssets
import DiagramAdapter
import DiagramSvg
import MarkdownExport
import parser.Markdown
import pdf.Layout
import "vendor/roc-deflate-LICENSE" as deflate_license : Str
import "vendor/NOTICE.adoc" as deflate_notice : Str
import "fonts/Inter-OFL.txt" as inter_license : Str
import "fonts/Merriweather-OFL.txt" as serif_license : Str
import "fonts/SourceCodePro-OFL.md" as mono_license : Str

main! = |arguments| {
	args = arguments.map(OsStr.display)
	match args.first() {
		Ok("build") | Ok("prepare") => return RepositoryBuild.run!(args).map_err(|message| BuildRejected(message))
		_ => {}
	}
	if args == ["--licenses"] {
		Stdout.line!("Renderer adapted from roc-ansi. Copyright © 2023 Luke Boswell and subsequent authors. Licensed under the Universal Permissive License (UPL), Version 1.0.")?
		Stdout.line!(inter_license)?
		Stdout.line!(serif_license)?
		Stdout.line!(mono_license)?
		for file in WebAssets.list.keep_if(|f| f.path.ends_with(".txt")) {
			Stdout.line!(Str.from_utf8_lossy(file.bytes))?
		}
		Stdout.line!(deflate_notice)?
		return Stdout.line!(deflate_license)
	}
	if args == ["--help"] {
		Stdout.line!("roc-docs build|prepare --workspace ROOT --docs-directory docs --entrypoint index.adoc --output-directory OUTPUT [metadata/options]")?
		Stdout.line!("roc-docs render INPUT.md NEW_OUTPUT_DIRECTORY [--title TEXT] [--author TEXT] [--version TEXT] [--language TAG] [--pdf-filename NAME.pdf] [--html-only|--pdf-only] [--pdf-image-dpi 72..300] [--image HREF PNG_PATH]... [--asset HREF PNG_PATH RGB_PATH WIDTH HEIGHT]...")?
		return Ok({})
	}
	options = match parse(args) {
		Ok(value) => value
		Err(message) => {
			Stderr.line!(message)?
			return Err(Exit(2))
		}
	}
	output = Path.utf8(options.output)
	stage = Path.utf8("${options.output}.roc-docs-stage")
	if Path.exists!(output)? or Path.exists!(stage)? {
		return Err(OutputExists(options.output))
	}
	if Path.size_in_bytes!(Path.utf8(options.input))? > 16777216 {
		return Err(InputTooLarge)
	}
	source = Path.read_utf8!(Path.utf8(options.input))?
	var $assets = []
	var $total = 0
	for spec in options.assets {
		if WebAssets.list.any(|file| file.path == spec.href) {
			return Err(InvalidAssetPath(spec.href))
		}
		if Path.size_in_bytes!(Path.utf8(spec.png))? > 33554432 {
			return Err(ImageTooLarge(spec.href))
		}
		png = Path.read_bytes!(Path.utf8(spec.png))?
		original = if spec.native {
			Png.decode(png).map_err(|message| InvalidPngSource(spec.href, message))?
		} else {
			if Path.size_in_bytes!(Path.utf8(spec.rgb))? > 100663296 {
				return Err(ImageTooLarge(spec.href))
			}
			pixels = Path.read_bytes!(Path.utf8(spec.rgb))?
			if pixels.len() != spec.width * spec.height * 3 {
				return Err(InvalidRgbLength(spec.href))
			}
			if png.take_first(8) != [137, 80, 78, 71, 13, 10, 26, 10] or png.len() < 24 or png.sublist({ start: 12, len: 4 }) != [73, 72, 68, 82] or png_dimension(png, 16) != spec.width or png_dimension(png, 20) != spec.height {
				return Err(InvalidPng(spec.href))
			}
			{ width: spec.width, height: spec.height, pixels }
		}
		pixels = original.pixels
		$total = $total + png.len() + pixels.len()
		if $total > 134217728 {
			return Err(AssetsTooLarge)
		}
		width = original.width * 750
		height = original.height * 750
		width_fit = if (width > 471000) 471000 else width
		height_fit = height * width_fit // width
		rendered_height = if (height_fit > 620000) 620000 else height_fit
		rendered_width = width_fit * rendered_height // height_fit
		raster = ImageAssets.fit(pixels, original.width, original.height, if (rendered_width * options.pdf_image_dpi < 72000) 1 else rendered_width * options.pdf_image_dpi // 72000, if (rendered_height * options.pdf_image_dpi < 72000) 1 else rendered_height * options.pdf_image_dpi // 72000)?
		$assets = $assets.append({ href: spec.href, name: spec.href, data: Raster({ dimensions: { width: raster.width.to_u32_wrap(), height: raster.height.to_u32_wrap() }, png, pixels: raster.pixels, size: { width: Layout.Unit.millipoints(rendered_width.to_i64_wrap()), height: Layout.Unit.millipoints(rendered_height.to_i64_wrap()) } }) })
	}
	adapted = DiagramAdapter.markdown(Markdown.parse_str(source), options.input, 0, [], 0).map_err(|e| DiagramRejected(e))?
	for diagram in adapted.cache {
		if $assets.any(|a| a.href == diagram.href) {
			return Err(InvalidAssetPath(diagram.href))
		}
		svg = DiagramSvg.document(diagram.picture)
		$total = $total + svg.len()
		if $total > 134217728 {
			return Err(AssetsTooLarge)
		}
		$assets = $assets.append({ href: diagram.href, name: diagram.href, data: Vector({ picture: diagram.picture, svg }) })
	}
	prepared_source = if (adapted.ordinal == 0) source else MarkdownExport.serialize(adapted.blocks).map_err(|e| DiagramRejected(e))?
	rendered = Renderer.render(prepared_source, options.metadata, $assets, options.mode)?
	Path.create_all!(stage)?
	result = write!(stage, options, rendered, $assets)
	match result {
		Err(error) => {
			Path.delete_all!(stage)?
			return Err(error)
		}
		Ok(_) => {}
	}
	if Path.exists!(output)? {
		Path.delete_all!(stage)?
		return Err(OutputExists(options.output))
	}
	Path.rename!(stage, output)?
	Stdout.line!("Rendered ${options.input}: pages=${rendered.pages.to_str()} pdf_bytes=${rendered.pdf.len().to_str()}")
}

write! = |stage, options, rendered, assets| {
	if options.mode != PdfOnly {
		for file in WebAssets.list {
			BuildFiles.write_bytes!(stage, file.path, file.bytes).map_err(|message| WebAssetRejected(message))?
		}
	}
	if options.mode != PdfOnly {
		Path.write_utf8!(Path.join(stage, "index.html"), rendered.html)?
	}
	if options.mode != HtmlOnly {
		Path.write_bytes!(Path.join(stage, options.metadata.pdf_filename), rendered.pdf)?
	}
	for asset in assets {
		destination = Path.join(stage, asset.name)
		Path.create_all!(Path.join(stage, Str.join_with(asset.name.split_on("/").drop_last(1), "/")))?
		bytes = match asset.data {
			Raster(r) => r.png
			Vector(v) => v.svg
		}
		Path.write_bytes!(destination, bytes)?
	}
	Ok({})
}

parse = |args| {
	(input, output, remaining) = match args {
		["render", source, destination, .. as rest] => (source, destination, rest)
		_ => return Err("usage: roc-docs render INPUT.md NEW_OUTPUT_DIRECTORY [options]; see --help")
	}
	var $metadata = { title: "Manual", author: "", version: "unreleased", language: "en", pdf_filename: "manual.pdf" }
	var $mode = Both
	var $dpi = 144.U64
	var $assets = []
	var $rest = remaining
	var $seen = []
	while !$rest.is_empty() {
		match $rest {
			["--image", href, png, .. as tail] => {
				if !Renderer.safe_path(href) or href == "index.html" or href.ends_with(".pdf") or $assets.any(|asset| asset.href == href) {
					return Err("invalid or duplicate asset path: ${href}")
				}
				$assets = $assets.append({ href, png, rgb: "", width: 0, height: 0, native: True })
				if $assets.len() > 256 {
					return Err("too many assets")
				}
				$rest = tail
			}
			["--asset", href, png, rgb, width_text, height_text, .. as tail] => {
				if !Renderer.safe_path(href) or href == "index.html" or href.ends_with(".pdf") or $assets.any(|asset| asset.href == href) {
					return Err("invalid or duplicate asset path: ${href}")
				}
				width = U64.from_str(width_text).map_err(|_| "invalid image width")?
				height = U64.from_str(height_text).map_err(|_| "invalid image height")?
				if width == 0 or height == 0 or width > 8192 or height > 8192 or width * height > 33554432 {
					return Err("image dimensions exceed limits")
				}
				$assets = $assets.append({ href, png, rgb, width, height, native: False })
				if $assets.len() > 256 {
					return Err("too many assets")
				}
				$rest = tail
			}
			["--html-only", .. as tail] => {
				if $mode != Both {
					return Err("choose one output mode")
				}
				$mode = HtmlOnly
				$rest = tail
			}
			["--pdf-only", .. as tail] => {
				if $mode != Both {
					return Err("choose one output mode")
				}
				$mode = PdfOnly
				$rest = tail
			}
			[key, value, .. as tail] => {
				if $seen.contains(key) {
					return Err("duplicate option: ${key}")
				}
				$seen = $seen.append(key)
				$metadata = match key {
					"--pdf-image-dpi" => {
						$dpi = U64.from_str(value).map_err(|_| "pdf-image-dpi must be an integer from 72 to 300")?
						if $dpi < 72 or $dpi > 300 {
							return Err("pdf-image-dpi must be from 72 to 300")
						}
						$metadata
					}
					"--title" => { ..$metadata, title: value }
					"--author" => { ..$metadata, author: value }
					"--version" => { ..$metadata, version: value }
					"--language" => { ..$metadata, language: value }
					"--pdf-filename" => { ..$metadata, pdf_filename: value }
					_ => return Err("unknown option: ${key}")
				}
				$rest = tail
			}
			_ => return Err("incomplete option; see --help")
		}
	}
	if $metadata.title.is_empty() or $metadata.language.is_empty() or !$metadata.language.to_utf8().all(|b| (b >= 'a' and b <= 'z') or (b >= 'A' and b <= 'Z') or (b >= '0' and b <= '9') or b == '-') {
		return Err("title and language must be valid")
	}
	if !Renderer.safe_path($metadata.pdf_filename) or $metadata.pdf_filename.contains("/") or !$metadata.pdf_filename.ends_with(".pdf") {
		return Err("pdf-filename must be a .pdf basename")
	}
	if $assets.any(|asset| asset.href == $metadata.pdf_filename) {
		return Err("asset conflicts with PDF filename")
	}
	Ok({ input, output, metadata: $metadata, mode: $mode, pdf_image_dpi: $dpi, assets: $assets })
}

png_dimension = |bytes, offset| {
	part = bytes.sublist({ start: offset, len: 4 })
	match part {
		[a, b, c, d] => a.to_u64() * 16777216 + b.to_u64() * 65536 + c.to_u64() * 256 + d.to_u64()
		_ => 0
	}
}
