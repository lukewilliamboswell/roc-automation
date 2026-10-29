app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.26.0-rc2/Gj3zVDr3qrQa6aTh7Z1zgUfe9NeBE6R76T5ho3xrc8af.tar.zst",
	font: "https://github.com/lukewilliamboswell/roc-font/releases/download/0.1.0-rc2/A14BmicBaf6dm4K9kdYH9j8Y6BoAZrpUmxWh9sojsFy5.tar.zst",
	mermaid: "https://github.com/lukewilliamboswell/roc-graph-layout/releases/download/0.3.0-rc3/mermaid-A9th2FTnD4nQrV1nkr9mMZFp6JkK4aNzt7Gi6HAGHWrr.tar.zst",
	parser: "https://github.com/lukewilliamboswell/roc-parser/releases/download/3.0.0/6xsRdRqenJrAb4wG7HaST4Xh349vD5sHAJDsKTK6E3dr.tar.zst",
	pdf: "https://github.com/lukewilliamboswell/roc-pdf/releases/download/0.2.0/2hTpvYoKFHy8C6VeEgiv4FgWkP4oNTVupc1XCN8UwdiZ.tar.zst",
}
import DiagramAdapter
import Preparation
import Sha256
main! = |_| Ok({})

options = { entrypoint: "index.adoc", version: "3", pdf_filename: "manual.pdf", site_url: "" }
expect Preparation.prepare([{ path: "index.adoc", source: "= Manual\n\nHello.\n" }], options).is_ok()

source = |s| [{ path: "index.adoc", source: s }]

manual = |s| Preparation.prepare(source(s), options).map_ok(|r| r.manual)
expect manual("= Manual\n\nHello *bold* and `code`.\n") == Ok("# Manual {#manual}\n\nHello **bold** and `code`.\n\n")
expect manual("= Manual\n\n[source,roc]\n----\n  x = 1  \n----\n").map_ok(|s| s.contains("```roc\n  x = 1  \n```")) == Ok(True)
expect manual("= Manual\n\nifdef::backend-html5[]\nOnly HTML.\nendif::[]\n").map_ok(|s| !s.contains("Only HTML")) == Ok(True)
expect Preparation.prepare(source("= Manual\n\nifdef::backend-html5[]\nOnly HTML.\nendif::[]\n"), options).map_ok(|r| r.pages.any(|p| p.source.contains("Only HTML"))) == Ok(True)
expect Preparation.prepare([{ path: "index.adoc", source: "= Manual\n\ninclude::part.adoc[leveloffset=+1]\n" }, { path: "part.adoc", source: "[#chosen]\n= Child\n\nText.\n" }], options).map_ok(|r| r.manual.contains("## Child {#chosen}")) == Ok(True)
expect Preparation.prepare(source("= Manual\n\ninclude::missing.adoc[]\n"), options).is_err()
expect Preparation.prepare(source("= Manual\n\ninclude::index.adoc[]\n"), options).is_err()
expect Preparation.prepare([{ path: "../bad.adoc", source: "= Bad" }], options).is_err()
expect manual("= Manual\n\n[#one]\n== First\n\n[#one]\n== Second\n").is_err()
expect manual("= Manual\n\n[mermaid]\n....\nflowchart LR\n A --> B\n....\n").is_ok()
expect manual("= Manual\n\n[cols=\"1,2\",options=\"header\"]\n|===\n| A | B\n| x | y\n|===\n").map_ok(|s| s.contains("| A | B |\n| --- | --- |\n| x | y |")) == Ok(True)
expect manual("= Manual\n\nTerm:: Definition\n").map_ok(|s| s.contains("- **Term**: Definition")) == Ok(True)
expect manual("= Manual\n\n* One\n* Two\n").map_ok(|s| s.contains("- One\n- Two")) == Ok(True)
expect manual("= Manual\n\n[quote]\n____\nText.\n____\n").map_ok(|s| s.contains("Text.")) == Ok(True)
expect manual("= Manual\n\n<<missing>>\n").is_err()
expect manual("= Manual\n\n== Child\n\n<<child>>\n").map_ok(|s| s.contains("[Child](#child)")) == Ok(True)
expect Preparation.prepare([{ path: "index.md", source: "# Native\n\nExact.\n" }], { ..options, entrypoint: "index.md" }).map_ok(|r| r.manual) == Ok("# Native\n\nExact.\n")
expect manual("= Manual\n\n[pass]\n++++\n<script>bad()</script>\n++++\n").is_err()

