import parser.Xml

## Static prepared SVG image profile. Never embedded as authored page markup.
## No scripts, event attributes, external resources, processing instructions or
## CSS escapes/imports. Small XHTML label containers support prepared Mermaid.
Svg :: [].{
	validate : List(U8) -> Try({}, Str)
	validate = |bytes| {
		if bytes.len() > 1048576 {
			return Err("prepared SVG exceeds byte limit")
		}
		source = Str.from_utf8(bytes).map_err(|_| "prepared SVG must be UTF-8")?
		if source.contains("<?") or source.contains("<!") {
			return Err("SVG declarations and processing instructions are unsupported")
		}
		parsed = Xml.parse_str(source).map_err(|_| "invalid prepared SVG XML")?
		if parsed.root.name() != Ok("svg") or parsed.root.attribute("xmlns") != Ok("http://www.w3.org/2000/svg") {
			return Err("prepared SVG requires an svg root")
		}
		check(parsed.root, 0)
	}
}

check : Xml.Node, U64 -> Try({}, Str)
check = |node, depth| {
	if depth > 64 {
		return Err("prepared SVG nesting exceeds limit")
	}
	match node {
		Xml.Node.Text(_) => Ok({})
		Xml.Node.Element(element) => {
			if !["svg", "g", "defs", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan", "marker", "linearGradient", "radialGradient", "stop", "filter", "feDropShadow", "foreignObject", "div", "span", "p", "br", "style"].contains(element.name) {
				return Err("unsupported prepared SVG element: ${element.name}")
			}
			if element.name == "style" and !safe_css(node.text()) {
				return Err("external or unsupported SVG CSS")
			}
			for attr in element.attributes {
				if !["xmlns", "xmlns:xlink", "aria-roledescription", "class", "id", "role", "width", "height", "viewBox", "preserveAspectRatio", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "d", "points", "transform", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "opacity", "fill-opacity", "stroke-opacity", "marker-start", "marker-mid", "marker-end", "markerHeight", "markerWidth", "markerUnits", "refX", "refY", "orient", "gradientUnits", "gradientTransform", "offset", "stop-color", "stop-opacity", "stdDeviation", "dx", "dy", "flood-color", "flood-opacity", "filter", "font-family", "font-size", "font-weight", "text-anchor", "dominant-baseline", "style", "data-edge", "data-et", "data-id", "data-look", "data-points"].contains(attr.name) {
					return Err("unsupported prepared SVG attribute: ${attr.name}")
				}
				if attr.name == "xmlns" and !["http://www.w3.org/2000/svg", "http://www.w3.org/1999/xhtml"].contains(attr.value) {
					return Err("unsupported SVG namespace")
				}
				if attr.name == "xmlns:xlink" and attr.value != "http://www.w3.org/1999/xlink" {
					return Err("unsupported SVG xlink namespace")
				}
				if attr.name != "xmlns" and attr.name != "xmlns:xlink" and !safe_css(attr.value) {
					return Err("external or unsupported SVG attribute: ${attr.name}")
				}
			}
			for child in element.children {
				check(child, depth + 1)?
			}
			Ok({})
		}
	}
}

safe_css = |value| {
	lower = Str.from_utf8_lossy(value.to_utf8().map(|b| if b >= 'A' and b <= 'Z' b + 32 else b))
	if lower.contains("://") or lower.contains("//") or lower.contains("\\") or lower.contains("/*") or lower.contains("@import") or lower.contains("expression(") or lower.contains("-moz-binding") or lower.contains("behavior:") {
		return False
	}
	# Reject resource-bearing CSS functions such as image-set("remote.png").
	# Every nested function is inspected; only static colour/geometry/selector
	# functions and local fragment URLs belong to this prepared-image profile.
	for prefix in lower.split_on("(").drop_last(1) {
		bytes = prefix.to_utf8()
		var $start = bytes.len()
		while $start > 0 and (
			match bytes.get($start - 1) {
				Ok(b) => (b >= 'a' and b <= 'z') or b == '-'
				Err(_) => False
			}
		) {
			$start = $start - 1
		}
		name = Str.from_utf8_lossy(bytes.drop_first($start))
		if !["drop-shadow", "rgb", "rgba", "hsl", "hsla", "url", "translate", "translatex", "translatey", "scale", "scalex", "scaley", "rotate", "matrix", "calc", "var", "not", "is", "where", "nth-child"].contains(name) {
			return False
		}
	}

	for part in lower.split_on("url").drop_first(1) {
		if part.trim().starts_with("(") {
			pieces = part.trim().drop_prefix("(").split_on(")")
			if pieces.len() < 2 {
				return False
			}
			target = (pieces.first() ?? "").trim().drop_prefix("\"").drop_suffix("\"").drop_prefix("'").drop_suffix("'")
			if !target.starts_with("#") or target.to_utf8().len() < 2 or !target.drop_prefix("#").to_utf8().all(|b| (b >= 'a' and b <= 'z') or (b >= '0' and b <= '9') or b == '-' or b == '_') {
				return False
			}
		}
	}
	True
}
