import cli.Path
import cli.Env
import cli.Cmd
import cli.OsStr
import cli.Stdout
import BuildOptions
import BuildFiles
import Preparation
import SourceIncludes
import Renderer
import Png
import Svg
import ImageAssets
import BundledDiagrams
import Html
import WebAssets
import parser.Markdown
import Sha256
import pdf.Layout

## The native repository driver. Parsing, image preparation and rendering use
## in-process Roc code; only explicitly requested compiler API docs execute a tool.
RepositoryBuild :: [].{
	run! : List(Str) => Try({}, Str)
	run! = |args| {
		options = BuildOptions.parse(args)?
		workspace = Path.canonicalize!(Path.utf8(options.workspace)).map_err(|e| Str.inspect(e))?
		workspace_text = Path.to_str(workspace).map_err(|_| "workspace must be UTF-8")?
		if !BuildOptions.safe_text(workspace_text) {
			return Err("workspace path must not contain controls")
		}
		docs = BuildFiles.inside!(workspace, options.docs_directory)?
		if !Path.is_dir!(docs).map_err(|e| Str.inspect(e))? {
			return Err("docs-directory must be a directory")
		}
		output = BuildFiles.inside!(workspace, options.output_directory)?
		if Path.exists!(output).map_err(|e| Str.inspect(e))? {
			if options.mode == Prepare {
				return Err("preparation output must be a new directory")
			}
			if !Path.is_dir!(output).map_err(|e| Str.inspect(e))? {
				return Err("output must be a directory")
			}
			_ = BuildFiles.list!(output)?
		}
		if options.api_entrypoint != "" {
			api = BuildFiles.inside!(workspace, options.api_entrypoint)?
			if !Path.is_file!(api).map_err(|e| Str.inspect(e))? {
				return Err("API entrypoint must be a regular file")
			}
		}
		files = BuildFiles.list!(docs)?
		var $sources = []
		var $source_bytes = 0
		for file in files {
			if file.path.ends_with(".adoc") or file.path.ends_with(".md") {
				bytes = BuildFiles.read!(file.absolute, 16777216)?
				$source_bytes = $source_bytes + bytes.len()
				if $source_bytes > 33554432 {
					return Err("documentation sources exceed aggregate byte limit")
				}
				$sources = $sources.append({ path: file.path, source: Str.from_utf8(bytes).map_err(|_| "documentation source must be UTF-8")? })
			}
		}
		if !$sources.any(|file| file.path == options.entrypoint) {
			return Err("documentation entrypoint does not exist")
		}
		diagrams = load_diagrams!(docs)?
		expanded = SourceIncludes.expand!(workspace, options.docs_directory, options.output_directory, $sources)?
		prepared = Preparation.prepare(expanded.files, { entrypoint: options.entrypoint, version: options.docs_version, pdf_filename: options.pdf_filename, site_url: options.site_url, diagrams: diagrams.map(|d| { source_sha256: d.source_sha256, href: d.href }) })?
		metadata : Renderer.Metadata
		metadata = { title: if (options.title == "") prepared.metadata.title else options.title, author: if (options.author == "") prepared.metadata.author else options.author, language: if (options.language == "") prepared.metadata.language else options.language, version: options.docs_version, pdf_filename: options.pdf_filename }
		if metadata.title.is_empty() or metadata.language.is_empty() or ![metadata.title, metadata.author, metadata.language, metadata.version].all(BuildOptions.safe_text) {
			return Err("invalid document metadata")
		}
		assets = load_assets!(docs, prepared.images, diagrams, options.pdf_image_dpi)?
		stage = Path.utf8("${Path.display(output)}.roc-docs-stage")
		backup = Path.utf8("${Path.display(output)}.roc-docs-backup")
		if Path.exists!(stage).map_err(|e| Str.inspect(e))? or Path.exists!(backup).map_err(|e| Str.inspect(e))? {
			return Err("stale publication stage or backup exists; inspect before retrying")
		}
		Path.create_all!(BuildFiles.parent_path(output)).map_err(|e| Str.inspect(e))?
		Path.create_dir!(stage).map_err(|e| Str.inspect(e))?
		result = if (options.mode == Prepare) write_prepared!(stage, prepared, metadata, assets, $sources, expanded.origins) else render_site!(stage, prepared, metadata, assets, files, options, workspace)
		match result {
			Err(message) => {
				_ = Path.delete_all!(stage)
				return Err(message)
			}
			Ok(_) => {}
		}
		publish!(stage, output, backup, options.mode == Prepare)?
		if options.mode == Build {
			site = Path.join(output, "site")
			pdf = Path.join(output, options.pdf_filename)
			match Env.var_str!("GITHUB_OUTPUT") {
				Ok(target) if target != "" => Path.append_utf8!(Path.utf8(target), "site-directory=${Path.display(site)}\npdf-path=${Path.display(pdf)}\n").map_err(|e| Str.inspect(e))?
				_ => {}
			}
			Stdout.line!("Site: ${Path.display(site)}/index.html\nManual: ${Path.display(pdf)}").map_err(|e| Str.inspect(e))?
		} else {
			Stdout.line!("Prepared ${prepared.pages.len().to_str()} pages: ${Path.display(output)}").map_err(|e| Str.inspect(e))?
		}
		Ok({})
	}
}

