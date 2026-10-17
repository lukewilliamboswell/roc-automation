"""Exercise the published repository builder with no tools on its PATH.

Python creates fixtures and verifies results; it is never launched by roc-docs.
"""
import hashlib
from html.parser import HTMLParser
import json
import os
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
        self.assertEqual((site/'index.html').read_bytes(), (site/'guide.html').read_bytes())
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
        self.assertEqual(manifest['schema_version'],2)
        self.assertEqual((prepared/manifest['manual']['input']).read_text(),'# Home\n')
        self.assertFalse((prepared/'manual.pdf').exists())
        before = (prepared/'manifest.json').read_bytes()
        self.run_build('--entrypoint','index.md',command='prepare',success=False)
        self.assertEqual((prepared/'manifest.json').read_bytes(),before)
    def test_local_prepared_diagram_uses_pinned_original_png(self):
        body = 'flowchart LR\n  First --> Last\n'
        self.source('index.adoc','= Home\n\n[mermaid]\n....\n'+body+'....\n')
        (self.docs/'diagram.png').write_bytes(png_bytes())
        registry = {'schema_version':1,'diagrams':[{'source_sha256':hashlib.sha256(body.encode()).hexdigest(),'href':'images/diagram.png','png':'diagram.png','png_sha256':hashlib.sha256(png_bytes()).hexdigest()}]}
        (self.docs/'prepared-diagrams.json').write_text(json.dumps(registry))
        self.run_build()
        self.assertEqual((self.root/'.docs-out/site/images/diagram.png').read_bytes(),png_bytes())
        registry['diagrams'][0]['png_sha256'] = '0'*64
        (self.docs/'prepared-diagrams.json').write_text(json.dumps(registry))
        before = (self.root/'.docs-out/manual.pdf').read_bytes(); self.run_build(success=False)
        self.assertEqual((self.root/'.docs-out/manual.pdf').read_bytes(),before)
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
