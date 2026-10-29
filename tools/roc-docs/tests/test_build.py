"""Exercise the published repository builder with no tools on its PATH.

Python creates fixtures and verifies results; it is never launched by roc-docs.
"""
import hashlib
from html.parser import HTMLParser
import json
import os
import shutil
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

BINARY = str(Path(os.environ['ROC_DOCS_BINARY']).resolve())


def png_bytes():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\0\x12\x34\x56')) + chunk(b'IEND', b'')


class Html(HTMLParser):
    def __init__(self, source):
        super().__init__(); self.pre = False; self.code = []; self.ids = []; self.links = []
        self.feed(source)
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'pre': self.pre = True; self.code.append('')
        if 'id' in attrs: self.ids.append(attrs['id'])
        if tag == 'a': self.links.append(attrs.get('href', ''))
    def handle_endtag(self, tag):
        if tag == 'pre': self.pre = False
    def handle_data(self, data):
        if self.pre: self.code[-1] += data


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(); self.docs = self.root/'docs'; self.docs.mkdir()
    def source(self, name, text):
        path = self.docs/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text)
        return path
    def run_build(self, *args, command='build', success=True):
        result = subprocess.run([BINARY, command, '--workspace', str(self.root), *args],
            capture_output=True, text=True, env={**os.environ, 'PATH':'/nonexistent'}, timeout=90)
        if success: self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else: self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result
    def prior(self):
        output = self.root/'.docs-out'; output.mkdir(); (output/'keep').write_bytes(b'previous publication')
        return output
    def unchanged(self, output):
        self.assertEqual({str(p.relative_to(output)):p.read_bytes() for p in output.rglob('*') if p.is_file()}, {'keep':b'previous publication'})
    def test_checked_listing_keeps_selected_tabs_whitespace_and_quote_bytes(self):
        code = '\tvalue = "`code`\'s"  \n'
        original = '# tag::body[]\n' + code + '# end::body[]\n'
        self.source('sample.roc', original)
        for options, expected in [('tag=body', code), ('tag=body,indent=0', code[1:])]:
            with self.subTest(options=options):
                self.source('index.adoc', '= Manual\n\n[source,roc]\n----\ninclude::sample.roc[' + options + ']\n----\n')
                self.run_build('--docs-version', '3')
                site = self.root/'.docs-out/site'
                self.assertEqual(Html((site/'index.html').read_text()).code, [expected])
                self.assertIn(expected, (site/'index.md').read_text())
                self.assertTrue((self.root/'.docs-out/manual.pdf').read_bytes().startswith(b'%PDF-'))
                prepared = '.prepared-' + ('dedent' if 'indent' in options else 'raw')
                self.run_build('--output-directory', prepared, command='prepare')
                provenance = json.loads((self.root/prepared/'manifest.json').read_text())['includes']
                self.assertEqual(provenance[0]['source_sha256'], hashlib.sha256(original.encode()).hexdigest())
                self.assertEqual(provenance[0]['selected_sha256'], hashlib.sha256(expected.encode()).hexdigest())

    def test_literal_attribute_code_is_preserved_in_html_pdf_preparation_and_markdown(self):
        self.source('index.adoc', '= Guide\n\nLiteral `+{docs-version}+`; expanded `{docs-version}`.\n')
        self.run_build('--markdown', 'true', '--docs-version', '7')
        site = self.root/'.docs-out/site'
        self.assertIn('<code>{docs-version}</code>', (site/'index.html').read_text())
        self.assertIn('<code>7</code>', (site/'index.html').read_text())
        self.assertIn('` {docs-version} `', (site/'index.md').read_text())
        self.assertIn('` 7 `', (site/'index.md').read_text())
        self.run_build('--docs-version', '7', command='prepare')
        self.assertIn('Literal `{docs-version}`; expanded `7`.', (self.root/'.docs-prepared/manual.md').read_text())

    def test_static_markdown_publication_preserves_content_and_rewrites_only_links(self):
        code = 'main! = || {\n\techo!("usage.html#start café <&>")  \n}\n'
        self.source('index.adoc', '= Test manual\n:doctype: book\n\ninclude::usage.adoc[leveloffset=+1]\n')
        self.source('usage.adoc', '= Usage\n\n[#start]\n== Start\n\nxref:usage.adoc#start[Start] xref:extra.adoc#details[Extra] link:https://example.com/usage.html[External] link:downloads/reference.pdf[PDF]\n\nLiteral `usage.html#start`.\n\n[source,roc]\n----\n' + code + '----\n\n[cols="1,2",options="header"]\n|===\n|Key |Value\n|One |Two\n|===\n\nNOTE: Keep this warning.\n\nimage::pixel.png[Pixel]\n')
        self.source('extra.adoc', '= Extra\n\n[#details]\n== Details\n\nWebsite only.\n')
        (self.docs/'pixel.png').write_bytes(png_bytes())
        self.run_build('--markdown', 'true', '--docs-version', '7', '--site-url', 'https://example.com/project/')
        site = self.root/'.docs-out/site'
        markdown = (site/'usage.md').read_text()
        self.assertIn('Documentation version: 7', markdown)
        self.assertIn('[HTML page](https://example.com/project/usage.html)', markdown)
        self.assertIn('[Start](#start)', markdown)
        self.assertIn('[Extra](extra.md#details)', markdown)
        self.assertIn('[External](https://example.com/usage.html)', markdown)
        self.assertIn('[PDF](downloads/reference.pdf)', markdown)
        self.assertIn('usage.html#start café <&>', markdown)
        self.assertIn(code, markdown)
        self.assertIn('<a id="start"></a>', markdown)
        self.assertIn('## Start', markdown)
        self.assertNotIn('{#start}', markdown)
        self.assertIn('| Key | Value |', markdown)
        self.assertIn('| One | Two |', markdown)
        self.assertIn('Keep this warning.', markdown)
        self.assertIn('![Pixel](pixel.png)', markdown)
        html = (site/'usage.html').read_text()
        self.assertIn('<link rel="alternate" type="text/markdown" href="usage.md">', html)
        self.assertIn('<a href="usage.md">View Markdown</a>', html)
        self.assertIn('[Usage](https://example.com/project/usage.md)', (site/'llms.txt').read_text())
        self.assertEqual((site/'pixel.png').read_bytes(), png_bytes())

    def test_markdown_export_uses_unique_anchors_and_preserves_inline_code_cells(self):
        self.source('index.md', '# Guide\n\n## Same\n\nFirst.\n\n## Same\n\n[Second](#same-2)\n\nAll spaces: `   `.\n\n| Code | Value |\n| --- | --- |\n| `a\\|b` | `   ` |\n')
        self.run_build('--entrypoint', 'index.md', '--markdown', 'true')
        site = self.root/'.docs-out/site'
        markdown = (site/'index.md').read_text()
        self.assertEqual(markdown.count('<a id="same"></a>'), 1)
        self.assertEqual(markdown.count('<a id="same-2"></a>'), 1)
        self.assertIn('[Second](#same-2)', markdown)
        self.assertIn('All spaces: `   `.', markdown)
        self.assertIn('` a\\|b `', markdown)
        self.assertIn('| ` a\\|b ` | `   ` |', markdown)

    def test_markdown_export_chooses_valid_fence_for_backtick_info_and_grows_it(self):
        self.source('index.md', '# Fence\n\n~~~~ ~lang`info\nvalue = 1\n~~~\n~~~~\n')
        self.run_build('--entrypoint', 'index.md', '--markdown', 'true')
        markdown = (self.root/'.docs-out/site/index.md').read_text()
        self.assertIn('~~~~ ~lang`info\nvalue = 1\n~~~\n~~~~\n', markdown)
        self.assertNotIn('```lang`info', markdown)

    def test_markdown_defaults_on_and_explicit_false_removes_exports(self):
        self.source('index.md', '# Home\n')
        self.run_build('--entrypoint', 'index.md')
        site = self.root/'.docs-out/site'
        self.assertTrue((site/'index.md').is_file())
        self.assertTrue((site/'llms.txt').is_file())
        self.assertIn('View Markdown', (site/'index.html').read_text())
        before = (site/'index.html').read_bytes()
        self.run_build('--entrypoint', 'index.md', '--markdown', 'yes', success=False)
        self.assertEqual((site/'index.html').read_bytes(), before)
        self.run_build('--entrypoint', 'index.md', '--markdown', 'false')
        self.assertFalse((site/'index.md').exists())
        self.assertFalse((site/'llms.txt').exists())
        self.assertNotIn('View Markdown', (site/'index.html').read_text())
        self.run_build('--entrypoint', 'index.md')
        self.assertTrue((site/'index.md').is_file())
        self.assertTrue((site/'llms.txt').is_file())


    def test_markdown_entrypoint_alias_and_escaped_unicode_index_labels(self):
        self.source('guide.md', '# Café [Guide]\n\n```text\n[Other](other.html#details)\n```\n')
        self.source('other.md', '# Other\n\n## Details\n\n[Guide](guide.html) [Home](index.html)\n')
        self.run_build('--entrypoint', 'guide.md', '--markdown', 'true')
        site = self.root/'.docs-out/site'
        self.assertEqual((site/'index.md').read_bytes(), (site/'guide.md').read_bytes())
        self.assertIn('href="index.md">View Markdown', (site/'index.html').read_text())
        self.assertIn('<link rel="alternate" type="text/markdown" href="index.md">', (site/'index.html').read_text())
        self.assertIn('[Guide](guide.md)', (site/'other.md').read_text())
        self.assertIn('[Home](index.md)', (site/'other.md').read_text())
        self.assertIn('[Other](other.html#details)\n```', (site/'guide.md').read_text())
        self.assertIn('[Café \\[Guide\\]](guide.md)', (site/'llms.txt').read_text())
        self.assertIn('<a id="details"></a>', (site/'other.md').read_text())

    def test_markdown_destinations_support_spaces_parentheses_unicode_and_query(self):
        self.source('index.md', '# Guide\n')
        self.source('links.md', '# Links\n\n[Other](<other guide (é).html?mode=plain#details>)\n\n![Pixel](<pixel space (é).png>)\n')
        self.source('other guide (é).md', '# Other\n\n## Details\n')
        (self.docs/'pixel space (é).png').write_bytes(png_bytes())
        self.run_build('--entrypoint', 'index.md', '--markdown', 'true')
        site = self.root/'.docs-out/site'
        markdown = (site/'links.md').read_text()
        self.assertIn('[Other](other%20guide%20%28é%29.md?mode=plain#details)', markdown)
        self.assertIn('![Pixel](pixel%20space%20%28é%29.png)', markdown)
        self.assertTrue((site/'pixel space (é).png').exists())
        self.assertIn('[Other](other%20guide%20%28é%29.md)', (site/'llms.txt').read_text())
        self.assertIn('[HTML page](other%20guide%20%28é%29.html)', (site/'other guide (é).md').read_text())

    def test_markdown_publication_collisions_preserve_previous_output(self):
        self.source('guide.adoc', '= Guide\n')
        output = self.prior()
        for name in ['llms.txt', 'guide.md', 'index.md', 'guide.md/file.txt', 'index.md/file.txt']:
            with self.subTest(name=name):
                target = self.source(name, 'collision')
                try:
                    self.run_build('--entrypoint', 'guide.adoc', '--markdown', 'true', success=False)
                    self.unchanged(output)
                finally:
                    target.unlink()
                    if target.parent != self.docs: target.parent.rmdir()

    def test_roc_ray_tagged_listing_preserves_checked_source(self):
        fixture = Path(__file__).parent/'fixtures/compatibility/roc-ray-step1.roc.txt'
        original = fixture.read_text()
        self.source('tutorial/step1.roc', original)
        self.source('index.adoc', '= Game tutorial\n\n[source,roc]\n----\ninclude::tutorial/step1.roc[tag=body,indent=0]\n----\n')
        self.run_build()
        expected = original.split('# tag::body[]\n', 1)[1].split('# end::body[]', 1)[0]
        self.assertEqual(Html((self.root/'.docs-out/site/index.html').read_text()).code, [expected])
        self.run_build(command='prepare')
        provenance = json.loads((self.root/'.docs-prepared/manifest.json').read_text())['includes']
        self.assertEqual(provenance, [{'document':'index.adoc', 'line':5, 'path':'docs/tutorial/step1.roc', 'options':'tag=body,indent=0', 'source_sha256':hashlib.sha256(original.encode()).hexdigest(), 'selected_sha256':hashlib.sha256(expected.encode()).hexdigest()}])

    def test_external_checked_listing_and_rejections_preserve_publication(self):
        examples = self.root/'examples'; examples.mkdir()
        (examples/'value.expected').write_text('  expected value  \n')
        self.source('index.adoc', '= Examples\n\n[source,text]\n----\ninclude::../examples/value.expected[]\n----\n')
        self.run_build()
        self.assertEqual(Html((self.root/'.docs-out/site/index.html').read_text()).code, ['  expected value  \n'])
        before = (self.root/'.docs-out/manual.pdf').read_bytes()
        for target in ['../../outside', '../examples/value.expected[tag=missing]', '../examples/link.expected']:
            if target.endswith('link.expected'):
                (examples/'link.expected').symlink_to(examples/'value.expected')
            directive = target if '[' in target else target+'[]'
            self.source('index.adoc', '= Examples\n\n[source,text]\n----\ninclude::'+directive+'\n----\n')
            result = self.run_build(success=False)
            self.assertIn('index.adoc:5:', result.stdout+result.stderr)
            self.assertEqual((self.root/'.docs-out/manual.pdf').read_bytes(), before)

    def test_listing_cannot_overwrite_its_source_or_bypass_limits(self):
        self.source('index.adoc', '= Examples\n\n[source,text]\n----\ninclude::../examples/value.expected[]\n----\n')
        examples = self.root/'examples'; examples.mkdir()
        original = examples/'value.expected'; original.write_text('keep me\n')
        result = self.run_build('--output-directory', 'examples', success=False)
        self.assertIn('overlaps publication output', result.stdout+result.stderr)
        self.assertEqual(original.read_text(), 'keep me\n')
        original.write_bytes(b'x' * (1048576+1))
        result = self.run_build(success=False)
        self.assertIn('byte limit', result.stdout+result.stderr)
        original.write_text('# tag::body[]\nvalue\n# end::body[]\n# tag::body[]\nagain\n# end::body[]\n')
        self.source('index.adoc', '= Examples\n\n[source,text]\n----\ninclude::../examples/value.expected[tag=body]\n----\n')
        result = self.run_build(success=False)
        self.assertIn('duplicate or nested selected tag', result.stdout+result.stderr)

    def test_listing_include_count_is_bounded_before_publication(self):
        self.source('tiny.expected', 'value\n')
        self.source('index.adoc', '= Examples\n\n[source,text]\n----\n' + 'include::tiny.expected[]\n' * 1025 + '----\n')
        output = self.prior()
        result = self.run_build(success=False)
        self.assertIn('include count exceeds limit', result.stdout+result.stderr)
        self.unchanged(output)

    def test_publication_links_keep_api_and_download_identities(self):
        self.source('index.adoc', '= Manual\n\nlink:api/index.html[API] link:downloads/reference.pdf[Reference]\n')
        self.run_build('--site-url', 'https://example.com/project/')
        links = Html((self.root/'.docs-out/site/index.html').read_text()).links
        self.assertIn('api/index.html', links)
        self.assertIn('downloads/reference.pdf', links)
        self.run_build('--site-url', 'https://example.com/project/', command='prepare')
        prepared = (self.root/'.docs-prepared/manual.md').read_text()
        for url in ['https://example.com/project/api/index.html', 'https://example.com/project/downloads/reference.pdf']:
            self.assertIn(url, prepared)
        if shutil.which('pdfinfo'):
            urls = subprocess.check_output(['pdfinfo', '-url', str(self.root/'.docs-out/manual.pdf')], text=True)
            self.assertIn('https://example.com/project/api/index.html', urls)
            self.assertIn('https://example.com/project/downloads/reference.pdf', urls)

    def test_website_only_chapter_links_use_the_publication_url_in_pdf(self):
        self.source('index.adoc', '= Manual\n\nxref:extra.adoc#details[Extra]\n')
        self.source('extra.adoc', '= Extra\n\n[#details]\n== Details\n\nExtra chapter.\n')
        self.run_build('--site-url', 'https://example.com/project')
        self.assertIn('extra.html#details', Html((self.root/'.docs-out/site/index.html').read_text()).links)
        self.run_build('--site-url', 'https://example.com/project', command='prepare')
        self.assertIn('https://example.com/project/extra.html#details', (self.root/'.docs-prepared/manual.md').read_text())
        if shutil.which('pdfinfo'):
            urls = subprocess.check_output(['pdfinfo', '-url', str(self.root/'.docs-out/manual.pdf')], text=True)
            self.assertIn('https://example.com/project/extra.html#details', urls)

    def test_unresolved_inline_reference_reports_source_location(self):
        self.source('index.adoc', '= Manual\n\nA reference to <<missing>>.\n')
        output = self.prior()
        result = self.run_build(success=False)
        self.assertRegex(result.stdout+result.stderr, r'index\.adoc:3: inline:')
        self.unchanged(output)

    def test_roc_ray_wrapped_examples_table(self):
        fixture = Path(__file__).parent/'fixtures/compatibility/roc-ray-examples-table.adoc'
        self.source('index.adoc', fixture.read_text())
        self.run_build()
        html = (self.root/'.docs-out/site/index.html').read_text()
        self.assertIn('A polished minimal interactive scene', html)
        self.assertIn('hello_world', html)
        self.assertIn('https://github.com/lukewilliamboswell/roc-ray/blob/main/examples/hello_world/main.roc', html)

    def test_simple_table_infers_columns_and_zero_weights_reject(self):
        self.source('index.adoc', '= Table\n\n[options="header"]\n|===\n| Key | Value\n| One | Two\n|===\n')
        self.run_build()
        html = (self.root/'.docs-out/site/index.html').read_text()
        self.assertIn('Key', html)
        self.assertIn('Two', html)
        self.source('index.adoc', '= Table\n\n[cols=",",options="header"]\n|===\n| Key | Value\n| One | Two\n|===\n')
        self.run_build()
        self.assertIn('Two', (self.root/'.docs-out/site/index.html').read_text())
        self.source('index.adoc', '= Table\n\n[cols="0,0",options="header"]\n|===\n| Key | Value\n| One | Two\n|===\n')
        result = self.run_build(success=False)
        self.assertIn('column weights must be from 1', result.stdout+result.stderr)

    def test_wrapped_psv_cell_separator_and_located_invalid_table(self):
        self.source('index.adoc', '= Tables\n\n[cols="1,2",options="header"]\n|===\n| Key | Value\n| One long\ncontinued key | Two\n|===\n')
        self.run_build()
        html = (self.root/'.docs-out/site/index.html').read_text()
        self.assertIn('One long continued key', html)
        self.assertIn('Two', html)
        self.source('index.adoc', '= Tables\n\n[cols="1,2",options="header"]\n|===\n| A | B | extra\n|===\n')
        result = self.run_build(success=False)
        self.assertRegex(result.stdout+result.stderr, r'index\.adoc:\d+: table:')

    def test_ascii_manual_includes_routes_links_tables_and_exact_source(self):
        self.source('index.adoc', '= Test manual\n:doctype: book\n\ninclude::usage.adoc[leveloffset=+1]\n')
        code = 'main! = |_args| {\n\techo!("café <&>")  \n}\n'
        self.source('usage.adoc', '= Usage\n\n[#start]\n== Start\n\nVersion {docs-version}.\n\n[source,roc]\n----\n' + code + '----\n\n[cols="1,2",options="header"]\n|===\n|Key |Value\n|One |Two\n|===\n\nxref:usage.adoc#start[Start]\n')
        self.run_build('--docs-version','7', '--title','Project <&>', '--author','Contributors')
        output = self.root/'.docs-out'; site = output/'site'
        for name in ('index.html','usage.html'): self.assertEqual(Html((site/name).read_text()).code, [code])
        self.assertIn('<table>', (site/'usage.html').read_text())
        self.assertIn('Version 7', (site/'usage.html').read_text())
        self.assertTrue((output/'manual.pdf').read_bytes().startswith(b'%PDF-2.0'))
        self.assertEqual((output/'manual.pdf').read_bytes(), (site/'manual.pdf').read_bytes())
    def test_asciidoc_semantics_numbered_toc_and_legacy_anchors(self):
        self.source('index.adoc', '= Guide\n:sectnums:\n:toclevels: 3\n\n.How to use it\n****\nTerm:: Definition with `code`.\n****\n\ninclude::a.adoc[leveloffset=+1]\n\ninclude::b.adoc[leveloffset=+1]\n')
        self.source('a.adoc','= First\n\n== Reporting\n\n[cols="1,3",options="header"]\n|===\n| A | B\n| One | Two\n|===\n')
        self.source('b.adoc','= Second\n\n== Reporting\n\nNOTE: Important note.\n')
        self.run_build()
        html = (self.root/'.docs-out/site/index.html').read_text(); page = Html(html)
        self.assertIn('<aside class="sidebar',html); self.assertIn('<dt>Term</dt><dd>',html)
        self.assertIn('width:25.00%',html); self.assertIn('width:75.00%',html)
        self.assertIn('class="admonition',html); self.assertIn('reporting-2',page.ids)
        self.assertIn('1.1. ',html); self.assertIn('2.1. ',html)
        self.assertRegex(html,r'<li><a href="#first">1\. First</a><ul>')
        self.assertEqual(len(page.ids),len(set(page.ids)))
    def test_native_diagram_families_captions_nested_repeats_and_determinism(self):
        import base64
        import re
        import xml.etree.ElementTree as ET
        bodies = [
            'flowchart LR\n A[Start] --> B[Done]\n',
            'sequenceDiagram\n Alice->>Bob: Request\n Bob-->>Alice: Reply\n',
            'erDiagram\n CUSTOMER ||--o{ ORDER : places\n',
            'classDiagram\n class Worker {\n +run()*\n +count$\n }\n Worker <|-- Task\n',
            'stateDiagram-v2\n [*] --> Idle\n Idle --> Working : start\n Working --> [*]\n',
        ]
        previous = None
        for format in ['adoc', 'md']:
            with self.subTest(format=format):
                for path in self.docs.glob('*'): path.unlink()
                if format == 'adoc':
                    content = '= Diagrams\n\n' + ''.join('.A useful diagram\n[mermaid]\n....\n' + b + '....\n\nExplanation.\n\n' for b in bodies)
                    content += '[quote]\n____\n[mermaid]\n....\n' + bodies[0] + '....\n____\n'
                else:
                    content = '# Diagrams\n\n' + ''.join('```mermaid\n' + b + '```\n\nExplanation.\n\n' for b in bodies)
                    content += '> ```mermaid\n' + ''.join('> ' + line + '\n' for line in bodies[0].splitlines()) + '> ```\n'
                self.source('index.'+format, content)
                self.run_build('--entrypoint','index.'+format)
                site = self.root/'.docs-out/site'
                vectors = {f.name:f.read_bytes() for f in (site/'images').glob('*.svg')}
                self.assertEqual(len(vectors), 5)
                for body in bodies:
                    name = 'diagram-'+hashlib.sha256(body.encode()).hexdigest()+'.svg'
                    data = vectors[name]
                    root = ET.fromstring(data)
                    self.assertEqual(root.tag, '{http://www.w3.org/2000/svg}svg')
                    self.assertIn(b'data:font/ttf;base64,', data)
                    encoded = data.split(b'data:font/ttf;base64,',1)[1].split(b')',1)[0]
                    self.assertEqual(hashlib.sha256(base64.b64decode(encoded)).hexdigest(), '40d692fce188e4471e2b3cba937be967878f631ad3ebbbdcd587687c7ebe0c82')
                    known_fonts = {'40d692fce188e4471e2b3cba937be967878f631ad3ebbbdcd587687c7ebe0c82','288316099b1e0a47a4716d159098005eef7c0066921f34e3200393dbdb01947f','bbc051dd204b5019a1aa0bc0ae2aa8a05ab13e7a3f979fa357631dc7feb6833a','948405a16cdc62701da5f4005ed068ca5f4d27061d98f7974ccfc37831d9581d'}
                    for embedded in re.findall(rb'data:font/ttf;base64,([A-Za-z0-9+/=]+)', data):
                        self.assertIn(hashlib.sha256(base64.b64decode(embedded)).hexdigest(),known_fonts)

                    notice = root.find('{http://www.w3.org/2000/svg}metadata').text
                    self.assertEqual(hashlib.sha256(notice.encode()).hexdigest(), '262481e844521b326f5ecd053e59b98c8b2da78c8ee1bdbb6e8174305e54935a')
                    self.assertIn('images/'+name, (site/'index.md').read_text())
                if previous is not None: self.assertEqual(vectors, previous)
                previous = vectors
                html = (site/'index.html').read_text()
                self.assertNotIn('<picture>', html)
                if format == 'adoc': self.assertIn('<figcaption>Figure 1. A useful diagram</figcaption>', html)
                self.assertIn('Explanation.', html)
                self.run_build('--entrypoint','index.'+format)
                self.assertEqual(vectors, {f.name:f.read_bytes() for f in (site/'images').glob('*.svg')})
                self.run_build('--entrypoint','index.'+format, command='prepare')
                manifest = json.loads((self.root/'.docs-prepared/manifest.json').read_text())
                self.assertEqual(manifest['schema_version'],3)
                self.assertEqual([a['kind'] for a in manifest['assets']], ['vector']*5)
                for asset in manifest['assets']:
                    self.assertEqual(hashlib.sha256((self.root/'.docs-prepared'/asset['svg']).read_bytes()).hexdigest(),asset['svg_sha256'])
                shutil.rmtree(self.root/'.docs-prepared')
                if shutil.which('pdftotext'):
                    text = subprocess.check_output(['pdftotext', str(site/'manual.pdf'), '-'], text=True)
                    for label in ['Start','Done','Request','Reply','CUSTOMER','ORDER','Worker','Task','Idle','Working']:
                        self.assertIn(label,text)
                    fonts = subprocess.check_output(['pdffonts',str(site/'manual.pdf')],text=True)
                    self.assertIn('Inter',fonts); self.assertIn('RocDocsSerif',fonts)
                    images = subprocess.check_output(['pdfimages','-list',str(site/'manual.pdf')],text=True)
                    self.assertEqual(len(images.splitlines()),2,images)
                    bounds = ET.fromstring(subprocess.check_output(['pdftotext','-bbox',str(site/'manual.pdf'),'-']))
                    ns = {'x':'http://www.w3.org/1999/xhtml'}
                    for page in bounds.findall('.//x:page',ns):
                        for word in page.findall('.//x:word',ns):
                            self.assertGreaterEqual(float(word.attrib['xMin']),0)
                            self.assertGreaterEqual(float(word.attrib['yMin']),0)
                            self.assertLessEqual(float(word.attrib['xMax']),float(page.attrib['width']))
                            self.assertLessEqual(float(word.attrib['yMax']),float(page.attrib['height']))

    def test_diagram_rejections_preserve_previous_publication(self):
        output = self.prior()
        cases = [
            ('malformed','flowchart LR\n A[unfinished\n'),
            ('unsupported','pie\n title Unsupported\n'),
            ('hostile','flowchart LR\n A["<script>alert(1)</script>"]\n'),
            ('empty','flowchart LR\n'),
            ('bytes','flowchart LR\n%%' + 'x'*65536+'\n'),
            ('elements','flowchart LR\n'+''.join(f'N{i}\n' for i in range(257))),
            ('events','sequenceDiagram\n'+ 'A->>B: Hi\n'*1025),
            ('members','classDiagram\n class Worker {\n'+' +field\n'*1025+' }\n'),
            ('attributes','erDiagram\n CUSTOMER {\n'+' string name\n'*1025+' }\n'),
            ('flow nesting','flowchart LR\n'+''.join(f'subgraph G{i}\n' for i in range(33))+'A\n'+'end\n'*33),
            ('state nesting','stateDiagram-v2\n'+''.join(f'state G{i} {{\n' for i in range(33))+'A\n'+'}\n'*33),
            ('nesting','sequenceDiagram\n'+ 'loop Again\n'*33+'A->>B: Hi\n'+'end\n'*33),
            ('huge','flowchart LR\n A[Hello]\nstyle A stroke-width:100000\n'),
        ]
        for name, body in cases:
            with self.subTest(name=name):
                self.source('index.md','# Reject\n\n```mermaid\n'+body+'```\n')
                result = self.run_build('--entrypoint','index.md', success=False)
                self.assertIn('index.md: diagram 1',result.stderr)
                if name in ('elements','events','members','attributes'):
                    self.assertIn('256 elements or 1024 relationships/events/member rows',result.stderr)
                elif 'nesting' in name:
                    self.assertIn('nesting exceeds 32',result.stderr)
                elif name == 'bytes':
                    self.assertIn('exceeds 64 KiB',result.stderr)
                elif name in ('empty','huge'):
                    self.assertIn('empty, nonfinite or oversized',result.stderr)
                self.unchanged(output)
                self.assertFalse((self.root/'.docs-out.roc-docs-stage').exists())

    def test_unique_diagram_limit_and_generated_asset_collision(self):
        output = self.prior()
        source = '# Many\n\n'+''.join(f'```mermaid\nflowchart LR\n N{i}\n```\n\n' for i in range(257))
        self.source('index.md',source)
        result = self.run_build('--entrypoint','index.md',success=False)
        self.assertIn('256 unique diagrams',result.stderr); self.unchanged(output)
        body = 'flowchart LR\n A --> B\n'
        self.source('index.md','# Collision\n\n```mermaid\n'+body+'```\n')
        self.source('images/diagram-'+hashlib.sha256(body.encode()).hexdigest()+'.svg','authored collision')
        self.run_build('--entrypoint','index.md',success=False); self.unchanged(output)

    def test_hostile_plain_label_is_escaped(self):
        import xml.etree.ElementTree as ET
        body = 'flowchart LR\n A["A #amp; B #lt; C #quot;quoted#quot;"] --> B[Done]\n'
        self.source('index.md','# Escaping\n\n```mermaid\n'+body+'```\n')
        self.run_build('--entrypoint','index.md')
        svg = next((self.root/'.docs-out/site/images').glob('*.svg')).read_bytes()
        root = ET.fromstring(svg)
        self.assertIn('A & B < C "quoted"', ' '.join(''.join(t.itertext()) for t in root.findall('{http://www.w3.org/2000/svg}text')))
        self.assertIn(b'&lt;',svg); self.assertNotIn(b'<script',svg)

    def test_markdown_images_static_files_and_successful_replacement(self):
        self.source('index.md', '# Home\n\n![Pixel](images/pixel.png)\n')
        image = self.docs/'images/pixel.png'; image.parent.mkdir(); image.write_bytes(png_bytes())
        (self.docs/'example.json').write_text('{"ok":true}')
        output = self.prior(); self.run_build('--entrypoint','index.md')
        self.assertFalse((output/'keep').exists())
        self.assertEqual((output/'site/images/pixel.png').read_bytes(), png_bytes())
        self.assertEqual((output/'site/example.json').read_text(), '{"ok":true}')
    def test_custom_entrypoint_supplies_index_when_no_index_source(self):
        self.source('guide.md', '# Guide\n')
        self.run_build('--entrypoint','guide.md')
        site = self.root/'.docs-out/site'
        self.assertIn('href="index.md"', (site/'index.html').read_text())
        self.assertIn('href="guide.md"', (site/'guide.html').read_text())
        self.assertEqual((site/'index.md').read_bytes(), (site/'guide.md').read_bytes())
    def test_github_outputs_are_absolute(self):
        self.source('index.md','# Home\n'); outputs = self.root/'outputs'
        result = subprocess.run([BINARY,'build','--workspace',str(self.root),'--entrypoint','index.md'], capture_output=True, text=True,
            env={**os.environ,'PATH':'/nonexistent','GITHUB_OUTPUT':str(outputs)}, timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn(f'site-directory={self.root}/.docs-out/site\n',outputs.read_text())
        self.assertIn(f'pdf-path={self.root}/.docs-out/manual.pdf\n',outputs.read_text())
    def test_prepare_materializes_without_rendering_or_overwriting(self):
        self.source('index.md','# Home\n')
        self.run_build('--entrypoint','index.md',command='prepare')
        prepared = self.root/'.docs-prepared'; manifest = json.loads((prepared/'manifest.json').read_text())
        self.assertEqual(manifest['schema_version'],3)
        self.assertEqual((prepared/manifest['manual']['input']).read_text(),'# Home\n')
        self.assertFalse((prepared/'manual.pdf').exists())
        before = (prepared/'manifest.json').read_bytes()
        self.run_build('--entrypoint','index.md',command='prepare',success=False)
        self.assertEqual((prepared/'manifest.json').read_bytes(),before)
    def test_obsolete_registry_rejects_with_migration_message(self):
        output = self.prior()
        self.source('index.adoc','= Home\n')
        for contents in ['{}','{"schema_version":1}','{"schema_version":2}','invalid']:
            self.source('prepared-diagrams.json',contents)
            result = self.run_build(success=False)
            self.assertIn('prepared-diagrams.json is obsolete',result.stderr)
            self.unchanged(output)

    def test_missing_include_cycle_raw_html_and_unknown_diagrams_preserve_output(self):
        output = self.prior()
        cases = ['= Home\n\ninclude::missing.adoc[]\n', '= Home\n\ninclude::index.adoc[]\n', '= Home\n\ninclude::../outside.adoc[]\n', '= Home\n\n[mermaid]\n....\nunknown source\n....\n']
        for text in cases:
            with self.subTest(source=text):
                self.source('index.adoc',text); self.run_build(success=False); self.unchanged(output)
        self.source('index.md','# Home\n\n<script>alert(1)</script>\n')
        self.run_build('--entrypoint','index.md',success=False); self.unchanged(output)
    def test_symlinks_in_source_or_output_ancestors_reject(self):
        self.source('index.md','# Home\n'); outside = self.root/'outside'; outside.mkdir()
        (outside/'secret.md').write_text('# Secret\n')
        (self.docs/'secret.md').symlink_to(outside/'secret.md')
        output = self.prior(); self.run_build('--entrypoint','index.md',success=False); self.unchanged(output)
        (self.docs/'secret.md').unlink(); (self.root/'linked').symlink_to(outside, target_is_directory=True)
        self.run_build('--entrypoint','index.md','--output-directory','linked/out',success=False)
        self.assertFalse((outside/'out').exists())
    def test_unsafe_output_overlap_and_stale_stage_preserve_previous(self):
        self.source('index.md','# Home\n'); output = self.prior()
        for path in ('docs','docs/out','.','../out','/tmp/out'):
            self.run_build('--entrypoint','index.md','--output-directory',path,success=False); self.unchanged(output)
        stage = self.root/'.docs-out.roc-docs-stage'; stage.mkdir(); (stage/'keep').write_text('interrupted')
        self.run_build('--entrypoint','index.md',success=False); self.unchanged(output)
        self.assertEqual((stage/'keep').read_text(),'interrupted')
    def test_corrupt_image_and_static_collision_leave_publication_unchanged(self):
        self.source('index.md','# Home\n\n![Pixel](pixel.png)\n'); (self.docs/'pixel.png').write_bytes(b'not a PNG')
        output = self.prior(); self.run_build('--entrypoint','index.md',success=False); self.unchanged(output)
        self.source('index.md','# Home\n'); (self.docs/'pixel.png').unlink(); (self.docs/'index.html').write_text('collision')
        self.run_build('--entrypoint','index.md',success=False); self.unchanged(output)
    def test_pdf_and_image_route_collisions_reject_before_publication(self):
        self.source('index.md','# Home\n'); output = self.prior()
        (self.docs/'manual.pdf').write_bytes(b'authored file')
        self.run_build('--entrypoint','index.md',success=False); self.unchanged(output)
        self.assertFalse((self.root/'.docs-out.roc-docs-stage').exists())
        (self.docs/'manual.pdf').unlink()
        self.source('index.md','# Home\n\n![Pixel](index.html)\n')
        (self.docs/'index.html').write_bytes(png_bytes())
        self.run_build('--entrypoint','index.md',success=False); self.unchanged(output)
        self.assertFalse((self.root/'.docs-out.roc-docs-stage').exists())
    def test_existing_output_symlink_is_rejected_without_touching_target(self):
        self.source('index.md','# Home\n'); output = self.prior()
        outside = self.root/'outside'; outside.write_bytes(b'outside bytes')
        (output/'link').symlink_to(outside)
        self.run_build('--entrypoint','index.md',success=False)
        self.assertTrue((output/'link').is_symlink())
        self.assertEqual(outside.read_bytes(),b'outside bytes')
        self.assertEqual((output/'keep').read_bytes(),b'previous publication')
    def test_optional_api_requires_explicit_tool_and_failed_generation_rolls_back(self):
        self.source('index.md','# Home\n'); (self.root/'main.roc').write_text('placeholder')
        output = self.prior()
        self.run_build('--entrypoint','index.md','--api-entrypoint','main.roc','--roc-command','missing-compiler',success=False)
        self.unchanged(output)
        compiler = self.root/'fake-roc'
        compiler.write_text(f'#!{sys.executable}\nimport pathlib,sys\npath=pathlib.Path(next(a[9:] for a in sys.argv if a.startswith("--output=")))\npath.mkdir(parents=True)\n(path/"index.html").write_text("API reference")\n')
        compiler.chmod(0o755)
        self.run_build('--entrypoint','index.md','--api-entrypoint','main.roc','--roc-command',str(compiler))
        self.assertEqual((output/'site/api/index.html').read_text(),'API reference')


if __name__ == '__main__': unittest.main()