Diagram : { source_sha256 : Str, href : Str, png_sha256 : Str, png : List(U8), svg : List(U8), svg_sha256 : Str }

Registry : { schema_version : U64, diagrams : List({ source_sha256 : Str, href : Str, png : Str, png_sha256 : Str }) }

RegistryVectors : { schema_version : U64, diagrams : List({ source_sha256 : Str, href : Str, png : Str, png_sha256 : Str, svg : Str, svg_sha256 : Str }) }

load_diagrams! : Path => Try(List(Diagram), Str)
load_diagrams! = |docs| {
	var $diagrams = []
	registry_path = BuildFiles.inside!(docs, "prepared-diagrams.json")?
	if Path.exists!(registry_path).map_err(|e| Str.inspect(e))? {
		bytes = BuildFiles.read!(registry_path, 1048576)?
		text = Str.from_utf8(bytes).map_err(|_| "diagram registry must be UTF-8")?
		header : { schema_version : U64 }
		header = Json.parse(text).map_err(|_| "invalid diagram registry")?
		entries = if header.schema_version == 1 {
			registry : Registry
			registry = Json.parse(text).map_err(|_| "invalid diagram registry")?
			registry.diagrams.map(|d| { source_sha256: d.source_sha256, href: d.href, png: d.png, png_sha256: d.png_sha256, svg: "", svg_sha256: "" })
		} else if header.schema_version == 2 {
			registry : RegistryVectors
			registry = Json.parse(text).map_err(|_| "invalid vector diagram registry")?
			registry.diagrams
		} else {
			return Err("unsupported diagram registry schema")
		}
		if entries.len() > 256 {
			return Err("diagram registry exceeds size limit")
		}
		for entry in entries {
			if !valid_digest(entry.source_sha256) or !valid_digest(entry.png_sha256) or !BuildOptions.safe_path(entry.href) {
				return Err("invalid diagram identity or path")
			}
			if $diagrams.any(|d| d.source_sha256 == entry.source_sha256) {
				return Err("duplicate local diagram source identity")
			}
			path = BuildFiles.inside!(docs, entry.png)?
			png = BuildFiles.read!(path, 16777216)?
			if Sha256.hex(png) != entry.png_sha256 {
				return Err("prepared diagram image hash mismatch: ${entry.href}")
			}
			svg = if (entry.svg == "") [] else BuildFiles.read!(BuildFiles.inside!(docs, entry.svg)?, 1048576)?
			if !svg.is_empty() {
				if !valid_digest(entry.svg_sha256) or Sha256.hex(svg) != entry.svg_sha256 {
					return Err("prepared SVG hash mismatch")
				}
				Svg.validate(svg)?
			} else if entry.svg != "" or entry.svg_sha256 != "" {
				return Err("incomplete prepared SVG identity")
			}
			$diagrams = $diagrams.append({ source_sha256: entry.source_sha256, href: entry.href, png_sha256: entry.png_sha256, png, svg, svg_sha256: entry.svg_sha256 })
		}
	}
	for entry in BundledDiagrams.list {
		if !$diagrams.any(|d| d.source_sha256 == entry.source_sha256) {
			$diagrams = $diagrams.append(entry)
		}
	}
	Ok($diagrams)
}

LoadedAsset : { href : Str, png : List(U8), rgb : List(U8), width : U64, height : U64, rendered : Renderer.Asset, svg : List(U8), svg_href : Str }