expect manual("= Manual\n\n`*` matches and `**/*.roc` includes files.\n").map_ok(|s| s.contains("`*` matches and `**/*.roc` includes files.")) == Ok(True)
expect Preparation.prepare([{ path: "index.adoc", source: "= Manual\n\ninclude::part.adoc[leveloffset=+1]\n" }, { path: "part.adoc", source: "[#chosen]\n= Child\n\nText.\n" }], options).map_ok(|r| r.pages.find_first(|p| p.route == "part.html").map_ok(|p| p.source.contains("# Child {#chosen}"))) == Ok(Ok(True))
expect Preparation.prepare([{ path: "index.md", source: "# Native\n\n![Pixel](images/a.png)\n" }], { ..options, entrypoint: "index.md" }).map_ok(|r| r.images) == Ok(["images/a.png"])
expect manual("= Manual\n\n[cols=\"1,2\"]\n|===\n| A | B\n|===\n").is_err()

expect manual("= Manual\r\n").is_err()
expect manual("= Manual\n\n[source,roc]\n----\nx = 1 \n----\n\n[source,roc]\n----\nx = 1  \n----\n").is_err()
expect manual("= Manual\n\n[source,asciidoc]\n------\n[source,roc]\n----\n  code = 1\n----\n------\n").map_ok(|s| s.contains("```asciidoc\n[source,roc]\n----\n  code = 1\n----\n```")) == Ok(True)

# Public AsciiDoc AST does not carry source paths: ambiguous included titles
# must reject rather than silently route links using the previous chapter.
expect Preparation.prepare(
	[
		{ path: "index.adoc", source: "= Manual\n\ninclude::a.adoc[leveloffset=+1]\n\ninclude::b.adoc[leveloffset=+1]\n" },
		{ path: "a.adoc", source: "= Shared title\n\nFirst.\n" },
		{ path: "b.adoc", source: "= Shared title\n\nSecond.\n" },
	],
	options,
).is_err()

expect manual("= Manual\n\n[cols=\"1,2\",options=\"header\"]\n|===\n| A | B\n| long\ncontinued | value\n|===\n").map_ok(|s| s.contains("| long continued | value |")) == Ok(True)
expect Preparation.prepare(source("= Manual\n\nlink:api/index.html[API]\n"), { ..options, site_url: "https://example.com/project/" }).map_ok(|r| r.manual.contains("https://example.com/project/api/index.html")) == Ok(True)

expect manual("= Manual\n\n[cols=\",,\",options=\"header\"]\n|===\n| A | B | C\n| x | y | z\n|===\n").map_ok(|s| s.contains("| A | B | C |")) == Ok(True)

expect manual("= Manual\n\nLiteral `+{docs-version}+`; expanded `{docs-version}`.\n").map_ok(|s| s.contains("Literal `{docs-version}`; expanded `3`.")) == Ok(True)

diagram_body = "flowchart LR\n  First --> Last\n"

diagram_prepared = Preparation.prepare(source("= Manual\n\n.A [useful] diagram\n[mermaid]\n....\n${diagram_body}....\n"), options)

diagram_text = diagram_prepared.map_ok(|r| r.manual)
expect diagram_text.map_ok(|s| s.contains("![A \\[useful\\] diagram](images/diagram-${Sha256.hex(diagram_body.to_utf8())}.svg)")) == Ok(True)
expect diagram_prepared.map_ok(|r| r.pages.any(|p| p.source.contains("![A \\[useful\\] diagram](images/diagram-${Sha256.hex(diagram_body.to_utf8())}.svg)"))) == Ok(True)

expect DiagramAdapter.validate_picture({ view: { x: F64.nan, y: 0, width: 10, height: 10 }, items: [] }).is_err()
expect DiagramAdapter.validate_picture({ view: { x: 0, y: 0, width: 10, height: 10 }, items: [Text({ text: "bad", origin: { x: 0, y: 0 }, size: F64.infinity, align: Start, color: { red: 0, green: 0, blue: 0 } })] }).is_err()
expect DiagramAdapter.validate_picture({ view: { x: 0, y: 0, width: 32769, height: 10 }, items: [] }).is_err()
