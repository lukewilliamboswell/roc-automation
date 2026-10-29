import mermaid.Svg
import mermaid.Drawing
import DiagramAdapter
import "fonts/Inter-OFL.txt" as inter_notice : Str
import "fonts/Inter-Regular.ttf" as regular : List(U8)
import "fonts/Inter-Bold.ttf" as bold : List(U8)
import "fonts/Inter-Italic.ttf" as italic : List(U8)
import "fonts/Inter-BoldItalic.ttf" as bold_italic : List(U8)

## Only generated geometry and escaped upstream text enter this serializer.
## Authored SVG validation deliberately does not admit CSS or embedded resources.
DiagramSvg :: [].{
	document : DiagramAdapter.Picture -> List(U8)
	document = |picture| raw_document(DiagramAdapter.raw_picture(picture))
}

raw_document : Drawing.Picture -> List(U8)
raw_document = |picture| {
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
	result =
		\\${svg_opening(lines)}
		\\<metadata id="roc-docs-font-license">${notice}</metadata>
		\\<defs><style>${$css}</style></defs>
		\\${Str.join_with(lines.drop_first(1), "\n")}
	result.to_utf8()
}

svg_opening : List(Str) -> Str
svg_opening = |lines| match lines.first() {
	Ok(line) if !line.is_empty() => line
	_ => crash "generated diagram SVG has no opening element"
}

base64 : List(U8) -> Str
base64 = |bytes| {
	var $out = List.with_capacity((bytes.len() + 2) // 3 * 4)
	var $i = 0.U64
	while $i < bytes.len() {
		a = match bytes.get($i) {
			Ok(value) => value.to_u64()
			Err(_) => crash "Base64 cursor exceeds input"
		}
		# Only the incomplete final group is zero-padded before adding '=' markers.
		b = (bytes.get($i + 1) ?? 0).to_u64()
		c = (bytes.get($i + 2) ?? 0).to_u64()
		$out = $out.append(digit(a // 4)).append(digit(a % 4 * 16 + b // 16)).append(if ($i + 1 < bytes.len()) digit(b % 16 * 4 + c // 64) else '=').append(if ($i + 2 < bytes.len()) digit(c % 64) else '=')
		$i = $i + 3
	}
	match Str.from_utf8($out) {
		Ok(value) => value
		Err(_) => crash "Base64 alphabet produced invalid UTF-8"
	}
}

alphabet : List(U8)
alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/".to_utf8()

digit : U64 -> U8
digit = |index| match alphabet.get(index) {
	Ok(value) => value
	Err(_) => crash "Base64 digit exceeds alphabet"
}

FaceWeight : [RegularWeight, BoldWeight]

FaceStyle : [Upright, Slanted]

face : { bytes : List(U8), weight : FaceWeight, style : FaceStyle } -> Str
face = |{ bytes, weight, style }| {
	css_weight = match weight {
		RegularWeight => "400"
		BoldWeight => "700"
	}
	css_style = match style {
		Upright => "normal"
		Slanted => "italic"
	}
	"@font-face{font-family:RocDocsDiagram;src:url(data:font/ttf;base64,${base64(bytes)}) format('truetype');font-weight:${css_weight};font-style:${css_style};}"
}

notice : Str
notice = inter_notice.replace_each("&", "&amp;").replace_each("<", "&lt;").replace_each(">", "&gt;")

regular_css : Str
regular_css = face({ bytes: regular, weight: RegularWeight, style: Upright })

bold_css : Str
bold_css = face({ bytes: bold, weight: BoldWeight, style: Upright })

italic_css : Str
italic_css = face({ bytes: italic, weight: RegularWeight, style: Slanted })

bold_italic_css : Str
bold_italic_css = face({ bytes: bold_italic, weight: BoldWeight, style: Slanted })

## Base64 keeps complete triples intact and pads only the missing final bytes.
expect ["", "f", "fo", "foo", "foob", "fooba", "foobar"].map(|text| base64(text.to_utf8())) == ["", "Zg==", "Zm8=", "Zm9v", "Zm9vYg==", "Zm9vYmE=", "Zm9vYmFy"]

## Base64 treats all input bytes as unsigned and uses both high alphabet digits.
expect base64([0, 255, 128, 251, 255, 255]) == "AP+A+///"

## Fixed face records serialize exact CSS without admitting author-supplied font styles.
expect face({ bytes: [0, 255, 128], weight: BoldWeight, style: Slanted }) == "@font-face{font-family:RocDocsDiagram;src:url(data:font/ttf;base64,AP+A) format('truetype');font-weight:700;font-style:italic;}"
