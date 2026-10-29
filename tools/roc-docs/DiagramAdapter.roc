import mermaid.Mermaid
import mermaid.Drawing
import mermaid.Flowchart
import mermaid.Sequence
import mermaid.EntityRelationship
import mermaid.ClassDiagram
import mermaid.StateDiagram
import parser.Markdown
import Sha256

## Pure admission, bounded layout and source-addressed caching shared by all commands.
DiagramAdapter :: [].{

	## Only admission can construct a diagram, cache or renderer-ready picture.
	Picture :: Drawing.Picture
	Diagram :: { source_sha256 : Str, href : Str, picture : Picture }
	Cache :: List(Diagram)

	Problem : [SourceTooLarge, TooManyDiagrams, ElementLimit, NestingLimit, InvalidContainer(U64), InvalidPicture, GeometryLimit, MarkdownNestingLimit, Upstream(List(Mermaid.Problem))]
	Error : { path : Str, ordinal : U64, problem : Problem }
	Admission : { source : Str, path : Str, ordinal : U64, cache : Cache }
	Admitted : { diagram : Diagram, cache : Cache }
	MarkdownInput : { blocks : List(Markdown), path : Str, ordinal : U64, cache : Cache, depth : U64 }
	MarkdownResult : { blocks : List(Markdown), cache : Cache, ordinal : U64 }

	empty_cache : Cache
	empty_cache = Cache.([])

	diagrams : Cache -> List(Diagram)
	diagrams = |Cache.(cached)| cached

	href : Diagram -> Str
	href = |Diagram.(diagram)| diagram.href

	source_sha256 : Diagram -> Str
	source_sha256 = |Diagram.(diagram)| diagram.source_sha256

	picture : Diagram -> Picture
	picture = |Diagram.(diagram)| diagram.picture

	raw_picture : Picture -> Drawing.Picture
	raw_picture = |Picture.(raw)| raw

	find_digest : Cache, Str -> Try(Diagram, [NotFound])
	find_digest = |Cache.(cached), digest| cached.find_first(|Diagram.(diagram)| diagram.source_sha256 == digest)

	admit : Admission -> Try(Admitted, Error)
	admit = |input| {
		context = |problem| { path: input.path, ordinal: input.ordinal, problem }
		if input.source.count_utf8_bytes() > max_source_bytes {
			return Err(context(SourceTooLarge))
		}
		digest = Sha256.hex(input.source.to_utf8())
		match find_digest(input.cache, digest) {
			Ok(diagram) => return Ok({ diagram, cache: input.cache })
			Err(NotFound) => {}
		}
		cached = diagrams(input.cache)
		if cached.len() >= max_diagrams {
			return Err(context(TooManyDiagrams))
		}
		raw = checked_draw(input.source).map_err(context)?
		diagram = Diagram.({ source_sha256: digest, href: "images/diagram-${digest}.svg", picture: Picture.(raw) })
		Ok({ diagram, cache: Cache.(cached.append(diagram)) })
	}

	## Render typed upstream and admission diagnostics only at the command boundary.
	message : Error -> Str
	message = |error| {
		context = match error.problem {
			MarkdownNestingLimit => error.path
			_ => "${error.path}: diagram ${error.ordinal.to_str()}"
		}
		"${context}: ${problem_message(error.problem)}"
	}

	markdown : MarkdownInput -> Try(MarkdownResult, Error)
	markdown = |input| {
		if input.depth > max_nesting {
			return Err({ path: input.path, ordinal: input.ordinal, problem: MarkdownNestingLimit })
		}
		var $cache = input.cache
		var $ordinal = input.ordinal
		var $out = []
		for block in input.blocks {
			next = match block {
				Code({ info, pre }) if (info.split_on(" ").first() ?? "") == "mermaid" => {
					$ordinal = $ordinal + 1
					r = admit({ source: pre, path: input.path, ordinal: $ordinal, cache: $cache })?
					$cache = r.cache
					Paragraph([Image({ alt: [Text("Diagram")], target: { href: href(r.diagram), title: Err(Missing) } })])
				}
				Blockquote(children) => {
					r = markdown({ ..input, blocks: children, ordinal: $ordinal, cache: $cache, depth: input.depth + 1 })?
					$cache = r.cache
					$ordinal = r.ordinal
					Blockquote(r.blocks)
				}
				ListBlock({ kind, loose, items }) => {
					var $items = []
					for item in items {
						r = markdown({ ..input, blocks: item.blocks, ordinal: $ordinal, cache: $cache, depth: input.depth + 1 })?
						$cache = r.cache
						$ordinal = r.ordinal
						$items = $items.append({ ..item, blocks: r.blocks })
					}
					ListBlock({ kind, loose, items: $items })
				}
				_ => block
			}
			$out = $out.append(next)
		}
		Ok({ blocks: $out, cache: $cache, ordinal: $ordinal })
	}
}

