"""Run the released renderer without a compiler, packages or external tools.

Optional independent-reader checks use Poppler installed on the test host. The
renderer subprocess always has an empty tool PATH; Poppler is verification only.
"""
import hashlib
from html.parser import HTMLParser
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib

BINARY = str(Path(os.environ.get('ROC_DOCS_BINARY', '/tmp/roc-docs')).resolve())

class Page(HTMLParser):
    def __init__(self, text):
        super().__init__(); self.ids = []; self.links = []; self.code = []; self.pre = False; self.images = []; self.syntax = []; self.heading_ids = []
        self.feed(text)
    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if 'id' in attrs: self.ids.append(attrs['id'])
        if tag in ('h1','h2','h3','h4','h5','h6'): self.heading_ids.append(attrs.get('id'))
        if tag == 'a': self.links.append(attrs.get('href', ''))
        if tag == 'pre': self.pre = True; self.code.append('')
        if tag == 'img': self.images.append(attrs)
        if tag == 'span' and self.pre: self.syntax.append(attrs.get('class', ''))
    def handle_endtag(self, tag):
        if tag == 'pre': self.pre = False
    def handle_data(self, text):
        if self.pre: self.code[-1] += text

def png_bytes(width, height, pixels):
    def chunk(name, data):
        return struct.pack('>I', len(data)) + name + data + struct.pack('>I', zlib.crc32(name + data))
    rows = b''.join(b'\0' + pixels[n*width*3:(n+1)*width*3] for n in range(height))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b'')

class RendererTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root = Path(self.tmp.name)
    def run_doc(self, source, *options, name='out', expect=0):
        path = self.root / (name + '.md'); path.write_text(source)
        output = self.root / name
        result = subprocess.run([BINARY, 'render', str(path), str(output), *options], capture_output=True, text=True,
                                env={**os.environ, 'PATH': '/nonexistent'}, timeout=45)
        self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        return output, result
    def test_metadata_code_links_and_determinism(self):
        code = 'app [main!] {}\n\n# café\nmain! = |_args| {\n\techo!("hello <&>")\n}\n'
        source = '# Example {#home}\n\n[Local](#home) [Other](https://example.com/chapter.html#details)\n\n```roc\n' + code + '```\n'
        args = ('--title', 'Project <&> manual', '--author', 'Contributors', '--version', '2.7', '--language', 'en-AU', '--pdf-filename', 'project.pdf')
        one, _ = self.run_doc(source, *args, name='one'); two, _ = self.run_doc(source, *args, name='two')
        html = (one / 'index.html').read_text(); page = Page(html)
        self.assertEqual(page.code, [code]); self.assertIn('home', page.ids); self.assertIn('https://example.com/chapter.html#details', page.links)
        self.assertIn('project.pdf', page.links); self.assertIn('syntax-string', ' '.join(page.syntax)); self.assertIn('Project &lt;&amp;&gt; manual', html)
        self.assertEqual((one / 'project.pdf').read_bytes(), (two / 'project.pdf').read_bytes())
        self.assertTrue((one / 'project.pdf').read_bytes().startswith(b'%PDF-2.0'))
    def test_highlighted_languages_keep_prepared_source_text(self):
        snippets = {
            'yaml': 'on:\r\n  push: true # café\r\nrun: |-\r\n  # shell comment\r\n  echo "hi"\r\n',
            'yml': 'on: off\nurl: https://example.com/#part\nvalue: "unterminated\n',
            'sh': '# shell\nexport MODE="green"\nif true; then echo "$MODE"; fi\n',
            'json': '{"key": false, "value": 42}\n',
            'diff': '@@ -1 +1 @@\n-old\n+new\n',
        }
        for language, code in snippets.items():
            with self.subTest(language=language):
                output, _ = self.run_doc('# Sources\n\n```' + language + '\n' + code + '```\n', '--html-only', name=language)
                page = Page((output/'index.html').read_bytes().decode())
                # CommonMark normalizes CRLF before handing code to the lexer;
                # the highlighter must preserve that prepared text exactly.
                self.assertEqual(page.code, [code.replace('\r\n','\n')]); self.assertTrue(page.syntax)
    def test_chrome_ids_do_not_collide_with_authored_headings(self):
        output, _ = self.run_doc('# Content {#content}\n\n## TOC {#toc}\n', '--html-only')
        page = Page((output/'index.html').read_text())
        self.assertEqual(len(page.ids), len(set(page.ids)))
        self.assertIn('content', page.ids); self.assertIn('toc', page.ids)
        self.assertIn('#content-navigation', page.links)
    def test_reading_fonts_and_notices_ship_with_standalone_html(self):
        output, _ = self.run_doc('# Reading fonts\n\nA **bold** and *italic* paragraph.\n', '--html-only')
        fonts = output/'_roc-docs/fonts'
        for name in ['SpaceGrotesk-Regular.ttf','SpaceGrotesk-Bold.ttf','PlusJakartaSans-Regular.ttf','PlusJakartaSans-Bold.ttf','PlusJakartaSans-Italic.ttf','PlusJakartaSans-BoldItalic.ttf']:
            font = (fonts/name).read_bytes()
            self.assertEqual(font[:4], b'\x00\x01\x00\x00'); self.assertGreater(len(font), 32000)
        for name in ['SPACE-GROTESK-OFL.txt','PLUS-JAKARTA-SANS-OFL.txt']:
            self.assertIn('SIL OPEN FONT LICENSE', (fonts/name).read_text())
        self.assertIn('prefers-color-scheme:dark', (output/'index.html').read_text())
    def test_unicode_and_duplicate_implicit_anchors(self):
        output, _ = self.run_doc('# Éléphant\n\n## Repeat\n\n## Repeat\n', '--html-only')
        self.assertEqual(Page((output / 'index.html').read_text()).heading_ids, ['Éléphant', 'repeat', 'repeat-2'])
    def test_duplicate_explicit_anchors_reject_before_publication(self):
        output, result = self.run_doc('# One {#same}\n\n## Two {#same}\n', '--html-only', expect=1)
        self.assertFalse(output.exists()); self.assertIn('DuplicateHeadingId', result.stderr)
    def test_runtime_asset_is_generic_and_copied_exactly(self):
        png = self.root / 'tiny.png'; rgb = self.root / 'tiny.rgb'; rgb.write_bytes(b'\xff\0\0'); png.write_bytes(png_bytes(1, 1, rgb.read_bytes()))
        output, _ = self.run_doc('# Images\n\n![Red square](images/red.png)\n', '--asset', 'images/red.png', str(png), str(rgb), '1', '1')
        self.assertEqual((output / 'images/red.png').read_bytes(), png.read_bytes())
        self.assertEqual(Page((output / 'index.html').read_text()).images[0]['alt'], 'Red square')
    def test_complex_lists_preserve_nested_source_and_figure(self):
        png = self.root / 'nested.png'; png.write_bytes(png_bytes(1, 1, b'\x12\x34\x56'))
        code = 'marker = "nested source"\n'
        source = '# Lists\n\n1. First\n\n   ```roc\n   ' + code + '   ```\n\n2. ![Pixel](pixel.png)\n'
        output, _ = self.run_doc(source, '--image', 'pixel.png', str(png))
        page = Page((output / 'index.html').read_text())
        self.assertEqual(page.code, [code]); self.assertEqual(len(page.images), 1)
        if shutil.which('pdftotext'):
            text = subprocess.check_output(['pdftotext', str(output / 'manual.pdf'), '-'], text=True)
            self.assertIn('nested source', text)
    def test_pdf_only_complex_task_list_rejects_without_dropping_task_state(self):
        output, _ = self.run_doc('# Tasks\n\n- [x] Item\n\n  ```text\n  code\n  ```\n', '--pdf-only', expect=1)
        self.assertFalse(output.exists())
    def test_native_png_asset_requires_no_rgb_preparation(self):
        png = self.root / 'native.png'; png.write_bytes(png_bytes(1, 1, b'\x12\x34\x56'))
        output, _ = self.run_doc('# Native image\n\n![Pixel](images/pixel.png)\n', '--image', 'images/pixel.png', str(png))
        self.assertEqual((output / 'images/pixel.png').read_bytes(), png.read_bytes())
        self.assertTrue((output / 'manual.pdf').is_file())
    def test_native_png_crc_rejects_before_publication(self):
        png = self.root / 'bad.png'; data = bytearray(png_bytes(1, 1, b'\x12\x34\x56')); data[-1] ^= 1; png.write_bytes(data)
        output, _ = self.run_doc('# Native image\n\n![Pixel](pixel.png)\n', '--image', 'pixel.png', str(png), expect=1)
        self.assertFalse(output.exists())
    def test_pdf_image_resolution_retains_original_website_bytes(self):
        png = self.root / 'wide.png'; rgb = self.root / 'wide.rgb'
        rgb.write_bytes(b'\xff\0\0' * (1200 * 10)); png.write_bytes(png_bytes(1200, 10, rgb.read_bytes()))
        output, _ = self.run_doc('# Images\n\n![Red strip](wide.png)\n', '--pdf-image-dpi', '72', '--asset', 'wide.png', str(png), str(rgb), '1200', '10')
        self.assertEqual((output / 'wide.png').read_bytes(), png.read_bytes())
        if shutil.which('pdfimages'):
            listing = subprocess.check_output(['pdfimages', '-list', str(output / 'manual.pdf')], text=True)
            self.assertRegex(listing, r'\b471\s+3\b')
    def test_asset_names_cannot_inject_html_attributes(self):
        png = self.root / 'tiny.png'; rgb = self.root / 'tiny.rgb'; rgb.write_bytes(b'\xff\0\0'); png.write_bytes(png_bytes(1, 1, rgb.read_bytes()))
        href = 'images/x" onerror="run.png'
        output, _ = self.run_doc('# Images\n\n![Red](<' + href + '>)\n', '--html-only', '--asset', href, str(png), str(rgb), '1', '1')
        image = Page((output / 'index.html').read_text()).images[0]
        self.assertEqual(image, {'src': href, 'alt': 'Red'})
    def test_nested_heading_ids_are_unique_and_explicit_duplicates_reject(self):
        output, _ = self.run_doc('# Repeat\n\n> ## Repeat\n', '--html-only', name='nested')
        self.assertEqual(Page((output / 'index.html').read_text()).heading_ids, ['repeat', 'repeat-2'])
        output, _ = self.run_doc('# One {#same}\n\n> ## Two {#same}\n', '--html-only', name='duplicate-nested', expect=1)
        self.assertFalse(output.exists())
    def test_deep_documents_reject_before_publication(self):
        output, _ = self.run_doc('> ' * 70 + 'Nested\n', '--html-only', expect=1)
        self.assertFalse(output.exists())
    def test_bundled_font_licenses_are_available_without_files(self):
        result = subprocess.run([BINARY, '--licenses'], capture_output=True, text=True, env={**os.environ, 'PATH': '/nonexistent'}, check=True)
        self.assertIn('SIL OPEN FONT LICENSE', result.stdout.upper()); self.assertIn('Noto', result.stdout); self.assertIn('Adobe', result.stdout)
    def test_unsupported_content_rejects_without_output(self):
        for name, source in [('raw', '# Header\n\n<script>alert(1)</script>\n'), ('image', '# Header\n\n![missing](no.png)\n'), ('link', '# Header\n\n[bad](javascript:alert)\n')]:
            with self.subTest(name=name):
                output, _ = self.run_doc(source, name=name, expect=1); self.assertFalse(output.exists())
    def test_existing_output_preserved(self):
        output = self.root / 'out'; output.mkdir(); (output / 'sentinel').write_text('keep')
        self.run_doc('# Header\n', expect=1)
        self.assertEqual((output / 'sentinel').read_text(), 'keep')
    def test_invalid_cli_and_asset_paths(self):
        for name, options in [('unknown', ['--wat','x']), ('dpi-low', ['--pdf-image-dpi','71']), ('dpi-high', ['--pdf-image-dpi','301']), ('dpi-bad', ['--pdf-image-dpi','no']), ('pdf', ['--pdf-filename','../bad.pdf']), ('asset', ['--asset','../bad.png','x','y','1','1']), ('mode', ['--pdf-only','--html-only'])]:
            with self.subTest(name=name):
                output, _ = self.run_doc('# Header\n', *options, name=name, expect=2); self.assertFalse(output.exists())
    def test_empty_and_minimal_documents(self):
        for name, content in [('empty', ''), ('minimal', 'A minimal paragraph.\n')]:
            output, _ = self.run_doc(content, name=name)
            self.assertTrue((output / 'manual.pdf').read_bytes().startswith(b'%PDF-2.0'))
    def test_output_modes(self):
        html, _ = self.run_doc('# Heading\n', '--html-only', name='html'); pdf, _ = self.run_doc('# Heading\n', '--pdf-only', name='pdf')
        self.assertTrue((html / 'index.html').is_file()); self.assertFalse((html / 'manual.pdf').exists())
        self.assertTrue((pdf / 'manual.pdf').is_file()); self.assertFalse((pdf / 'index.html').exists())
    @unittest.skipUnless(shutil.which('pdftotext') and shutil.which('pdfinfo') and shutil.which('pdftocairo'), 'Poppler optional independent reader')
    def test_pdf_highlighting_accepts_each_supported_language(self):
        snippets = {
            'yaml': 'on: true # comment\nport: 42\nname: "green string"\nrun: |\n  echo "multiline"\n',
            'yml': 'enabled: false\nname: "green string"\n',
            'sh': 'export MODE="green string"\n',
            'json': '{"name": "green string", "port": 42, "enabled": true}\n',
            'diff': '@@ -1 +1 @@\n-old\n+green string\n',
            'unknown-language': 'unhighlighted plain source\n',
        }
        for language, code in snippets.items():
            with self.subTest(language=language):
                output, _ = self.run_doc('# Highlight probe\n\n```' + language + '\n' + code + '```\n', '--pdf-only', name=language)
                pdf = output/'manual.pdf'
                text = subprocess.check_output(['pdftotext', '-layout', str(pdf), '-'], text=True)
                for line in code.splitlines():
                    self.assertIn(line.strip(), text)
                svg = self.root/(language + '.svg')
                subprocess.run(['pdftocairo', '-svg', '-f', '2', '-l', '2', str(pdf), str(svg)], check=True, capture_output=True)
                colours = [tuple(float(component) * 2.55 for component in match) for match in re.findall(r'rgb\(([\d.]+)%,\s*([\d.]+)%,\s*([\d.]+)%\)', svg.read_text())]
                green = any(all(abs(a-b) < 1 for a,b in zip(colour, (36,99,51))) for colour in colours)
                self.assertEqual(green, language != 'unknown-language', ('PDF string colour/fallback', language))
    @unittest.skipUnless(shutil.which('pdftotext') and shutil.which('pdfinfo') and shutil.which('pdftocairo'), 'Poppler optional independent reader')
    def test_independent_reader_pagination_tags_colours_and_source(self):
        code = 'main! = |_args| { if True 42 else echo!("green string") }\n' + ''.join(f'# Unique source line {n:03d}\n' for n in range(100))
        source = '# Reader probe\n\n```roc\n' + code + '```\n\n| Repeated heading | Value |\n| --- | --- |\n' + ''.join(f'| Row {n:03d} | Item {n:03d} |\n' for n in range(120))
        output, _ = self.run_doc(source)
        pdf = output / 'manual.pdf'
        text = subprocess.check_output(['pdftotext', '-layout', str(pdf), '-'], text=True)
        for n in range(100): self.assertEqual(text.count(f'Unique source line {n:03d}'), 1)
        self.assertIn('Row 119', text); self.assertGreater(text.count('Repeated heading'), 1)
        info = subprocess.check_output(['pdfinfo', str(pdf)], text=True); self.assertIn('Tagged:          yes', info)
        svg = self.root / 'source.svg'
        subprocess.run(['pdftocairo', '-svg', '-f', '2', '-l', '2', str(pdf), str(svg)], check=True, capture_output=True)
        colours = [tuple(float(component) * 2.55 for component in match) for match in re.findall(r'rgb\(([\d.]+)%,\s*([\d.]+)%,\s*([\d.]+)%\)', svg.read_text())]
        for expected in [(118, 57, 165), (36, 99, 51), (156, 61, 16), (52, 84, 164)]:
            self.assertTrue(any(all(abs(a - b) < 1 for a, b in zip(actual, expected)) for actual in colours), ('Missing PDF source colour', expected))
        structure = subprocess.run(['pdfinfo', '-struct-text', str(pdf)], capture_output=True, text=True, check=True).stdout
        self.assertIn('Code (', structure)

if __name__ == '__main__': unittest.main(verbosity=2)
