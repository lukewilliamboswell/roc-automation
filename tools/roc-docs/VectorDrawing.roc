import mermaid.Drawing
import pdf.Scene
import pdf.Layout
import pdf.Color
import font.Font
import "fonts/Inter-Regular.ttf" as regular_bytes : List(U8)
import "fonts/Inter-Bold.ttf" as bold_bytes : List(U8)
import "fonts/Inter-Italic.ttf" as italic_bytes : List(U8)
import "fonts/Inter-BoldItalic.ttf" as bold_italic_bytes : List(U8)

## Convert the admitted ordered picture, including baseline alignment, to PDF y-up geometry.
VectorDrawing :: [].{
	unit = |pixels| Layout.Unit.millipoints((pixels * 750).round_to_i64_try() ?? 0)
	color = |c| Color.srgb8(c)
	point = |p, view| { x: unit(p.x - view.x), y: unit(view.y + view.height - p.y) }
	font = |bytes| match Font.from_bytes(bytes, Font.ReadOptions.{}) {
		Ok(face) => face
		Err(_) => crash "reviewed diagram font is invalid"
	}
	regular = font(regular_bytes)
	bold = font(bold_bytes)
	italic = font(italic_bytes)
	bold_italic = font(bold_italic_bytes)

	drawing : Drawing.Picture -> Scene.Drawing
	drawing = |picture| {
		var $drawing = Scene.Drawing.empty
		for item in picture.items {
			match item {
				Shape(shape) => {
					path = shape.path.map(
						|segment| match segment {
							MoveTo(p) => MoveTo(point(p, picture.view))
							LineTo(p) => LineTo(point(p, picture.view))
							CubicTo(c) => CubicTo({ control_1: point(c.control_1, picture.view), control_2: point(c.control_2, picture.view), end: point(c.end, picture.view) })
							Close => Close
						},
					)
					fill = match shape.paint.fill {
						NoFill => AuthorNoFill
						Fill(c) => AuthorSolidFill(color(c))
					}
					stroke = match shape.paint.stroke {
						NoStroke => AuthorNoStroke
						Stroke(s) => AuthorSolidStroke({ color: color(s.color), width: unit(s.width) })
					}
					$drawing = $drawing.path(path, { fill, stroke })
				}
				Text(label) => {
					$drawing = add_label($drawing, label, False, False, False, picture.view)
				}
				StyledText(s) => {
					$drawing = add_label($drawing, s.label, s.bold, s.italic, s.underline, picture.view)
				}
			}
		}
		$drawing
	}

	add_label = |base, label, is_bold, is_italic, underline, view| {
		pdf_label : Scene.Label
		pdf_label = { text: label.text, origin: point(label.origin, view), size: unit(label.size), align: label.align, color: color(label.color) }
		var $out = if is_bold and is_italic {
			base.text_in(Quote, pdf_label)
		} else if is_bold {
			base.text_in(Strong, pdf_label)
		} else if is_italic {
			base.text_in(Emphasis, pdf_label)
		} else base.text(pdf_label)
		if underline {
			face = if is_bold and is_italic {
				bold_italic
			} else if is_bold {
				bold
			} else if is_italic {
				italic
			} else regular
			width = Font.width(face, label.text).to_f64() * label.size / Font.units_per_em(face).to_f64()
			left = label.origin.x - (
				match label.align {
					Start => 0
					Center => width / 2
					End => width
				}
			)
			y = label.origin.y + label.size * 0.1
			path = Scene.PathBuilder.start.move_to(point({ x: left, y }, view)).line_to(point({ x: left + width, y }, view)).finish()
			$out = $out.path(path, Scene.solid_stroke(color(label.color), unit(label.size * 0.05)))
		}
		$out
	}
}