max_source_bytes : U64
max_source_bytes = 65536

max_diagrams : U64
max_diagrams = 256

max_elements : U64
max_elements = 256

max_rows : U64
max_rows = 1024

max_nesting : U64
max_nesting = 32

max_view_size : F64
max_view_size = 32768

max_coordinate : F64
max_coordinate = 1000000000

max_geometry : U64
max_geometry = 262144

max_items : U64
max_items = 65536

problem_message : DiagramAdapter.Problem -> Str
problem_message = |problem| match problem {
	SourceTooLarge => "Mermaid source exceeds 64 KiB"
	TooManyDiagrams => "more than 256 unique diagrams"
	ElementLimit => "Mermaid exceeds 256 elements or 1024 relationships/events/member rows"
	NestingLimit => "Mermaid nesting exceeds 32"
	InvalidContainer(index) => "invalid Mermaid group reference ${index.to_str()}"
	InvalidPicture => "empty, nonfinite or oversized Mermaid drawing"
	GeometryLimit => "Mermaid drawing exceeds geometry limit"
	MarkdownNestingLimit => "Markdown diagram nesting exceeds 32"
	Upstream(problems) => Str.inspect(problems)
}

checked_draw : Str -> Try(Drawing.Picture, DiagramAdapter.Problem)
checked_draw = |source| {
	header = match source.split_on("\n").map(Str.trim).find_first(|line| !line.is_empty() and !line.starts_with("%%")) {
		Ok(line) => line
		Err(_) => ""
	}
	declaration = match header.split_on("%%") {
		[first, ..] => match first.split_on(";") {
			[part, ..] => part.trim()
			[] => crash "split_on always returns a component"
		}
		[] => crash "split_on always returns a component"
	}
	match declaration {
		"sequenceDiagram" => {
			d = Sequence.parse(source).map_err(|problems| Upstream(problems))?
			limits({ elements: d.participants.len(), rows: d.events.len() })?
			var $depth = 0.U64
			for event in d.events {
				match event {
					StartBlock(_) => {
						$depth = $depth + 1
					}
					EndBlock => {
						$depth = if ($depth > 0) $depth - 1 else 0
					}
					_ => {}
				}
				if $depth > max_nesting {
					return Err(NestingLimit)
				}
			}
		}
		"erDiagram" => {
			d = EntityRelationship.parse(source).map_err(|problems| Upstream(problems))?
			limits({ elements: d.entities.len(), rows: d.relationships.len() + d.entities.fold(0.U64, |count, entity| count + entity.attributes.len()) })?
		}
		"classDiagram" => {
			d = ClassDiagram.parse(source).map_err(|problems| Upstream(problems))?
			limits({ elements: d.classes.len(), rows: d.relationships.len() + d.classes.fold(0.U64, |count, class| count + class.fields.len() + class.methods.len() + class.annotations.len()) })?
		}
		"stateDiagram" | "stateDiagram-v2" => {
			d = StateDiagram.parse(source).map_err(|problems| Upstream(problems))?
			limits({ elements: d.states.len() + d.groups.len() + d.notes.len(), rows: d.transitions.len() + d.notes.len() + d.states.fold(0.U64, |count, state| count + state.descriptions.len()) + d.groups.fold(0.U64, |count, group| count + group.descriptions.len()) })?
			containers(d.groups.map(|group| group.container))?
		}
		_ => {
			d = match Flowchart.parse(source) {
				Ok(parsed) => parsed
				Err(problems) => return Err(Upstream(problems))
			}
			limits({ elements: d.nodes.len() + d.groups.len(), rows: d.edges.len() })?
			containers(d.groups.map(|group| group.container))?
		}
	}
	# Parse and bound the appropriate family before entering layout.
	picture = Mermaid.draw(source).map_err(|problems| Upstream(problems))?
	validate_picture(picture)?
	Ok(picture)
}

