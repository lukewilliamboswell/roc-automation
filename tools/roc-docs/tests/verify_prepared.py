"""Check prepared Markdown against actual site output and optional tagged PDF.

Python standard library only. This verifies content inventories and links; it
is not a visual-layout test. --independent-reader additionally requires Poppler
and checks PDF code characters and heading destinations.
"""
import argparse
from collections import Counter
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit


def digest(data):
    return hashlib.sha256(data).hexdigest()


def markdown_inventory(source, nested_fences=False):
    blocks = []
    visible = []
    fence = None
    prefix = ''
    content = []
    for line in source.splitlines(keepends=True):
        bare = line.rstrip('\r\n')
        if fence:
            candidate = bare.removeprefix(prefix)
            if re.fullmatch(re.escape(fence) + r'[ \t]*', candidate):
                blocks.append(''.join(content))
                fence, content = None, []
            else:
                content.append(line.removeprefix(prefix))
        else:
            candidate = bare.lstrip(' ') if nested_fences else bare
            match = re.fullmatch(r'(`{3,}|~{3,})[^\n]*', candidate)
            if match:
                fence = match[1]
                if fence.startswith('`') and '`' in candidate[len(fence):]:
                    raise ValueError('Invalid Markdown backtick fence info')
                prefix = bare[:len(bare) - len(candidate)]
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
        'visible': text,
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
        self.image_alts = []
        self.links = []
        self.alternates = []
        self.describedby = []

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
            self.image_alts.append(attrs.get('alt', ''))
        if tag == 'a':
            self.links.append(attrs.get('href', ''))
        if tag == 'link' and 'alternate' in attrs.get('rel', '').split() and attrs.get('type') == 'text/markdown':
            self.alternates.append(attrs.get('href', ''))
        if tag == 'link' and 'describedby' in attrs.get('rel', '').split():
            self.describedby.append(attrs.get('href', ''))

    def handle_endtag(self, tag):
        if tag == 'pre':
            self.pre = False

    def handle_data(self, data):
        if self.pre:
            self.code[-1] += data


def check(prepared, site, pdf=None, independent_reader=False, markdown=False):
    prepared, site = Path(prepared), Path(site)
    manifest = json.loads((prepared/'manifest.json').read_text())
    if manifest['schema_version'] not in (1, 3):
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
        if markdown:
            check_markdown_page(site, entry['output'], inventory, page)
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
        if asset.get('kind') == 'vector':
            import xml.etree.ElementTree as ET
            data = (site/asset['href']).read_bytes()
            ET.fromstring(data)
            if b'data:font/ttf;base64,' not in data:
                raise ValueError('Generated SVG must embed its font')
            if manifest['schema_version'] == 3 and digest(data) != asset['svg_sha256']:
                raise ValueError('Published SVG bytes changed')
            continue
        if digest((site/asset['href']).read_bytes()) != asset['png_sha256']:
            raise ValueError(f"Published image bytes changed: {asset['href']}")
    if markdown:
        check_markdown_index(site, [entry['output'] for entry in manifest['pages']])
        totals['markdown_pages'] = len(manifest['pages'])
    # The combined PDF Markdown carries the source inventories independently of HTML.
    manual = markdown_inventory((prepared/manifest['manual']['input']).read_text())
    coverage = manifest['coverage'] if manifest['schema_version'] == 1 else {'code_blocks':[], 'tables':[]}
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
            labels = [re.sub(r'[`*_]', '', re.sub(r' \{#[^}]+\}$', '', heading)) for heading in manual['headings']]
            compact = compact_pdf_text(extracted, manifest['metadata'], labels)
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


def heading_label(value):
    value = re.sub(r' \{#[^}]+\}$', '', value)
    value = re.sub(r'\\([\\`*_{}\[\]()#+.!|<>-])', r'\1', value)
    # Exported code-span padding and Markdown escaping change spelling, while
    # the displayed heading and its independently checked anchor stay the same.
    value = re.sub(r'(?<!`)(`+)(?!`)(.*?)(?<!`)\1(?!`)', lambda match: match[2].strip(' '), value)
    value = value.translate(str.maketrans({'’': "'", '‘': "'", '“': '"', '”': '"'}))
    return ' '.join(value.split())


def prose_words(text):
    text = re.sub(r'(?m)^<a id="[^"]+"></a>\s*$', '', text)
    text = re.sub(r'(?m)^(#{1,6} [^\n]*?) \{#[^}\n]+\}[ \t]*$', r'\1', text)
    # The reference adapter supplies synthetic diagram labels. Check image
    # descriptions against admitted HTML separately instead of its labels.
    text = re.sub(r'!\[[^\n]*?\]\([^\n]*?\)', '', text)
    text = re.sub(r'\[([^\n]*?)\]\([^\n]*?\)', r'\1', text)
    text = re.sub(r'\\([\\`*_{}\[\]()#+.!|<>-])', r'\1', text)
    text = unescape(text)
    # Typography and list numbering may differ; losing an authored word must
    # not be hidden by otherwise matching code/table/heading inventories.
    return Counter(re.findall(r'[^\W\d_][\w]*', text, re.UNICODE))


