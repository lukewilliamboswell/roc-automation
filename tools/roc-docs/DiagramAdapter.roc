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
	Diagram : { source_sha256 : Str, href : Str, picture : Drawing.Picture }

	admit = |source, path, ordinal, cache| {
		context = "${path}: diagram ${ordinal.to_str()}"
		if source.to_utf8().len() > 65536 {
			return Err("${context}: Mermaid source exceeds 64 KiB")
		}
		digest = Sha256.hex(source.to_utf8())
		match cache.find_first(|d| d.source_sha256 == digest) {
			Ok(diagram) => return Ok({ diagram, cache })
			Err(_) => {}
		}
		if cache.len() >= 256 {
			return Err("${context}: more than 256 unique diagrams")
		}
		picture = checked_draw(source).map_err(|e| "${context}: ${e}")?
		diagram = { source_sha256: digest, href: "images/diagram-${digest}.svg", picture }
		Ok({ diagram, cache: cache.append(diagram) })
	}

	checked_draw = |source| {
		header = source.split_on("\n").map(Str.trim).find_first(|line| !line.is_empty() and !line.starts_with("%%")) ?? ""
		declaration = ((header.split_on("%%").first() ?? header).split_on(";").first() ?? header).trim()
		upstream = |e| Str.inspect(e)
		match declaration {
			"sequenceDiagram" => {
				d = Sequence.parse(source).map_err(upstream)?
				limits(d.participants.len(), d.events.len())?
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
					if $depth > 32 {
						return Err("Mermaid nesting exceeds 32")
					}
				}
			}
			"erDiagram" => {
				d = EntityRelationship.parse(source).map_err(upstream)?
				limits(d.entities.len(), d.relationships.len() + d.entities.fold(0.U64, |n, e| n + e.attributes.len()))?
			}
			"classDiagram" => {
				d = ClassDiagram.parse(source).map_err(upstream)?
				limits(d.classes.len(), d.relationships.len() + d.classes.fold(0.U64, |n, c| n + c.fields.len() + c.methods.len() + c.annotations.len()))?
			}
			"stateDiagram" | "stateDiagram-v2" => {
				d = StateDiagram.parse(source).map_err(upstream)?
				limits(d.states.len() + d.groups.len() + d.notes.len(), d.transitions.len() + d.notes.len() + d.states.fold(0.U64, |n, s| n + s.descriptions.len()) + d.groups.fold(0.U64, |n, g| n + g.descriptions.len()))?
				containers(d.groups.map(|g| g.container))?
			}
			_ => {
				d = match Flowchart.parse(source) {
					Ok(value) => value
					Err(e) => return Err(upstream(e))
				}
				limits(d.nodes.len() + d.groups.len(), d.edges.len())?
				containers(d.groups.map(|g| g.container))?
			}
		}
		# Each family is parsed and bounded before its layout entrypoint runs.
		picture = match Mermaid.draw(source) {
			Ok(value) => value
			Err(e) => return Err(upstream(e))
		}
		validate_picture(picture)?
		Ok(picture)
	}

	validate_picture : Drawing.Picture -> Try({}, Str)
	validate_picture = |picture| {
		if !Drawing.is_finite(picture) or picture.items.is_empty() or picture.view.width <= 0 or picture.view.height <= 0 or picture.view.width > 32768 or picture.view.height > 32768 or picture.items.len() > 65536 {
			return Err("empty, nonfinite or oversized Mermaid drawing")
		}
		if picture.items.fold(
			0.U64,
			|n, item| n + (
				match item {
					Shape(s) => s.path.len()
					_ => 1
				}
			),
		) > 262144 {
			return Err("Mermaid drawing exceeds geometry limit")
		}
		Ok({})
	}

	limits = |elements, rows| {
		if elements > 256 or rows > 1024 {
			Err("Mermaid exceeds 256 elements or 1024 relationships/events/member rows")
		} else Ok({})
	}

	containers = |groups| {
		for container in groups {
			var $parent = container
			var $depth = 1.U64
			while $parent != Root {
				if $depth > 32 {
					return Err("Mermaid nesting exceeds 32")
				}
				$parent = match $parent {
					Root => Root
					Group(i) => groups.get(i) ?? Root
				}
				$depth = $depth + 1
				if $depth > 32 {
					return Err("Mermaid nesting exceeds 32")
				}
			}
		}
		Ok({})
	}

	markdown : List(Markdown), Str, U64, List(Diagram), U64 -> Try({ blocks : List(Markdown), cache : List(Diagram), ordinal : U64 }, Str)
	markdown = |blocks, path, ordinal, cache, depth| {
		if depth > 32 {
			return Err("${path}: Markdown diagram nesting exceeds 32")
		}
		var $cache = cache
		var $ordinal = ordinal
		var $out = []
		for block in blocks {
			next = match block {
				Code({ info, pre }) if (info.split_on(" ").first() ?? "") == "mermaid" => {
					$ordinal = $ordinal + 1
					r = admit(pre, path, $ordinal, $cache)?
					$cache = r.cache
					Paragraph([Image({ alt: [Text("Diagram")], target: { href: r.diagram.href, title: Err(Missing) } })])
				}
				Blockquote(children) => {
					r = markdown(children, path, $ordinal, $cache, depth + 1)?
					$cache = r.cache
					$ordinal = r.ordinal
					Blockquote(r.blocks)
				}
				ListBlock({ kind, loose, items }) => {
					var $items = []
					for item in items {
						r = markdown(item.blocks, path, $ordinal, $cache, depth + 1)?
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
