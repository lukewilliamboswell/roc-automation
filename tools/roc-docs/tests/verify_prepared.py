"""Check prepared Markdown against actual site output and optional tagged PDF.

Python standard library only. This verifies content inventories and links; it
is not a visual-layout test. --independent-reader additionally requires Poppler
and checks PDF code characters and heading destinations.
"""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess


def digest(data):
    return hashlib.sha256(data).hexdigest()


def markdown_inventory(source):
    blocks = []
    visible = []
    fence = None
    content = []
    for line in source.splitlines(keepends=True):
        bare = line.rstrip('\r\n')
        if fence:
            if re.fullmatch(re.escape(fence) + r'[ \t]*', bare):
                blocks.append(''.join(content))
                fence, content = None, []
            else:
                content.append(line)
        else:
            match = re.fullmatch(r'(`{3,}|~{3,})[^\n]*', bare)
            if match:
                fence = match[1]
            else:
                visible.append(line)
    if fence:
        raise ValueError('Unclosed prepared Markdown fence')
    text = ''.join(visible)
    return {
        'code': [digest(block.encode()) for block in blocks],
        'code_texts': blocks,
        'headings': re.findall(r'^#{1,6} (.+)$', text, re.M),
        'ids': re.findall(r'^#{1,6} .+ \{#([^}]+)\}$', text, re.M),
        'tables': len(re.findall(r'^\|[ \t]*:?-{3,}:?[ \t]*\|', text, re.M)),
        'images': len(re.findall(r'!\[[^\]]*\]\([^\n]+?\)', text)),
    }


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pre = False
        self.code = []
        self.ids = []
        self.heading_ids = []
        self.headings = 0
        self.tables = 0
        self.images = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'pre':
            self.pre = True
            self.code.append('')
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if tag in ('h1','h2','h3','h4','h5','h6'):
            self.headings += 1
            self.heading_ids.append(attrs.get('id'))
        if tag == 'table':
            self.tables += 1
        if tag == 'img':
            self.images.append(attrs.get('src', ''))
        if tag == 'a':
            self.links.append(attrs.get('href', ''))

    def handle_endtag(self, tag):
        if tag == 'pre':
            self.pre = False

    def handle_data(self, data):
        if self.pre:
            self.code[-1] += data


def check(prepared, site, pdf=None, independent_reader=False):
    prepared, site = Path(prepared), Path(site)
    manifest = json.loads((prepared/'manifest.json').read_text())
    if manifest['schema_version'] != 1:
        raise ValueError('Unknown preparation schema')
    parsed = {}
    totals = {'pages':0, 'code_blocks':0, 'tables':0, 'headings':0, 'images':0, 'links':0}
    for entry in manifest['pages']:
        inventory = markdown_inventory((prepared/entry['input']).read_text())
        page = Page()
        page.feed((site/entry['output']).read_text())
        if inventory['code'] != [digest(s.encode()) for s in page.code]:
            raise ValueError(f"Source bytes changed in {entry['output']}")
        if len(inventory['headings']) != page.headings or len(page.ids) != len(set(page.ids)):
            raise ValueError(f"Heading inventory changed/duplicate in {entry['output']}")
        if None in page.heading_ids or not set(inventory['ids']).issubset(page.heading_ids):
            raise ValueError(f"Explicit anchors changed in {entry['output']}")
        if inventory['tables'] != page.tables or inventory['images'] != len(page.images):
            raise ValueError(f"Table/image inventory changed in {entry['output']}")
        parsed[entry['output']] = page
        totals['pages'] += 1
        totals['code_blocks'] += len(page.code)
        totals['tables'] += page.tables
        totals['headings'] += page.headings
        totals['images'] += len(page.images)
    for name, page in parsed.items():
        for href in page.links:
            if href.startswith(('https://','http://','mailto:')):
                continue
            path, _, fragment = href.partition('#')
            path = path or name
            if path in parsed:
                if fragment and fragment not in parsed[path].ids:
                    raise ValueError(f'Unresolved anchor {name}: {href}')
                totals['links'] += 1
            elif not (site/path).is_file():
                raise ValueError(f'Unresolved local link {name}: {href}')
        for href in page.images:
            if not (site/href).is_file():
                raise ValueError(f'Missing HTML image {name}: {href}')
    for asset in manifest['assets']:
        if digest((site/asset['href']).read_bytes()) != asset['png_sha256']:
            raise ValueError(f"Published image bytes changed: {asset['href']}")
    # The combined PDF Markdown carries the source inventories independently of HTML.
    manual = markdown_inventory((prepared/manifest['manual']['input']).read_text())
    coverage = manifest['coverage']
    if coverage['code_blocks'] and manual['code'] != [b['sha256'] for b in coverage['code_blocks']]:
        raise ValueError('Combined manual source blocks differ from original AsciiDoc bytes')
    if coverage['tables'] and manual['tables'] != len(coverage['tables']):
        raise ValueError('Combined manual table count differs from original AsciiDoc inventory')
    if pdf:
        data = Path(pdf).read_bytes()
        if not data.startswith(b'%PDF-2.0') or not data.rstrip().endswith(b'%%EOF'):
            raise ValueError('Expected complete PDF 2.0 output')
        totals['pdf_bytes'] = len(data)
        totals['pdf_sha256'] = digest(data)
        if independent_reader:
            info = subprocess.check_output(['pdfinfo', str(pdf)], text=True)
            if not re.search(r'^Tagged:\s+yes$', info, re.M):
                raise ValueError('Independent PDF reader did not find tagged structure')
            extracted = subprocess.check_output(['pdftotext', '-layout', str(pdf), '-'], text=True)
            compact = compact_pdf_text(extracted, manifest['metadata'])
            for index, block in enumerate(manual['code_texts']):
                if re.sub(r'\s+', '', block) not in compact:
                    raise ValueError(f'PDF reader did not retain code block {index}')
            destinations = subprocess.check_output(['pdfinfo', '-dests', str(pdf)], text=True)
            missing = [anchor for anchor in manual['ids'] if '"' + anchor + '"' not in destinations]
            if missing:
                raise ValueError(f'PDF reader did not find heading destinations: {missing}')
            totals['pdf_code_blocks_with_text'] = len(manual['code_texts'])
            totals['pdf_heading_destinations'] = len(manual['ids'])
    return totals



def compact_pdf_text(extracted, metadata):
    """Remove edge furniture before testing non-whitespace source sequences.

    This checks retained code characters through an independent reader. Exact
    authored HTML bytes and source ActualText semantics are tested separately;
    arbitrary readers need not return tabs or line wraps byte-for-byte.
    """
    pages = []
    for page in extracted.split('\f'):
        lines = page.splitlines()
        nonempty = [index for index, line in enumerate(lines) if line.strip()]
        removed = set()
        for index in nonempty[:3]:
            line = lines[index].strip()
            if line.startswith(metadata['title']) and line.endswith(metadata['version']):
                removed.add(index)
        for index in nonempty[-3:]:
            line = lines[index].strip()
            if re.search(r'\d+\s*/\s*\d+$', line) and (not metadata['author'] or line.startswith(metadata['author'])):
                removed.add(index)
        pages.extend(line for index, line in enumerate(lines) if index not in removed)
    return re.sub(r'\s+', '', '\n'.join(pages))


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument('--prepared', required=True)
    args.add_argument('--site', required=True)
    args.add_argument('--pdf')
    args.add_argument('--independent-reader', action='store_true', help='Require Poppler code-text and destination checks')
    options = args.parse_args()
    print(json.dumps(check(options.prepared, options.site, options.pdf, options.independent_reader), indent=2))


if __name__ == '__main__':
    main()
