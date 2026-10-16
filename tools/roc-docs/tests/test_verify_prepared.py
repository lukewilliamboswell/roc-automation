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

    def test_missing_published_asset_rejects(self):
        (self.site/'index.html').write_text(self.html+'<img src="missing.png">')
        (self.prepared/'index.md').write_text((self.prepared/'index.md').read_text()+'\n![Missing](missing.png)\n')
        with self.assertRaisesRegex(ValueError,'Missing HTML image'):
            verify.check(self.prepared,self.site)


if __name__=='__main__':
    unittest.main()