def markdown_destinations(text):
    # The publisher's inline link representation is deliberately small. Source
    # fences have already been removed; code examples are never link evidence.
    # Inline code is also literal, including examples of Markdown link syntax.
    spans = list(re.finditer(r'`+', text))
    cursor = 0
    visible = []
    index = 0
    while index < len(spans):
        opening = spans[index]
        closing = next((i for i in range(index + 1, len(spans))
                        if spans[i].group() == opening.group()), None)
        if closing is None:
            index += 1
            continue
        visible.append(text[cursor:opening.start()])
        visible.append(' ')
        cursor = spans[closing].end()
        index = closing + 1
    visible.append(text[cursor:])
    text = ''.join(visible)
    destinations = []
    for match in re.finditer(r'!?\[[^\n]*?\]\(\s*(?:<([^>\n]+)>|([^\s)]+))(?:\s+"[^"\n]*")?\s*\)', text):
        before = text[:match.start()]
        if (len(before) - len(before.rstrip('\\'))) % 2:
            continue
        destinations.append(match[1] or match[2])
    return destinations


def check_markdown_page(site, route, expected, html):
    name = route.removesuffix('.html') + '.md'
    path = site / name
    if not path.is_file():
        raise ValueError(f'Missing published Markdown: {name}')
    actual = markdown_inventory(path.read_text(), nested_fences=True)
    if actual['code'] != expected['code']:
        raise ValueError(f'Markdown source bytes changed in {name}')
    if [heading_label(h) for h in actual['headings']] != [heading_label(h) for h in expected['headings']]:
        raise ValueError(f'Markdown heading inventory changed in {name}')
    if actual['tables'] != expected['tables'] or actual['images'] != expected['images']:
        raise ValueError(f'Markdown table/image inventory changed in {name}')
    image_alts = re.findall(r'!\[([^\n]*?)\]\(', actual['visible'])
    if [prose_words(alt) for alt in image_alts] != [prose_words(alt) for alt in html.image_alts]:
        raise ValueError(f'Markdown image descriptions changed in {name}')
    if prose_words(expected['visible']) - prose_words(actual['visible']):
        raise ValueError(f'Markdown prose words omitted in {name}')
    anchors = re.findall(r'(?m)^<a id="([^"]+)"></a>\s*$', actual['visible'])
    if len(anchors) != len(set(anchors)) or not set(expected['ids']).issubset(anchors):
        raise ValueError(f'Markdown explicit anchors changed/duplicate in {name}')
    if html.alternates != [name] or html.describedby != ['llms.txt'] or name not in html.links:
        raise ValueError(f'Markdown discovery link missing or ambiguous in {route}')
    for destination in markdown_destinations(actual['visible']):
        target = urlsplit(destination)
        if target.scheme or target.netloc:
            continue
        relative = unquote(target.path) or name
        resolved = (site / relative).resolve()
        if not resolved.is_relative_to(site.resolve()) or not resolved.is_file():
            raise ValueError(f'Unresolved Markdown local link {name}: {destination}')
        if target.fragment:
            if relative.endswith('.md'):
                linked = markdown_inventory(resolved.read_text(), nested_fences=True)
                ids = linked['ids'] + re.findall(r'(?m)^<a id="([^"]+)"></a>\s*$', linked['visible'])
            elif relative.endswith('.html'):
                page = Page()
                page.feed(resolved.read_text())
                ids = page.ids
            else:
                continue
            if unquote(target.fragment) not in ids:
                raise ValueError(f'Unresolved Markdown anchor {name}: {destination}')


def check_markdown_index(site, routes):
    index = site / 'llms.txt'
    if not index.is_file():
        raise ValueError('Missing Markdown discovery index llms.txt')
    expected = {route.removesuffix('.html') + '.md' for route in routes}
    links = markdown_destinations(index.read_text())
    found = {unquote(urlsplit(link).path).rsplit('/', 1)[-1] for link in links}
    if not expected.issubset(found):
        raise ValueError('Markdown discovery index omits chapters')
    for name in found:
        if name.endswith('.md') and not (site / name).is_file():
            raise ValueError(f'Markdown discovery index names missing file: {name}')



def compact_pdf_text(extracted, metadata, chapter_labels=()):
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
        # rc6 continuation furniture is the chapter title. Only remove an
        # exact known title at the first nonempty line, never a body occurrence.
        if nonempty and lines[nonempty[0]].strip() in chapter_labels:
            removed.add(nonempty[0])
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
    args.add_argument('--markdown', action='store_true', help='Also require published Markdown parity and discovery')
    options = args.parse_args()
    print(json.dumps(check(options.prepared, options.site, options.pdf, options.independent_reader, options.markdown), indent=2))


if __name__ == '__main__':
    main()
