import mermaid.Drawing
import DiagramAdapter
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
	drawing : DiagramAdapter.Picture -> Scene.Drawing
	drawing = |picture| raw_drawing(DiagramAdapter.raw_picture(picture))
}

raw_drawing : Drawing.Picture -> Scene.Drawing
raw_drawing = |picture| {
	var $drawing = Scene.Drawing.empty
	for item in picture.items {
		match item {
			Shape(shape) => {
				path = shape.path.map(
					|segment| match segment {
						MoveTo(p) => MoveTo(point({ point: p, view: picture.view }))
						LineTo(p) => LineTo(point({ point: p, view: picture.view }))
						CubicTo(c) => CubicTo({ control_1: point({ point: c.control_1, view: picture.view }), control_2: point({ point: c.control_2, view: picture.view }), end: point({ point: c.end, view: picture.view }) })
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
				$drawing = add_label({ base: $drawing, label, style: { bold: False, italic: False, underline: False }, view: picture.view })
			}
			StyledText(s) => {
				$drawing = add_label({ base: $drawing, label: s.label, style: { bold: s.bold, italic: s.italic, underline: s.underline }, view: picture.view })
			}
		}
	}
	$drawing
}

add_label : { base : Scene.Drawing, label : Drawing.Label, style : Style, view : Drawing.Rect } -> Scene.Drawing
add_label = |{ base, label, style, view }| {
	pdf_label : Scene.Label
	pdf_label = { text: label.text, origin: point({ point: label.origin, view }), size: unit(label.size), align: label.align, color: color(label.color) }
	var $out = if style.bold and style.italic {
		base.text_in(Quote, pdf_label)
	} else if style.bold {
		base.text_in(Strong, pdf_label)
	} else if style.italic {
		base.text_in(Emphasis, pdf_label)
	} else base.text(pdf_label)
	if style.underline {
		face = if style.bold and style.italic {
			bold_italic
		} else if style.bold {
			bold
		} else if style.italic {
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
		path = Scene.PathBuilder.start.move_to(point({ point: { x: left, y }, view })).line_to(point({ point: { x: left + width, y }, view })).finish()
		$out = $out.path(path, Scene.solid_stroke(color(label.color), unit(label.size * 0.05)))
	}
	$out
}

Style : { bold : Bool, italic : Bool, underline : Bool }

unit : F64 -> Layout.Unit
unit = |pixels| Layout.Unit.millipoints(
	match (pixels * 750).round_to_i64_try() {
		Ok(value) => value
		Err(_) => crash "admitted diagram coordinate exceeds PDF units"
	},
)

color : Drawing.Color -> Color.SourceValue
color = |value| Color.srgb8(value)

point : { point : Drawing.Point, view : Drawing.Rect } -> Layout.Point
point = |args| { x: unit(args.point.x - args.view.x), y: unit(args.view.y + args.view.height - args.point.y) }

font : List(U8) -> Font
font = |bytes| match Font.from_bytes(bytes, Font.ReadOptions.{}) {
	Ok(face) => face
	Err(_) => crash "reviewed diagram font is invalid"
}

regular : Font
regular = font(regular_bytes)

bold : Font
bold = font(bold_bytes)

italic : Font
italic = font(italic_bytes)

bold_italic : Font
bold_italic = font(bold_italic_bytes)

## Path conversion preserves painting order and all cubic controls while translating and reversing y.
expect {
	picture : Drawing.Picture
	picture = {
		view: { x: 10, y: 20, width: 100, height: 80 },
		items: [
			Shape({
				path: [MoveTo({ x: 10, y: 20 }), LineTo({ x: 30, y: 40 }), CubicTo({ control_1: { x: 40, y: 50 }, control_2: { x: 60, y: 70 }, end: { x: 90, y: 100 } }), Close],
				paint: { fill: Fill(Drawing.white), stroke: Stroke({ color: Drawing.black, width: 2 }) },
			}),
		],
	}
	actual = match raw_drawing(picture).commands() {
		[AuthorPath(path)] => Ok(path)
		_ => Err(UnexpectedCommands)
	}?
	{ path: actual.path, fill: actual.style.fill, stroke: actual.style.stroke } == {
		path: [MoveTo({ x: Layout.Unit.millipoints(0), y: Layout.Unit.millipoints(60000) }), LineTo({ x: Layout.Unit.millipoints(15000), y: Layout.Unit.millipoints(45000) }), CubicTo({ control_1: { x: Layout.Unit.millipoints(22500), y: Layout.Unit.millipoints(37500) }, control_2: { x: Layout.Unit.millipoints(37500), y: Layout.Unit.millipoints(22500) }, end: { x: Layout.Unit.millipoints(60000), y: Layout.Unit.millipoints(0) } }), Close],
		fill: AuthorSolidFill(Color.srgb8({ red: 255, green: 255, blue: 255 })),
		stroke: AuthorSolidStroke({ color: Color.srgb8({ red: 0, green: 0, blue: 0 }), width: Layout.Unit.millipoints(1500) }),
	}
}

## Plain and styled labels retain their exact face, baseline, size, alignment and order.
expect {
	label : Drawing.Label
	label = { text: "A", origin: { x: 50, y: 40 }, size: 12, align: End, color: Drawing.black }
	picture : Drawing.Picture
	picture = { view: { x: 10, y: 20, width: 100, height: 80 }, items: [Text(label), StyledText({ label, bold: True, italic: False, underline: False }), StyledText({ label, bold: False, italic: True, underline: False }), StyledText({ label, bold: True, italic: True, underline: False })] }
	expected_label : Scene.Label
	expected_label = { text: "A", origin: { x: Layout.Unit.millipoints(30000), y: Layout.Unit.millipoints(45000) }, size: Layout.Unit.millipoints(9000), align: End, color: Color.srgb8({ red: 0, green: 0, blue: 0 }) }
	actual = match raw_drawing(picture).commands() {
		[AuthorText(plain), AuthorText(strong), AuthorText(emphasis), AuthorText(quote)] => Ok([Box.unbox(plain), Box.unbox(strong), Box.unbox(emphasis), Box.unbox(quote)])
		_ => Err(UnexpectedCommands)
	}?
	actual.map(|text| { face: text.face, origin: text.label.origin, size: text.label.size, align: text.label.align, color: text.label.color, text: text.label.text }) == [BodyFace, RoleFace(Strong), RoleFace(Emphasis), RoleFace(Quote)].map(|face| { face, origin: expected_label.origin, size: expected_label.size, align: expected_label.align, color: expected_label.color, text: expected_label.text })
}

## Underlines use reviewed Inter advances and honor start, center and end alignment.
expect {
	label : Drawing.Label
	label = { text: "A", origin: { x: 50, y: 40 }, size: 12, align: Start, color: Drawing.black }
	view : Drawing.Rect
	view = { x: 10, y: 20, width: 100, height: 80 }
	cases = [
		{ align: Start, bold: False, italic: False, left: 30000.I64, right: 36209.I64 },
		{ align: Center, bold: True, italic: False, left: 26640.I64, right: 33360.I64 },
		{ align: End, bold: False, italic: True, left: 23791.I64, right: 30000.I64 },
		{ align: End, bold: True, italic: True, left: 23281.I64, right: 30000.I64 },
	]
	var $actual = []
	for case in cases {
		commands = add_label({ base: Scene.Drawing.empty, label: { ..label, align: case.align }, style: { bold: case.bold, italic: case.italic, underline: True }, view }).commands()
		underline_path = match commands {
			[AuthorText(_), AuthorPath(path)] => Ok(path)
			_ => Err(UnexpectedCommands)
		}?
		$actual = $actual.append({ path: underline_path.path, fill: underline_path.style.fill, stroke: underline_path.style.stroke })
	}
	expected = cases.map(
		|case| {
			path: [MoveTo({ x: Layout.Unit.millipoints(case.left), y: Layout.Unit.millipoints(44100) }), LineTo({ x: Layout.Unit.millipoints(case.right), y: Layout.Unit.millipoints(44100) })],
			fill: AuthorNoFill,
			stroke: AuthorSolidStroke({ color: Color.srgb8({ red: 0, green: 0, blue: 0 }), width: Layout.Unit.millipoints(450) }),
		},
	)
	$actual == expected
}
