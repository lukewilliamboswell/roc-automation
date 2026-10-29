import mermaid.Svg
import mermaid.Drawing
import "fonts/Inter-OFL.txt" as inter_notice : Str
import "fonts/Inter-Regular.ttf" as regular : List(U8)
import "fonts/Inter-Bold.ttf" as bold : List(U8)
import "fonts/Inter-Italic.ttf" as italic : List(U8)
import "fonts/Inter-BoldItalic.ttf" as bold_italic : List(U8)

## Only generated geometry and escaped upstream text enter this serializer.
## Authored SVG validation deliberately does not admit CSS or embedded resources.
DiagramSvg :: [].{
	base64 = |bytes| {
		alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/".to_utf8()
		digit = |n| alphabet.get(n) ?? '='
		var $out = List.with_capacity((bytes.len() + 2) // 3 * 4)
		var $i = 0.U64
		while $i < bytes.len() {
			a = (bytes.get($i) ?? 0).to_u64()
			b = (bytes.get($i + 1) ?? 0).to_u64()
			c = (bytes.get($i + 2) ?? 0).to_u64()
			$out = $out.append(digit(a // 4)).append(digit(a % 4 * 16 + b // 16)).append(if ($i + 1 < bytes.len()) digit(b % 16 * 4 + c // 64) else '=').append(if ($i + 2 < bytes.len()) digit(c % 64) else '=')
			$i = $i + 3
		}
		Str.from_utf8_lossy($out)
	}
	face = |bytes, weight, style| "@font-face{font-family:RocDocsDiagram;src:url(data:font/ttf;base64,${base64(bytes)}) format('truetype');font-weight:${weight};font-style:${style};}"
	notice = inter_notice.replace_each("&", "&amp;").replace_each("<", "&lt;").replace_each(">", "&gt;")
	regular_css = face(regular, "400", "normal")
	bold_css = face(bold, "700", "normal")
	italic_css = face(italic, "400", "italic")
	bold_italic_css = face(bold_italic, "700", "italic")
	document : Drawing.Picture -> List(U8)
	document = |picture| {
		var $css = regular_css
		var $bold = False
		var $italic = False
		var $both = False
		for item in picture.items {
			match item {
				StyledText(s) => {
					$both = $both or (s.bold and s.italic)
					$bold = $bold or (s.bold and !s.italic)
					$italic = $italic or (s.italic and !s.bold)
				}
				_ => {}
			}
		}
		if $bold {
			$css = $css.concat(bold_css)
		}
		if $italic {
			$css = $css.concat(italic_css)
		}
		if $both {
			$css = $css.concat(bold_italic_css)
		}
		# Insert fixed reviewed CSS before generated items; never rewrite label bytes.
		svg = Svg.document(picture, "RocDocsDiagram")
		lines = svg.split_on("\n")
		"${lines.first() ?? ""}\n<metadata id=\"roc-docs-font-license\">${notice}</metadata>\n<defs><style>${$css}</style></defs>\n${Str.join_with(lines.drop_first(1), "\n")}".to_utf8()
	}
}