load_assets! : Path, List(Str), List(Diagram), U64 => Try(List(LoadedAsset), Str)
load_assets! = |docs, hrefs, diagrams, dpi| {
	var $assets = []
	var $total = 0
	for href in hrefs {
		if !$assets.any(|a| a.href == href) {
			if $assets.len() >= 256 or !BuildOptions.safe_path(href) {
				return Err("invalid image path or too many images")
			}
			var $svg = []
			png = match diagrams.find_first(|d| d.href == href) {
				Ok(d) => {
					if diagrams.any(|other| other.href == href and (other.png_sha256 != d.png_sha256 or other.svg_sha256 != d.svg_sha256)) {
						return Err("conflicting diagram image path")
					}
					if Sha256.hex(d.png) != d.png_sha256 {
						return Err("bundled diagram image hash mismatch")
					}
					if !d.svg.is_empty() {
						if Sha256.hex(d.svg) != d.svg_sha256 {
							return Err("bundled SVG hash mismatch")
						}
						Svg.validate(d.svg)?
						$svg = d.svg
					}
					d.png
				}
				Err(_) => BuildFiles.read!(BuildFiles.inside!(docs, href)?, 16777216)?
			}
			decoded = Png.decode(png)?
			$total = $total + png.len() + decoded.pixels.len() + $svg.len()
			if $total > 134217728 {
				return Err("prepared image assets exceed aggregate byte limit")
			}
			width = decoded.width * 750
			height = decoded.height * 750
			width_fit = if (width > 471000) 471000 else width
			height_fit = height * width_fit // width
			rendered_height = if (height_fit > 620000) 620000 else height_fit
			rendered_width = width_fit * rendered_height // height_fit
			raster = ImageAssets.fit(decoded.pixels, decoded.width, decoded.height, if (rendered_width * dpi < 72000) 1 else rendered_width * dpi // 72000, if (rendered_height * dpi < 72000) 1 else rendered_height * dpi // 72000).map_err(|_| "invalid decoded image")?
			rendered : Renderer.Asset
			rendered = { href, name: href, dimensions: { width: raster.width.to_u32_wrap(), height: raster.height.to_u32_wrap() }, png, pixels: raster.pixels, size: { width: Layout.Unit.millipoints(rendered_width.to_i64_wrap()), height: Layout.Unit.millipoints(rendered_height.to_i64_wrap()) } }
			$assets = $assets.append({ href, png, rgb: decoded.pixels, width: decoded.width, height: decoded.height, rendered, svg: $svg, svg_href: if ($svg.is_empty()) "" else href.drop_suffix(".png").concat(".svg") })
		}
	}
	Ok($assets)
}

render_site! = |stage, prepared, metadata, assets, files, options, workspace| {
	site = Path.join(stage, "site")
	Path.create_dir!(site).map_err(|e| Str.inspect(e))?
	var $routes = []
	for file in WebAssets.list {
		BuildFiles.write_bytes!(site, file.path, file.bytes)?
	}
	for page in prepared.pages {
		if !BuildOptions.safe_path(page.route) or page.route.contains("/") or !page.route.ends_with(".html") or $routes.contains(page.route) {
			return Err("invalid or duplicate page route")
		}
		$routes = $routes.append(page.route)
		html = match page.html {
			MarkdownSource => Renderer.render(page.source, metadata, assets.map(|a| a.rendered), HtmlOnly).map_ok(|r| r.html).map_err(|e| Str.inspect(e))?
			Prepared(presentation) => Html.document(presentation.body, Markdown.parse_str(page.source), metadata, presentation.numbered, presentation.toc_depth, presentation.remark)
		}
		BuildFiles.write_text!(site, page.route, Html.vector_images(html, assets.map(|a| { href: a.href, svg_href: a.svg_href })))?
	}
	if !$routes.contains("index.html") {
		entry_route = "${Str.join_with(options.entrypoint.split_on(".").drop_last(1), ".")}.html"
		entry = BuildFiles.inside!(site, entry_route)?
		BuildFiles.copy!(entry, Path.join(site, "index.html"))?
	}
	for asset in assets {
		if WebAssets.list.any(|f| f.path == asset.href or f.path == asset.svg_href) or $routes.contains(asset.href) or asset.href == "index.html" or asset.href == metadata.pdf_filename or (options.api_entrypoint != "" and (asset.href == "api" or asset.href.starts_with("api/"))) {
			return Err("image asset conflicts with publication output")
		}
		BuildFiles.write_bytes!(site, asset.href, asset.png)?
		if asset.svg_href != "" {
			if $routes.contains(asset.svg_href) or asset.svg_href == metadata.pdf_filename or asset.svg_href == "index.html" or assets.any(|other| other.href == asset.svg_href or (other.href != asset.href and other.svg_href == asset.svg_href)) {
				return Err("vector image asset conflicts with publication output")
			}
			BuildFiles.write_bytes!(site, asset.svg_href, asset.svg)?
		}
	}
	for file in files {
		if !file.path.ends_with(".adoc") and !file.path.ends_with(".md") and !file.path.ends_with(".markdown") and !file.path.starts_with("theme/") {
			target = BuildFiles.inside!(site, file.path)?
			if Path.exists!(target).map_err(|e| Str.inspect(e))? {
				if BuildFiles.read!(target, 16777216)? != BuildFiles.read!(file.absolute, 16777216)? {
					return Err("static file collides with rendered publication: ${file.path}")
				}
			} else {
				BuildFiles.copy!(file.absolute, target)?
			}
		}
	}
	rendered = Renderer.render(prepared.manual, metadata, assets.map(|a| a.rendered), PdfOnly).map_err(|e| Str.inspect(e))?
	pdf_in_site = Path.join(site, metadata.pdf_filename)
	if Path.exists!(pdf_in_site).map_err(|e| Str.inspect(e))? {
		existing_pdf = BuildFiles.read!(pdf_in_site, 16777216)?
		if existing_pdf != rendered.pdf {
			return Err("static file collides with PDF output")
		}
	}
	BuildFiles.write_bytes!(stage, metadata.pdf_filename, rendered.pdf)?
	BuildFiles.write_bytes!(site, metadata.pdf_filename, rendered.pdf)?
	if options.api_entrypoint != "" {
		if $routes.contains("api.html") {
			return Err("API output conflicts with page")
		}
		api = Path.join(site, "api")
		if Path.exists!(api).map_err(|e| Str.inspect(e))? {
			Path.delete_all!(api).map_err(|e| Str.inspect(e))?
		}
		entrypoint = BuildFiles.inside!(workspace, options.api_entrypoint)?
		Cmd.new(OsStr.from_str(options.roc_command)).args(["docs", Path.display(entrypoint), "--output=${Path.display(api)}"].map(OsStr.from_str)).cwd(workspace).exec!().map_err(|e| Str.inspect(e))?
		if !Path.is_file!(Path.join(api, "index.html")).map_err(|e| Str.inspect(e))? {
			return Err("Roc API reference was not generated")
		}
		_ = BuildFiles.list!(site)?
	}
	Ok({})
}

write_prepared! : Path, Preparation.Result, Renderer.Metadata, List(LoadedAsset), List(Preparation.File), List(SourceIncludes.Origin) => Try({}, Str)
write_prepared! = |stage, prepared, metadata, assets, sources, includes| {
	for page in prepared.pages {
		BuildFiles.write_text!(stage, "pages/${page.route.drop_suffix(".html")}.md", page.source)?
	}
	BuildFiles.write_text!(stage, "manual.md", prepared.manual)?
	for asset in assets {
		stem = Sha256.hex(asset.png)
		BuildFiles.write_bytes!(stage, "assets/${stem}.png", asset.png)?
		BuildFiles.write_bytes!(stage, "assets/${stem}.rgb", asset.rgb)?
	}
	manifest = { schema_version: 2.U64, includes, metadata, pages: prepared.pages.map(|p| { input: "pages/${p.route.drop_suffix(".html")}.md", output: p.route }), manual: { input: "manual.md", output: metadata.pdf_filename }, assets: assets.map(|a| { href: a.href, png: "assets/${Sha256.hex(a.png)}.png", rgb: "assets/${Sha256.hex(a.png)}.rgb", width: a.width, height: a.height, png_sha256: Sha256.hex(a.png), rgb_sha256: Sha256.hex(a.rgb) }), sources: sources.map(|s| { path: s.path, sha256: Sha256.hex(s.source.to_utf8()) }) }
	BuildFiles.write_text!(stage, "manifest.json", Json.to_str(manifest))
}

publish! : Path, Path, Path, Bool => Try({}, Str)
publish! = |stage, output, backup, require_new| {
	exists = Path.exists!(output).map_err(|e| Str.inspect(e))?
	if exists {
		if require_new {
			return Err("preparation output already exists")
		}
		_ = BuildFiles.list!(output)?
		Path.rename!(output, backup).map_err(|e| Str.inspect(e))?
	}
	match Path.rename!(stage, output) {
		Err(error) => {
			if exists {
				Path.rename!(backup, output).map_err(|restore| "publication and rollback failed: ${Str.inspect(error)}; ${Str.inspect(restore)}")?
			}
			return Err("publication failed: ${Str.inspect(error)}")
		}
		Ok(_) => {}
	}
	if exists {
		Path.delete_all!(backup).map_err(|e| Str.inspect(e))?
	}
	Ok({})
}

valid_digest = |value| value.to_utf8().len() == 64 and value.to_utf8().all(|b| (b >= '0' and b <= '9') or (b >= 'a' and b <= 'f'))
