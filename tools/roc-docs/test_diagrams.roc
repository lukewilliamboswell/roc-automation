app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.26.0-rc2/Gj3zVDr3qrQa6aTh7Z1zgUfe9NeBE6R76T5ho3xrc8af.tar.zst",
	font: "https://github.com/lukewilliamboswell/roc-font/releases/download/0.1.0-rc2/A14BmicBaf6dm4K9kdYH9j8Y6BoAZrpUmxWh9sojsFy5.tar.zst",
	mermaid: "https://github.com/lukewilliamboswell/roc-graph-layout/releases/download/0.3.0-rc3/mermaid-A9th2FTnD4nQrV1nkr9mMZFp6JkK4aNzt7Gi6HAGHWrr.tar.zst",
	parser: "https://github.com/lukewilliamboswell/roc-parser/releases/download/3.0.0/6xsRdRqenJrAb4wG7HaST4Xh349vD5sHAJDsKTK6E3dr.tar.zst",
	pdf: "https://github.com/lukewilliamboswell/roc-pdf/releases/download/0.2.0/2hTpvYoKFHy8C6VeEgiv4FgWkP4oNTVupc1XCN8UwdiZ.tar.zst",
}
import cli.OsStr
import DiagramAdapter
import DiagramSvg
import VectorDrawing

main! : List(OsStr) => Try({}, _)
main! = |_args| Ok({})

## Both serializers consume the same opaque admitted picture without another parser pass.
expect {
	admitted = DiagramAdapter.admit({ source: "flowchart LR\nA --> B\n", path: "docs/index.adoc", ordinal: 1, cache: DiagramAdapter.empty_cache })?
	picture = DiagramAdapter.picture(admitted.diagram)
	svg = Str.from_utf8(DiagramSvg.document(picture))?
	actual =
		\\PDF commands: ${Str.inspect(VectorDrawing.drawing(picture).command_count() > 0)}
		\\SVG font notice: ${Str.inspect(svg.contains("<metadata id=\"roc-docs-font-license\">"))}
		\\SVG font data: ${Str.inspect(svg.contains("data:font/ttf;base64,"))}
	expected =
		\\PDF commands: True
		\\SVG font notice: True
		\\SVG font data: True
	actual == expected
}