validate_picture : Drawing.Picture -> Try({}, DiagramAdapter.Problem)
validate_picture = |picture| {
	if !Drawing.is_finite(picture) or picture.items.is_empty() or !is_coordinate(picture.view.x) or !is_coordinate(picture.view.y) or !is_size(picture.view.width) or !is_size(picture.view.height) or picture.items.len() > max_items or !picture.items.all(is_item_bounded) {
		return Err(InvalidPicture)
	}
	if picture.items.fold(
		0.U64,
		|count, item| count + (
			match item {
				Shape(shape) => shape.path.len()
				_ => 1
			}
		),
	) > max_geometry {
		return Err(GeometryLimit)
	}
	Ok({})
}

is_coordinate : F64 -> Bool
is_coordinate = |value| F64.is_finite(value) and value >= -max_coordinate and value <= max_coordinate

is_point_bounded : Drawing.Point -> Bool
is_point_bounded = |point| is_coordinate(point.x) and is_coordinate(point.y)

is_size : F64 -> Bool
is_size = |value| F64.is_finite(value) and value > 0 and value <= max_view_size

is_label_bounded : Drawing.Label -> Bool
is_label_bounded = |label| is_point_bounded(label.origin) and is_size(label.size) and label.text.count_utf8_bytes().to_f64() * label.size <= max_coordinate

is_item_bounded : Drawing.Item -> Bool
is_item_bounded = |item| match item {
	Text(label) => is_label_bounded(label)
	StyledText(styled) => is_label_bounded(styled.label)
	Shape(shape) => {
		stroke_ok = match shape.paint.stroke {
			NoStroke => True
			Stroke(stroke) => is_size(stroke.width)
		}
		stroke_ok and shape.path.all(
			|segment| match segment {
				MoveTo(point) | LineTo(point) => is_point_bounded(point)
				CubicTo(curve) => is_point_bounded(curve.control_1) and is_point_bounded(curve.control_2) and is_point_bounded(curve.end)
				Close => True
			},
		)
	}
}

limits : { elements : U64, rows : U64 } -> Try({}, DiagramAdapter.Problem)
limits = |counts| if counts.elements > max_elements or counts.rows > max_rows {
	Err(ElementLimit)
} else Ok({})

containers : List(Flowchart.Container) -> Try({}, DiagramAdapter.Problem)
containers = |groups| {
	for container in groups {
		var $parent = container
		var $depth = 1.U64
		while $parent != Root {
			if $depth > max_nesting {
				return Err(NestingLimit)
			}
			$parent = match $parent {
				Root => Root
				Group(index) => groups.get(index).map_err(|_| InvalidContainer(index))?
			}
			$depth = $depth + 1
			if $depth > max_nesting {
				return Err(NestingLimit)
			}
		}
	}
	Ok({})
}

