"""Preparation boundaries: semantic preservation and explicit rejected inputs."""
import importlib.util
import json
import re
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

MODULE = Path(__file__).resolve().parent / 'reference_prepare_docs.py'
spec = importlib.util.spec_from_file_location('prepare_docs', MODULE)
prep = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = prep
spec.loader.exec_module(prep)


def png(width=1, height=1, pixel=b'\x12\x34\x56', color=2):
    def chunk(kind, content):
        return struct.pack('>I', len(content)) + kind + content + struct.pack('>I', zlib.crc32(kind+content))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, color, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\0' + pixel)) + chunk(b'IEND', b'')


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.docs = self.root / 'docs'
        self.docs.mkdir()

    def write(self, name, content):
        path = self.docs / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def prepare(self):
        return prep.prepare_docs(self.root, output_directory='prepared', docs_version='3', pdf_filename='project-3.pdf')

    def test_checked_listing_include_preserves_source_independently(self):
        self.write('index.adoc', '= Manual\n\n[source,roc]\n----\ninclude::../example.roc[tag=sample,indent=0]\n----\n')
        source = '# tag::sample[]\n    message = "hello"  \n# end::sample[]\n'
        (self.root/'example.roc').write_text(source)
        manifest = self.prepare()
        self.assertIn('```roc\nmessage = "hello"  \n```', (self.root/'prepared/manual.md').read_text())
        self.assertIn('workspace:example.roc', manifest['coverage']['sources'])

    def test_native_markdown_sibling_symlink_rejected(self):
        self.write('index.md', '# Manual\n')
        outside = self.root / 'outside.md'; outside.write_text('# Outside\n')
        (self.docs / 'chapter.md').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, '[Ss]ymlink'):
            prep.prepare_docs(self.root, entrypoint='index.md', output_directory='prepared')
        self.assertFalse((self.root / 'prepared').exists())

    def test_literal_attributes_expand_and_unknown_attributes_reject(self):
        self.write('index.adoc', '= Manual\n:project: Roc\n\nThe {project} version is {docs-version}.\n')
        self.prepare()
        self.assertIn('The Roc version is 3.', (self.root / 'prepared/manual.md').read_text())
        self.write('index.adoc', '= Manual\n\nThe {unknown} value.\n')
        with self.assertRaisesRegex(ValueError, 'Unknown attribute'):
            prep.prepare_docs(self.root, output_directory='bad-prepared')

    def test_include_metadata_explicit_anchor_and_backend_separation(self):
        self.write('index.adoc', '= Manual\n:author: Author\n\nifdef::backend-html5[]\nlink:old.pdf[Download]\nendif::[]\n\ninclude::chapter.adoc[leveloffset=+1]\n')
        self.write('chapter.adoc', '[#chosen]\n= Chapter\n\n<<chosen,Here>>\n')
        manifest = self.prepare()
        pdf = (self.root/'prepared/manual.md').read_text()
        html = (self.root/'prepared/pages/index.md').read_text()
        self.assertNotIn('Download', pdf)
        self.assertIn('[Download](project-3.pdf)', html)
        self.assertIn('## Chapter {#chosen}', pdf)
        self.assertEqual(manifest['metadata'], {'title':'Manual', 'author':'Author', 'version':'3', 'language':'en'})
        self.assertEqual(len(manifest['coverage']['sources']), 2)

    def test_nested_source_blocks_are_exact(self):
        source = '= Literal\n\n[source,roc]\n----\n  x = "${not-expanded}"  \n----\n'
        self.write('index.adoc', '= Manual\n\n[source,asciidoc]\n------\n' + source + '------\n')
        manifest = self.prepare()
        markdown = (self.root/'prepared/manual.md').read_text()
        self.assertIn(source, markdown)
        self.assertEqual(manifest['coverage']['code_blocks'][0]['sha256'], prep.sha(source.encode()))
        self.assertEqual(len(manifest['coverage']['headings']), 1)

    def test_cross_page_reference_and_local_reference(self):
        self.write('index.adoc', '= Manual\n\ninclude::chapter.adoc[leveloffset=+1]\n')
        self.write('chapter.adoc', '= Chapter\n\nxref:index.adoc#manual[Home] and <<chapter>>\n')
        self.prepare()
        page = (self.root/'prepared/pages/chapter.md').read_text()
        self.assertIn('[Home](index.html#manual)', page)
        self.assertIn('[Chapter](#chapter)', page)

    def test_chapter_only_references_preserve_explicit_first_heading_and_routes(self):
        self.write('index.adoc', '= Manual\n\ninclude::releases.adoc[leveloffset=+1]\n\nxref:releases/3.adoc[]\n')
        self.write('releases.adoc', '[#history]\n= Releases\n\ninclude::releases/3.adoc[leveloffset=+1]\n')
        self.write('releases/3.adoc', '[#v3]\n= Version three\n\n== Details\n')
        self.write('usage.adoc', '= Usage\n\nxref:releases.adoc[History] and xref:releases/3.adoc[]\n')
        self.prepare()
        self.assertIn('[Version three](#v3)', (self.root/'prepared/manual.md').read_text())
        page = (self.root/'prepared/pages/usage.md').read_text()
        self.assertIn('[History](releases.html#history)', page)
        self.assertIn('[Version three](releases.html#v3)', page)
        self.write('usage.adoc', '= Usage\n\nxref:missing.adoc[]\n')
        with self.assertRaisesRegex(ValueError, 'Unknown anchor missing.adoc'):
            prep.prepare_docs(self.root, output_directory='bad-prepared')
        self.assertFalse((self.root/'bad-prepared').exists())

    def test_table_continuation_sidebar_and_description_text(self):
        self.write('index.adoc', '= Manual\n\n.Sidebar\n****\nTerm:: Definition\ncontinued\n****\n\n[cols="1,2",options="header"]\n|===\n| A | B\n| cell\n| words\n  continue\n|===\n')
        manifest = self.prepare()
        content = (self.root/'prepared/manual.md').read_text()
        self.assertIn('**Sidebar**', content)
        self.assertIn('- **Term**: Definition', content)
        self.assertIn('| cell | words continue |', content)
        self.assertEqual(manifest['coverage']['tables'][0]['column_weights'], '1,2')

    def test_image_decode_and_manifest_hashes(self):
        self.write('index.adoc', '= Manual\n\nimage::images/p.png[Pixel]\n')
        (self.docs/'images').mkdir()
        (self.docs/'images/p.png').write_bytes(png())
        manifest = self.prepare()
        asset = manifest['assets'][0]
        self.assertEqual((asset['width'], asset['height']), (1,1))
        self.assertEqual((self.root/'prepared'/asset['rgb']).read_bytes(), b'\x12\x34\x56')

    def test_unknown_mermaid_fails_without_outputs(self):
        self.write('index.adoc', '= Manual\n\n[mermaid]\n....\nflowchart LR\n A --> B\n....\n')
        with self.assertRaisesRegex(ValueError, 'Unprepared/stale'):
            self.prepare()
        self.assertFalse((self.root/'prepared').exists())

    def test_recursive_include_rejected(self):
        self.write('index.adoc', '= Manual\ninclude::index.adoc[]\n')
        with self.assertRaisesRegex(ValueError, 'cycle'):
            self.prepare()

    def test_include_escape_and_symlink_rejected(self):
        self.write('index.adoc', '= Manual\ninclude::../outside.adoc[]\n')
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            self.prepare()
        self.write('index.adoc', '= Manual\ninclude::link.adoc[]\n')
        (self.docs/'link.adoc').symlink_to(self.docs/'index.adoc')
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            self.prepare()

    def test_unsupported_structural_syntax_rejected(self):
        for source in ('[quote]\ntext', 'ifdef::other[]\ntext\nendif::[]', 'include::other.adoc[tags=section]'):
            with self.subTest(source=source):
                self.write('index.adoc', '= Manual\n\n' + source + '\n')
                with self.assertRaises(ValueError):
                    self.prepare()

    def test_missing_reference_rejected(self):
        self.write('index.adoc', '= Manual\n\n<<missing>>\n')
        with self.assertRaisesRegex(ValueError, 'Unknown anchor'):
            self.prepare()

    def test_duplicate_explicit_anchor_rejected(self):
        self.write('index.adoc', '[#same]\n= Manual\n\n[#same]\n== Next\n')
        with self.assertRaisesRegex(ValueError, 'Duplicate explicit'):
            self.prepare()

    def test_png_crc_truncation_and_excess_stream_rejected(self):
        valid = png()
        damaged = bytearray(valid)
        damaged[-1] ^= 1
        for data in (bytes(damaged), valid[:-1], valid+b'garbage', png(2,1)):
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    prep.decode_png(data)

    def test_rgba_composites_white(self):
        self.assertEqual(prep.decode_png(png(pixel=b'\0\0\0\0', color=6))[2], b'\xff\xff\xff')

    def test_markdown_input_and_image_preparation(self):
        self.write('index.md', '# Native manual\n\n![Pixel](pixel.png)\n')
        (self.docs/'pixel.png').write_bytes(png())
        self.write('usage.md', '# Usage\n\nExact prose.\n')
        manifest = prep.prepare_docs(self.root, entrypoint='index.md', output_directory='prepared')
        self.assertEqual(len(manifest['pages']), 2)
        self.assertEqual(len(manifest['assets']), 1)
        self.assertEqual(manifest['metadata']['title'], 'Native manual')
        self.assertEqual((self.root/'prepared/manual.md').read_bytes(), (self.docs/'index.md').read_bytes())

    def test_local_prepared_diagram_and_stale_source(self):
        body = 'flowchart LR\n  Custom --> Diagram\n'
        self.write('index.adoc', '= Manual\n\n[mermaid]\n....\n' + body + '....\n')
        data = png()
        (self.docs/'diagram.png').write_bytes(data)
        self.write('prepared-diagrams.json', json.dumps({'schema_version':1,'diagrams':[{'source_sha256':prep.sha(body.encode()),'href':'images/custom.png','png':'diagram.png','png_sha256':prep.sha(data)}]}))
        manifest = self.prepare()
        self.assertEqual(manifest['assets'][0]['href'], 'images/custom.png')
        self.write('index.adoc', '= Manual\n\n[mermaid]\n....\n' + body.replace('Custom', 'Changed') + '....\n')
        with self.assertRaisesRegex(ValueError, 'Unprepared/stale'):
            prep.prepare_docs(self.root, output_directory='other')

    def test_nested_chapter_routes_resolve_to_existing_top_level_pages(self):
        self.write('index.adoc', '= Manual\n\ninclude::releases.adoc[leveloffset=+1]\n')
        self.write('releases.adoc', '= Releases\n\ninclude::releases/3.adoc[leveloffset=+1]\n')
        self.write('releases/3.adoc', '= Version three\n')
        self.write('usage.adoc', '= Usage\n\nxref:releases/3.adoc#version-three[Release]\n')
        self.prepare()
        self.assertIn('[Release](releases.html#version-three)', (self.root/'prepared/pages/usage.md').read_text())

    def test_crlf_asciidoc_rejected_without_normalizing_code(self):
        (self.docs/'index.adoc').write_bytes(b'= Manual\r\n')
        with self.assertRaisesRegex(ValueError, 'LF source'):
            self.prepare()

    def test_generated_ids_preserve_existing_asciidoctor_urls(self):
        self.assertEqual(prep.slug("Start from the reader's knowledge"), 'start-from-the-readers-knowledge')
        self.assertEqual(prep.slug('Build with `--opt=dev`'), 'build-with-optdev')

    def test_conditional_source_block_is_retained_only_in_selected_backend(self):
        self.write('index.adoc', '= Manual\n\nifdef::backend-html5[]\n[source,roc]\n----\nsecret = 1\n----\nendif::[]\n')
        self.prepare()
        self.assertNotIn('secret', (self.root/'prepared/manual.md').read_text())
        self.assertIn('secret = 1', (self.root/'prepared/pages/index.md').read_text())

    def test_existing_output_untouched(self):
        self.write('index.adoc', '= Manual\n')
        (self.root/'prepared').mkdir()
        (self.root/'prepared/keep').write_text('old')
        with self.assertRaisesRegex(ValueError, 'new directory'):
            self.prepare()
        self.assertEqual((self.root/'prepared/keep').read_text(), 'old')


if __name__ == '__main__':
    unittest.main()
