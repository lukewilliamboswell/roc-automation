import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('verify_prepared',Path(__file__).with_name('verify_prepared.py'))
verify=importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.prepared=self.root/'prepared'
        self.site=self.root/'site'
        self.prepared.mkdir()
        self.site.mkdir()
        (self.prepared/'index.md').write_text('# Title {#title}\n\n```roc\nx = 1\n```\n')
        (self.prepared/'manifest.json').write_text(json.dumps({'schema_version':1,'pages':[{'input':'index.md','output':'index.html'}],'manual':{'input':'index.md','output':'manual.pdf'},'assets':[],'coverage':{'code_blocks':[{'sha256':verify.digest(b'x = 1\n')}],'tables':[]}}))
        self.html='<h1 id="title">Title</h1><pre><code>x = 1\n</code></pre><a href="#title">Title</a>'
        (self.site/'index.html').write_text(self.html)

    def test_chapter_furniture_does_not_interrupt_code_across_pages(self):
        metadata = {'title':'Manual','version':'3','author':'Authors'}
        text = 'Chapter\n\nvalue = 1\nAuthors 1 / 2\n\fChapter\nnext = 2\nChapter\nAuthors 2 / 2\n'
        self.assertEqual(verify.compact_pdf_text(text, metadata, ['Chapter']), 'value=1next=2Chapter')

    def test_reader_text_removes_only_edge_furniture(self):
        metadata={'title':'Manual','author':'Contributors','version':'3'}
        text='Manual   3\nvalue = 1\nContributors  1 / 2\fManual  3\nnext = 2\nContributors  2 / 2\f'
        self.assertEqual(verify.compact_pdf_text(text, metadata), 'value=1next=2')

    def test_schema_three_vector_identity_is_required(self):
        manifest_path = self.prepared/'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['schema_version'] = 3
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><style>data:font/ttf;base64,AAAA</style></svg>'
        (self.site/'diagram.svg').write_bytes(svg)
        manifest['assets'] = [{'kind':'vector','href':'diagram.svg','svg_sha256':verify.digest(svg)}]
        manifest_path.write_text(json.dumps(manifest))
        verify.check(self.prepared,self.site)
        (self.site/'diagram.svg').write_bytes(svg.replace(b'AAAA',b'BBBB'))
        with self.assertRaisesRegex(ValueError,'SVG bytes changed'):
            verify.check(self.prepared,self.site)
        del manifest['assets'][0]['svg_sha256']
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaises(KeyError):
            verify.check(self.prepared,self.site)

    def test_reference_schema_still_requires_original_coverage(self):
        manifest_path = self.prepared/'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        del manifest['coverage']
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaises(KeyError):
            verify.check(self.prepared,self.site)

    def test_actual_inventory_passes(self):
        self.assertEqual(verify.check(self.prepared,self.site)['code_blocks'],1)

    def test_changed_rendered_source_bytes_reject(self):
        (self.site/'index.html').write_text(self.html.replace('x = 1','x = 2'))
        with self.assertRaisesRegex(ValueError,'Source bytes'):
            verify.check(self.prepared,self.site)

    def test_missing_anchor_rejects(self):
        (self.site/'index.html').write_text(self.html.replace('href="#title"','href="#missing"'))
        with self.assertRaisesRegex(ValueError,'Unresolved anchor'):
            verify.check(self.prepared,self.site)

    def test_skip_links_and_legacy_nonheading_anchors_resolve(self):
        (self.site/'index.html').write_text('<a href="#content">Skip</a><main id="content">'+self.html+'<span id="legacy"></span><a href="#legacy">Old link</a></main>')
        self.assertEqual(verify.check(self.prepared,self.site)['headings'],1)

    def test_chrome_cannot_reuse_a_heading_id(self):
        (self.site/'index.html').write_text(self.html+'<main id="title"></main>')
        with self.assertRaisesRegex(ValueError,'duplicate'):
            verify.check(self.prepared,self.site)

    def test_heading_anchor_cannot_move_to_chrome(self):
        (self.site/'index.html').write_text(self.html.replace('<h1 id="title">','<h1 id="other">')+'<main id="title"></main>')
        with self.assertRaisesRegex(ValueError,'Explicit anchors'):
            verify.check(self.prepared,self.site)

    def test_missing_published_asset_rejects(self):
        (self.site/'index.html').write_text(self.html+'<img src="missing.png">')
        (self.prepared/'index.md').write_text((self.prepared/'index.md').read_text()+'\n![Missing](missing.png)\n')
        with self.assertRaisesRegex(ValueError,'Missing HTML image'):
            verify.check(self.prepared,self.site)

    def markdown_publication(self):
        (self.site/'index.md').write_text('<a id="title"></a>\n# Title\n\n```roc\nx = 1\n```\n\n[Title](index.md#title)\n')
        (self.site/'llms.txt').write_text('# Manual\n\n## Chapters\n- [Title](index.md)\n')
        (self.site/'index.html').write_text('<link rel="alternate" type="text/markdown" href="index.md"><link rel="describedby" href="llms.txt">'+self.html+'<a href="index.md">View Markdown</a>')

    def test_markdown_publication_parity_and_discovery(self):
        self.markdown_publication()
        self.assertEqual(verify.check(self.prepared,self.site,markdown=True)['markdown_pages'],1)

    def test_markdown_damage_cannot_pass_html_only_evidence(self):
        for before, after, error in [
            ('x = 1', 'x = 2', 'Markdown source bytes'),
            ('# Title', '# Other', 'Markdown heading'),
            ('<a id="title"></a>', '<a id="wrong"></a>', 'Markdown explicit anchors'),
            ('index.md#title', 'missing.md#title', 'Unresolved Markdown local link'),
            ('index.md#title', 'index.md#wrong', 'Unresolved Markdown anchor'),
            ('index.md#title', '../outside.md', 'Unresolved Markdown local link'),
        ]:
            with self.subTest(error=error):
                self.markdown_publication()
                path=self.site/'index.md'
                path.write_text(path.read_text().replace(before,after))
                with self.assertRaisesRegex(ValueError,error):
                    verify.check(self.prepared,self.site,markdown=True)

    def test_missing_markdown_discovery_or_index_is_rejected(self):
        for name, source, error in [
            ('index.html', self.html, 'Markdown discovery link'),
            ('index.html', '<link rel="alternate" type="text/markdown" href="index.md">'+self.html+'<a href="index.md">View Markdown</a>', 'Markdown discovery link'),
            ('llms.txt', '# Empty index\n', 'index omits chapters'),
            ('llms.txt', '# Index\n- [Title](index.md)\n- [Missing](missing.md)\n', 'index names missing file'),
        ]:
            with self.subTest(name=name):
                self.markdown_publication()
                (self.site/name).write_text(source)
                with self.assertRaisesRegex(ValueError,error):
                    verify.check(self.prepared,self.site,markdown=True)

    def test_markdown_literal_links_in_code_are_not_navigation(self):
        self.markdown_publication()
        path=self.site/'index.md'
        path.write_text(path.read_text()+'\nUse `[example](absent.md)` or `` `[other](missing.md)` `` as literal text.\n')
        path.write_text(path.read_text()+'\nAlso \\[example\\](absent.md) is escaped literal text.\n')
        self.assertEqual(verify.check(self.prepared,self.site,markdown=True)['markdown_pages'],1)

    def test_markdown_index_accepts_publication_base_path(self):
        self.markdown_publication()
        (self.site/'llms.txt').write_text('# Manual\n- [Title](https://example.com/project/index.md)\n')
        self.assertEqual(verify.check(self.prepared,self.site,markdown=True)['markdown_pages'],1)

    def test_markdown_prose_omission_is_rejected(self):
        self.markdown_publication()
        path=self.prepared/'index.md'
        path.write_text(path.read_text()+'\nThe job must never expose credentials.\n')
        published=self.site/'index.md'
        published.write_text(published.read_text()+'\nThe job must expose credentials.\n')
        with self.assertRaisesRegex(ValueError,'Markdown prose words omitted'):
            verify.check(self.prepared,self.site,markdown=True)

    def test_markdown_image_description_matches_admitted_html(self):
        self.markdown_publication()
        (self.site/'pixel.png').write_bytes(b'fixture')
        expected=self.prepared/'index.md'
        expected.write_text(expected.read_text()+'\n![Synthetic reference label](pixel.png)\n')
        html=self.site/'index.html'
        html.write_text(html.read_text()+'<img alt="Useful diagram description" src="pixel.png">')
        published=self.site/'index.md'
        original=published.read_text()
        published.write_text(original+'\n![Useful diagram description](pixel.png)\n')
        self.assertEqual(verify.check(self.prepared,self.site,markdown=True)['markdown_pages'],1)
        published.write_text(original+'\n![](pixel.png)\n')
        with self.assertRaisesRegex(ValueError,'Markdown image descriptions'):
            verify.check(self.prepared,self.site,markdown=True)

    def test_markdown_list_container_indent_is_not_part_of_source_bytes(self):
        source='- Example\n\n  ```roc\n  x = 1\n      indented = 2\n  ```\n'
        inventory=verify.markdown_inventory(source,nested_fences=True)
        self.assertEqual(inventory['code_texts'],['x = 1\n    indented = 2\n'])
        self.assertNotIn('x = 1',inventory['visible'])

    def test_markdown_fence_info_does_not_invalidate_code_delimiters(self):
        with self.assertRaisesRegex(ValueError,'Invalid Markdown backtick fence'):
            verify.markdown_inventory('```lang`info\nx = 1\n```\n')
        self.assertEqual(verify.markdown_inventory('~~~lang`info\nx = 1\n~~~\n')['code_texts'],['x = 1\n'])


if __name__=='__main__':
    unittest.main()