## Repeated bodies share one admitted cache entry and source-addressed path.
expect {
	first = DiagramAdapter.admit({ source: "flowchart LR\n A --> B\n", path: "one.adoc", ordinal: 1, cache: DiagramAdapter.empty_cache })?
	second = DiagramAdapter.admit({ source: "flowchart LR\n A --> B\n", path: "two.adoc", ordinal: 7, cache: first.cache })?
	{ cached: DiagramAdapter.diagrams(second.cache).len(), same_path: DiagramAdapter.href(first.diagram) == DiagramAdapter.href(second.diagram) } == { cached: 1, same_path: True }
}

## UTF-8 admission counts bytes before consulting the cache.
expect match DiagramAdapter.admit({ source: Str.join_with(List.repeat("é", 32769), ""), path: "large.md", ordinal: 2, cache: DiagramAdapter.empty_cache }) {
	Err(error) => error == { path: "large.md", ordinal: 2, problem: SourceTooLarge }
	Ok(_) => False
}

## Upstream diagnostic tags retain their document and diagram context.
expect match DiagramAdapter.admit({ source: "pie\n A: 1\n", path: "bad.md", ordinal: 3, cache: DiagramAdapter.empty_cache }) {
	Err({ path: "bad.md", ordinal: 3, problem: Upstream(problems) }) => !problems.is_empty()
	_ => False
}

## Invalid parent references reject rather than turning into root groups.
expect containers([Group(4)]) == Err(InvalidContainer(4))

## Cyclic parents terminate at the nesting bound.
expect containers([Group(0)]) == Err(NestingLimit)

## Element and row limits admit their exact boundary.
expect {
	limits({ elements: 256, rows: 1024 })?
	True
}

## Too many graph elements reject before layout.
expect limits({ elements: 257, rows: 0 }) == Err(ElementLimit)

## Too many relationship or member rows reject before layout.
expect limits({ elements: 0, rows: 1025 }) == Err(ElementLimit)

## Nonfinite views reject before conversion.
expect validate_picture({ view: { x: F64.nan, y: 0, width: 10, height: 10 }, items: [Text({ text: "valid", origin: { x: 0, y: 0 }, size: 12, align: Start, color: { red: 0, green: 0, blue: 0 } })] }) == Err(InvalidPicture)

## Finite coordinates too large for safe downstream arithmetic reject.
expect validate_picture({ view: { x: 0, y: 0, width: 10, height: 10 }, items: [Text({ text: "bad", origin: { x: 1e100, y: 0 }, size: 12, align: Start, color: { red: 0, green: 0, blue: 0 } })] }) == Err(InvalidPicture)

## Cubic controls and stroke widths share the bounded geometry gate.
expect validate_picture({ view: { x: 0, y: 0, width: 10, height: 10 }, items: [Shape({ path: [MoveTo({ x: 0, y: 0 }), CubicTo({ control_1: { x: 1e100, y: 0 }, control_2: { x: 0, y: 0 }, end: { x: 1, y: 1 } })], paint: { fill: NoFill, stroke: Stroke({ color: { red: 0, green: 0, blue: 0 }, width: 1 }) } })] }) == Err(InvalidPicture)

## Labels with unsafe derived underline extents reject despite finite inputs.
expect validate_picture({ view: { x: 0, y: 0, width: 10, height: 10 }, items: [Text({ text: Str.join_with(List.repeat("x", 65536), ""), origin: { x: 0, y: 0 }, size: 32768, align: Start, color: { red: 0, green: 0, blue: 0 } })] }) == Err(InvalidPicture)

## Nonfinite label sizes reject before conversion.
expect validate_picture({ view: { x: 0, y: 0, width: 10, height: 10 }, items: [Text({ text: "bad", origin: { x: 0, y: 0 }, size: F64.infinity, align: Start, color: { red: 0, green: 0, blue: 0 } })] }) == Err(InvalidPicture)

## Oversized views reject even when the picture has a valid text item.
expect validate_picture({ view: { x: 0, y: 0, width: 32769, height: 10 }, items: [Text({ text: "bad", origin: { x: 0, y: 0 }, size: 12, align: Start, color: { red: 0, green: 0, blue: 0 } })] }) == Err(InvalidPicture)
