app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.26.0-rc2/Gj3zVDr3qrQa6aTh7Z1zgUfe9NeBE6R76T5ho3xrc8af.tar.zst",
	parser: "https://github.com/lukewilliamboswell/roc-parser/releases/download/3.0.0/6xsRdRqenJrAb4wG7HaST4Xh349vD5sHAJDsKTK6E3dr.tar.zst",
}
import Svg
import BundledDiagrams
import Preparation
import Html
import CodeHighlight
import SyntaxHighlight
import parser.Markdown
main! = |_| Ok({})
expect {
	source = "# Home {#home}\n\n## Chapter {#chapter}\n\n### Section {#section}\n"
	entries = Html.entries(Markdown.parse_str(source), True)
	entries.map(|e| e.number) == ["1. ", "1.1. "] and Html.toc(entries).contains("<ul><li><a href=\"#section\">")
}
expect {
	sources = ["on:\r\n  push: true # café\r\n", "run: |-\n  # shell content\n  echo \"hello\"\nnext: 42\n", "url: https://example.com/#part\n", "value: \"unfinished", "value: 'it''s café'\n", "x: [true, 1, null]\n"]
	sources.all(|source| Str.join_with(CodeHighlight.highlight("yaml", source).map(|span| SyntaxHighlight.text(source, span)), "") == source)
}
expect {
	spans = CodeHighlight.highlight("yaml", "on: true # comment\nvalue: 42\n")
	spans.any(|s| s.category == Name) and spans.any(|s| s.category == ReservedWord) and spans.any(|s| s.category == CommentText) and spans.any(|s| s.category == NumericLiteral)
}

expect {
	inputs = ["", "# café\r\n", "a='quoted # text' # comment\n", "a=12\nif true; then echo \"hello\"; fi\n", "{\"key\": false, \"x\": -12.5}\n", "@@ -1 +1 @@\n-old\n+new\n"]
	["sh", "json", "diff", "yml", "unknown"].all(|language| inputs.all(|source| Str.join_with(CodeHighlight.highlight(language, source).map(|span| SyntaxHighlight.text(source, span)), "") == source))
}
expect {
	code = "run: |-\n  # shell text\n  echo \"hello\"\nurl: https://example.com/#part\n"
	spans = CodeHighlight.highlight("yaml", code)
	!spans.any(|span| span.category == CommentText) and spans.any(|span| span.category == StringText and SyntaxHighlight.text(code, span).contains("# shell text"))
}
expect {
	code = "on: off\nyes: no\n"
	CodeHighlight.highlight("yaml", code).all(|span| span.category != ReservedWord)
}
expect {
	body = "<p><img src=\"a.png\" alt=\"one &gt; two\"></p>"
	result = Html.vector_images(body, [{ href: "a.png", svg_href: "a.svg" }])
	result == "<p><picture><source type=\"image/svg+xml\" srcset=\"a.svg\"><img src=\"a.png\" alt=\"one &gt; two\"></picture></p>"
}

expect BundledDiagrams.list.all(|d| Svg.validate(d.svg).is_ok())
expect {
	bad = ["<svg><script>bad()</script></svg>", "<svg onload='bad()'/>", "<svg><style>@import 'https://example.com/x';</style></svg>", "<svg><path fill='url(https://example.com/x)'/></svg>", "<svg><image href='https://example.com/x'/></svg>", "<?xml-stylesheet href='https://example.com/x'?><svg/>", "<svg><style>p{background:u\\rl(https://example.com/x)}</style></svg>"]
	bad.all(|source| Svg.validate(source.replace_each("<svg", "<svg xmlns='http://www.w3.org/2000/svg'").to_utf8()).is_err())
}
expect {
	files = [{ path: "index.adoc", source: "= Guide\n:sectnums:\n:toclevels: 2\n\n== Start\n\n.How to use it\n****\nTerm:: Definition with `code`.\n****\n\n[cols=\"1,3\",options=\"header\"]\n|===\n| A | B\n| one | two\n|===\n\nNOTE: Read this.\n" }]
	match Preparation.prepare(files, { entrypoint: "index.adoc", version: "1", pdf_filename: "manual.pdf", diagrams: [], site_url: "" }) {
		Ok({ pages: [{ html: Prepared(p), .. }], .. }) => p.numbered and p.toc_depth == 2 and p.body.contains("<aside class=\"sidebar") and p.body.contains("<dt>Term</dt><dd>") and p.body.contains("width:25.00%") and p.body.contains("width:75.00%") and p.body.contains("admonition")
		_ => False
	}
}

expect {
	files = [
		{ path: "index.adoc", source: "= Book\n\ninclude::a.adoc[leveloffset=+1]\n\ninclude::b.adoc[leveloffset=+1]\n" },
		{ path: "a.adoc", source: "= First\n\n== Shared\n\nxref:b.adoc#shared[Other chapter]\n" },
		{ path: "b.adoc", source: "= Second\n\n== Shared\n\n<<shared,Own section>>\n" },
	]
	match Preparation.prepare(files, { entrypoint: "index.adoc", version: "1", pdf_filename: "manual.pdf", diagrams: [], site_url: "" }) {
		Ok(result) => result.pages.all(
			|page| match page.html {
				Prepared(p) => if (page.route == "index.html") p.body.contains("href=\"#b-shared\">Other chapter") and p.body.contains("href=\"#b-shared\">Own section") else if (page.route == "a.html") p.body.contains("href=\"b.html#shared\">Other chapter") else p.body.contains("href=\"#shared\">Own section")
				_ => False
			},
		)
		_ => False
	}
}

expect Html.vector_images("<img src=\"a.png\" alt=\"A\">", [{ href: "a.png", svg_href: "images/a b,c#?.svg" }]).contains("srcset=\"images/a%20b%2Cc%23%3F.svg\"")

expect Svg.validate("<svg xmlns='http://www.w3.org/1999/xhtml'/>".to_utf8()).is_err()
expect Svg.validate("<svg/>".to_utf8()).is_err()

expect {
	files = [{ path: "index.adoc", source: "= Notes\n\nWARNING: Read this first.\n\n.Matrix\n[cols=\"1,1\",options=\"header\"]\n|===\n| A | B\n| one | two\n|===\n" }]
	match Preparation.prepare(files, { entrypoint: "index.adoc", version: "1", pdf_filename: "manual.pdf", diagrams: [], site_url: "" }) {
		Ok({ pages: [{ html: Prepared(p), .. }], .. }) => p.body.contains("class=\"admonition warning\"") and p.body.contains(">Warning</div>") and p.body.contains("<caption>Table 1. Matrix</caption>")
		_ => False
	}
}
