import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'actions/nightly'))
import compiler_pins as pins

OLD = 'nightly-2026-09-05-b195f5b'
NEW = 'nightly-2026-09-06-d85e877'


class HeaderTests(unittest.TestCase):
    def test_all_root_kinds_and_body_decoys(self):
        sources = [f'app [main!] {{ pf: platform "p", roc: "{OLD}" }}\nmain! = |io| {{roc: "body"}}',
                   f'package [Thing] {{ roc: "{OLD}" }}\nThing := {{roc: Str}}',
                   f'platform "p" requires {{main!: {{}} => {{}}}} exposes [] packages {{ roc: "{OLD}" }} provides [main!]']
        for source in sources:
            with self.subTest(source=source):
                start, end, pin = pins.header_pin(source)
                self.assertEqual(pin, OLD)
                self.assertEqual(pins.replace_pin(source, NEW), source[:start] + NEW + source[end:])

    def test_comments_strings_and_nonroot_are_not_authority(self):
        source = f'# roc: "wrong"\napp [main!] {{ pf: platform "roc: text", # roc: "wrong"\nroc: "{OLD}" }}\nroc = "wrong"'
        self.assertEqual(pins.header_pin(source)[2], OLD)
        self.assertIsNone(pins.header_pin('Thing := {roc: Str}'))

    def test_duplicate_interpolated_and_malformed_pins_rejected(self):
        for source in (f'package [] {{roc: "{OLD}", roc: "{NEW}"}}',
                       'package [] {roc: "${version}"}', 'package [] {roc: platform "pin"}',
                       f'package [] {{roc: "{OLD}"'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                pins.header_pin(source)

    def test_mixed_selected_lanes_and_competing_legacy_rejected(self):
        for sources in ({'p.roc': f'package [] {{roc: "{OLD}"}}', 'q.roc': f'package [] {{roc: "{NEW}"}}'},
                        {'p.roc': f'package [] {{roc: "{OLD}"}}', '.roc-version': OLD}):
            with self.assertRaises(ValueError):
                pins.discover(sources)

    def test_roots_reject_escape_directories_and_duplicates(self):
        for roots in ([], ['../main.roc'], ['/main.roc'], ['package'], ['a.roc', 'a.roc'], ['.git/main.roc']):
            with self.subTest(roots=roots), self.assertRaises(ValueError):
                pins.validate_paths(roots)

    def test_release_header_spelling_matches_compiler_profile(self):
        for version in ('0.1.0', '0.1.0-rc1', '0.1.0-rc.1'):
            self.assertEqual(pins.header_pin(f'package [] {{roc: "{version}"}}')[2], version)
        with self.assertRaises(ValueError):
            pins.header_pin('package [] {roc: "v0.1.0"}')

class ManifestTests(unittest.TestCase):
    manifest = {'path': 'scripts/deps.json', 'key': 'roc_nightly'}

    def test_preserves_bytes_and_ignores_other_versions(self):
        source = '{\r\n "nested": {"roc_nightly": "body"}, "roc_stable": "'+OLD+'",\r\n "roc_nightly" : "'+OLD+'"\r\n}\r\n'
        found = pins.discover({'scripts/deps.json': source}, self.manifest)
        result = pins.replace(found, NEW)['scripts/deps.json']
        self.assertEqual(result, source.replace('"roc_nightly" : "'+OLD+'"', '"roc_nightly" : "'+NEW+'"'))

    def test_rejects_ambiguous_or_invalid_json(self):
        for source in ('[]', '{}', '{"roc_nightly": 1}', '{"roc_nightly":"latest"}',
                       '{"roc_nightly":"'+OLD+'","roc_nightly":"'+OLD+'"}',
                       '{"roc_nightly":"'+OLD+'","nested":{"x":0,"x":1}}',
                       '{"roc_nightly":"'+OLD+'","bad":NaN}',
                       '{"roc_nightly":"'+OLD.replace('n', '\\u006e', 1)+'"}'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                pins.discover({'scripts/deps.json': source}, self.manifest)

    def test_rejects_competing_authorities_and_unsafe_paths(self):
        with self.assertRaises(ValueError):
            pins.configured_paths({'compiler_manifest': self.manifest, 'compiler_roots': ['main.roc']})
        for path in ('../deps.json', '/deps.json', '.git/deps.json', 'deps.roc'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                pins.validate_manifest(dict(self.manifest, path=path))

    def test_rejects_symlinked_parent_even_inside_checkout(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'real').mkdir()
            (root / 'real/deps.json').write_text('{"roc_nightly":"'+OLD+'"}')
            (root / 'scripts').symlink_to(root / 'real', target_is_directory=True)
            with self.assertRaises(ValueError):
                pins.local_sources(root, manifest=self.manifest)
