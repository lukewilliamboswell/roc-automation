"""Independent Python reference for native preparation regression tests only.

The production action and roc-docs binary never invoke this reference adapter.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import os
import textwrap
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
MAX_BYTES = 16 * 1024 * 1024


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe(root, name):
    path = Path(name)
    if path.is_absolute() or not path.parts or any(p in ('..', '.') for p in path.parts):
        raise ValueError(f'Unsafe relative path: {name}')
    candidate = root / path
    cursor = root
    for part in path.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f'Symlink not accepted: {cursor}')
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Path escapes root: {name}')
    return candidate


def read(path):
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError(f'Not a bounded regular input: {path}')
    return path.read_bytes()


def slug(text):
    text = re.sub(r'[`*]', '', text).lower()
    text = re.sub(r'[^\w\s-]', '', text, flags=re.UNICODE)
    return re.sub(r'[-\s]+', '-', text).strip('-') or 'section'


@dataclass(frozen=True)
class Line:
    text: str
    source: str
    number: int
    offset: int = 0


class Converter:
    def __init__(self, docs, backend, pdf_filename, version, diagrams=None, workspace=None):
        self.docs = docs
        self.workspace = (workspace or docs.parent).resolve()
        self.backend = backend
        self.pdf_filename = pdf_filename
        self.version = version
        self.sources = {}
        self.headings = []
        self.code = []
        self.tables = []
        self.images = []
        self.attributes = {}
        self.labels = {}
        self.diagrams = diagrams or {}
        self.anchor_by_line = {}
        self.current_page = ''
        self.targets = {}
        self.routes = {}
        self.expanded_input_bytes = 0

    def listing_include(self, directive, document, number, offset):
        match = re.fullmatch(r'include::([^\[]+)\[([^\]]*)\]', directive)
        if not match or ':' in match[1] or '{' in match[1] or Path(match[1]).is_absolute():
            raise ValueError(f'Invalid local listing include {document}:{number}')
        target = Path(os.path.abspath(self.docs / Path(document).parent / match[1]))
        try:
            relative = target.relative_to(self.workspace)
        except ValueError as error:
            raise ValueError('Listing include escapes workspace') from error
        data = read(safe(self.workspace, relative))
        self.expanded_input_bytes += len(data)
        if len(data) > 1024*1024 or self.expanded_input_bytes > 32*1024*1024 or b'\r' in data:
            raise ValueError('Listing include exceeds byte/line-ending limits')
        body = data.decode('utf-8')
        settings = {}
        for part in filter(None, map(str.strip, match[2].split(','))):
            key, separator, value = part.partition('=')
            if not separator or key in settings or key not in ('tag','indent'):
                raise ValueError('Unsupported listing include option')
            settings[key] = value
        if 'tag' in settings:
            tag = re.escape(settings['tag'])
            regions = re.findall(r'(?ms)^[^\n]*tag::'+tag+r'\[\]\n(.*?)^[^\n]*end::'+tag+r'\[\](?:\n|$)', body)
            if len(regions) != 1:
                raise ValueError('Missing or ambiguous listing tag')
            body = regions[0]
        if 'indent' in settings:
            if settings['indent'] != '0': raise ValueError('Only indent=0 is supported')
            body = textwrap.dedent(body)
        self.sources['workspace:'+relative.as_posix()] = sha(data)
        return [Line(line, document, number, offset) for line in body.removesuffix('\n').split('\n')]

    def expand(self, name, offset=0, stack=()):
        if len(stack) > 32 or name in stack:
            raise ValueError(f'Include cycle/depth: {name}')
        data = read(safe(self.docs, name))
        self.expanded_input_bytes += len(data)
        if self.expanded_input_bytes > 32 * 1024 * 1024 or len(self.sources) > 1024:
            raise ValueError('Expanded documentation exceeds resource limits')
        if b'\r' in data:
            raise ValueError(f'AsciiDoc adapter requires LF source bytes: {name}')
        self.sources[name] = sha(data)
        text = data.decode('utf-8')
        lines = []
        conditional = []
        delimiter = None
        for n, line in enumerate(text.split('\n'), 1):
            if delimiter:
                if all(conditional):
                    if line.startswith('include::'):
                        lines.extend(self.listing_include(line, name, n, offset))
                    else:
                        lines.append(Line(line, name, n, offset))
                if line == delimiter:
                    delimiter = None
                continue
            if re.fullmatch(r'-{4,}|\.{4}', line):
                delimiter = line
                if all(conditional):
                    lines.append(Line(line, name, n, offset))
                continue
            if line.startswith(('ifdef::', 'ifndef::')):
                match = re.fullmatch(r'(ifdef|ifndef)::backend-(html5|pdf)\[\]', line)
                if not match:
                    raise ValueError(f'Unsupported conditional {name}:{n}: {line}')
                active = match[2] == self.backend
                conditional.append(active if match[1] == 'ifdef' else not active)
                continue
            if line == 'endif::[]':
                if not conditional:
                    raise ValueError(f'Unmatched endif {name}:{n}')
                conditional.pop()
                continue
            if not all(conditional):
                continue
            include = re.fullmatch(r'include::([^\[]+)\[(?:leveloffset=([+-]?\d+))?\]', line)
            if include:
                target = (Path(name).parent / include[1]).as_posix()
                lines.extend(self.expand(target, offset + int(include[2] or 0), (*stack, name)))
            elif line.startswith('include::'):
                raise ValueError(f'Unsupported include {name}:{n}')
            else:
                lines.append(Line(line, name, n, offset))
        if delimiter or conditional:
            raise ValueError(f'Unclosed block/conditional: {name}')
        return lines

    def index(self, lines):
        pending = None
        block = None
        used = set()
        local_used = {}
        for item in lines:
            line = item.text
            if block:
                if line == block:
                    block = None
                continue
            if re.fullmatch(r'-{4,}|\.{4}', line):
                block = line
                continue
            explicit = re.fullmatch(r'\[#([\w-]+)\]|\[\[([\w-]+)\]\]', line)
            if explicit:
                pending = explicit[1] or explicit[2]
                continue
            match = re.fullmatch(r'(=+) (.+)', line)
            if pending and line and not match:
                raise ValueError(f'Only heading anchors are supported: {item.source}:{item.number}')
            if match:
                base = pending or slug(match[2])
                anchor = base
                i = 2
                local = local_used.setdefault(item.source, set())
                while anchor in local:
                    if pending:
                        raise ValueError(f'Duplicate explicit anchor: {pending}')
                    anchor = f'{base}-{i}'
                    i += 1
                local.add(anchor)
                original = anchor
                if anchor in used:
                    anchor = slug(Path(item.source).stem) + '-' + anchor
                used.add(anchor)
                self.targets[(item.source, original)] = anchor
                self.anchor_by_line[(item.source, item.number)] = anchor
                self.labels[(item.source, original)] = match[2]
                self.headings.append({'source': item.source, 'line': item.number, 'id': anchor, 'title': match[2], 'level': len(match[1]) + item.offset})
                pending = None
        if pending:
            raise ValueError(f'Anchor without heading: {pending}')

    def inline(self, text, item):
        protected = []
        def protect(match):
            protected.append(match[0].replace('`+', '`').replace('+`', '`'))
            return f'\x00{len(protected)-1}\x00'
        text = re.sub(r'`[^`]+`', protect, text)
        text = re.sub(r'\+\{([^}]+)\}\+', lambda m: '`{' + m[1] + '}`', text)
        def ref(target, label):
            if '#' in target:
                source, anchor = target.split('#', 1)
                source = (source if any(s == source for s, _ in self.labels) else (Path(item.source).parent / source).as_posix()) if source else item.source
            else:
                source, anchor = '', target
            candidates = [(s, a, t) for (s, a), t in self.labels.items() if a == anchor and (not source or source == s)]
            if not candidates:
                raise ValueError(f'Unknown anchor {target} at {item.source}:{item.number}')
            # Prefer local chapter for shorthand references, otherwise first stable manual occurrence.
            chosen = next((c for c in candidates if c[0] == item.source), candidates[0])
            if self.backend == 'pdf' or chosen[0] == self.current_page:
                href = '#' + (self.targets.get((chosen[0], chosen[1]), chosen[1]) if self.backend == 'pdf' else chosen[1])
            else:
                route, anchor = self.routes.get((chosen[0], chosen[1]), (Path(chosen[0]).with_suffix('.html').as_posix(), chosen[1]))
                href = route + '#' + anchor
            return f'[{label or chosen[2]}]({href})'
        text = re.sub(r'xref:([^\s\[]+)\[([^\]]*)\]', lambda m: ref(m[1], m[2]), text)
        text = re.sub(r'<<([\w-]+)(?:,([^>]+))?>>', lambda m: ref(m[1], m[2]), text)
        text = re.sub(r'link:([^\s\[]+)\[([^\]]*)\]', lambda m: f'[{m[2] or m[1]}]({self.pdf_filename if m[1].endswith(".pdf") else m[1]})', text)
        text = re.sub(r'(https?://[^\s\[]+)\[([^\]]*)\]', lambda m: f'[{m[2] or m[1]}]({m[1]})', text)
        text = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'**\1**', text)
        if 'xref:' in text or '<<' in text:
            raise ValueError(f'Unconverted reference: {item}')
        def attribute(match, stack=()):
            name = match[1]
            if name in stack or len(stack) >= 16:
                raise ValueError(f'Attribute cycle/depth at {item.source}:{item.number}: {name}')
            if name == 'docs-version':
                return self.version
            if name not in self.attributes:
                raise ValueError(f'Unknown attribute at {item.source}:{item.number}: {name}')
            return re.sub(r'(?<!\\)\{([A-Za-z][\w-]*)\}', lambda nested: attribute(nested, (*stack, name)), self.attributes[name])
        text = re.sub(r'(?<!\\)\{([A-Za-z][\w-]*)\}', attribute, text)
        return re.sub(r'\x00(\d+)\x00', lambda m: protected[int(m[1])], text)

    def convert(self, lines, page):
        self.current_page = page
        out = []
        i = 0
        ordered = 0
        while i < len(lines):
            item = lines[i]
            line = item.text
            i += 1
            if not line:
                out.append('')
                continue
            attribute = re.fullmatch(r':([\w-]+):\s*(.*)', line)
            if attribute:
                self.attributes[attribute[1]] = attribute[2]
                continue
            if line.startswith('//') or line in ('[.lead]', '[arabic]', '****', '+'):
                continue
            if re.fullmatch(r'\[#([\w-]+)\]|\[\[([\w-]+)\]\]', line):
                continue
            heading = re.fullmatch(r'(=+) (.+)', line)
            if heading:
                level = len(heading[1]) + item.offset
                if level > 6:
                    raise ValueError(f'Heading exceeds Markdown depth: {item}')
                anchor = self.anchor_by_line[(item.source, item.number)]
                out.extend(['', '#' * level + ' ' + self.inline(heading[2], item) + ' {#' + anchor + '}', ''])
                ordered = 0
                continue
            source = re.fullmatch(r'\[source,([\w-]+)\]', line)
            if source or line == '[mermaid]':
                if i >= len(lines) or not re.fullmatch(r'-{4,}|\.{4}', lines[i].text):
                    raise ValueError(f'Missing block delimiter: {item}')
                delimiter = lines[i].text
                i += 1
                code = []
                while i < len(lines) and lines[i].text != delimiter:
                    code.append(lines[i].text)
                    i += 1
                if i == len(lines):
                    raise ValueError(f'Unclosed source block: {item}')
                i += 1
                content = '\n'.join(code) + '\n'
                if source:
                    fence = '`' * max(3, max((len(m[0]) + 1 for m in re.finditer(r'`+', content)), default=3))
                    out.extend(['', fence + source[1], *code, fence, ''])
                    self.code.append({'source': item.source, 'line': item.number, 'sha256': sha(content.encode()), 'language': source[1]})
                else:
                    digest = sha(content.encode())
                    if digest not in self.diagrams:
                        raise ValueError(f'Unprepared/stale Mermaid diagram {item.source}:{item.number}, source SHA256 {digest}')
                    href = self.diagrams[digest]['href']
                    self.images.append({'href': href, 'diagram_sha256': digest})
                    out.extend(['', f'![Diagram from {item.source}]({href})', ''])
                continue
            table = re.fullmatch(r'\[cols="([0-9,]+)",options="header",?\]', line)
            if table:
                cols = len(table[1].split(','))
                if i >= len(lines) or lines[i].text != '|===':
                    raise ValueError(f'Missing table delimiter: {item}')
                i += 1
                cells = []
                while i < len(lines) and lines[i].text != '|===':
                    row = lines[i].text
                    if row:
                        if not row.startswith('|'):
                            if not cells or not row.startswith('  '):
                                raise ValueError(f'Unsupported table continuation: {lines[i]}')
                            cells[-1] += ' ' + row.strip()
                        else:
                            cells.extend(c.strip() for c in re.split(r'(?<!\\)\|', row[1:]))
                    i += 1
                if i == len(lines) or not cells or len(cells) % cols:
                    raise ValueError(f'Unclosed/ragged table: {item}')
                i += 1
                rows = [cells[j:j+cols] for j in range(0, len(cells), cols)]
                def emit(row):
                    return '| ' + ' | '.join(self.inline(c, item) for c in row) + ' |'
                out.extend(['', emit(rows[0]), '| ' + ' | '.join('---' for _ in range(cols)) + ' |', *map(emit, rows[1:]), ''])
                self.tables.append({'source': item.source, 'line': item.number, 'columns': cols, 'column_weights': table[1], 'body_rows': len(rows)-1})
                ordered = 0
                continue
            image = re.fullmatch(r'image::([^\[]+)\[([^\]]*)\]', line)
            if image:
                if ',' in image[2]:
                    raise ValueError(f'Unsupported image options: {item}')
                self.images.append({'href': image[1], 'alt': image[2]})
                out.extend(['', f'![{image[2]}]({image[1]})', ''])
                continue
            if line.startswith('.') and not line.startswith('. '):
                out.extend(['', '**' + self.inline(line[1:], item) + '**', ''])
                continue
            definition = re.fullmatch(r'(.+?):: (.+)', line)
            if definition:
                out.append('- **' + self.inline(definition[1], item) + '**: ' + self.inline(definition[2], item))
                continue
            if line.startswith('* '):
                out.append('- ' + self.inline(line[2:], item))
                ordered = 0
                continue
            if line.startswith('. '):
                ordered += 1
                out.append(f'{ordered}. ' + self.inline(line[2:], item))
                continue
            if line.startswith(('[', 'include::', 'ifdef::', 'endif::', '----', '|===', '....', '////')):
                raise ValueError(f'Unsupported structural syntax: {item}')
            out.append(self.inline(line, item))
        return '\n'.join(out).rstrip() + '\n'


def decode_png(data):
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Expected PNG signature')
    offset = 8
    compressed = bytearray()
    header = None
    ended = False
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError('Truncated PNG chunk')
        length = int.from_bytes(data[offset:offset+4], 'big')
        end = offset + length + 12
        if end > len(data):
            raise ValueError('Truncated PNG payload')
        kind = data[offset+4:offset+8]
        payload = data[offset+8:end-4]
        if zlib.crc32(kind + payload) != int.from_bytes(data[end-4:end], 'big'):
            raise ValueError('PNG CRC mismatch')
        if kind == b'IHDR':
            if header is not None or offset != 8 or length != 13:
                raise ValueError('Invalid PNG IHDR')
            header = struct.unpack('>IIBBBBB', payload)
        elif kind == b'IDAT':
            compressed.extend(payload)
        elif kind == b'tRNS':
            raise ValueError('PNG tRNS transparency is unsupported; use RGBA')
        elif kind == b'IEND':
            if length or end != len(data):
                raise ValueError('Invalid PNG end/trailing bytes')
            ended = True
        elif kind[0] & 32 == 0:
            raise ValueError(f'Unsupported PNG critical chunk {kind!r}')
        offset = end
    if header is None or not ended:
        raise ValueError('Incomplete PNG')
    width, height, depth, color, compression, filtering, interlace = header
    if not width or not height or width * height > 16_000_000:
        raise ValueError('PNG dimensions exceed limit')
    if (depth, color, compression, filtering, interlace) not in ((8, 2, 0, 0, 0), (8, 6, 0, 0, 0)):
        raise ValueError('Only non-interlaced RGB/RGBA 8-bit PNG is supported')
    channels = 3 if color == 2 else 4
    stride = width * channels
    expected = height * (stride + 1)
    decoder = zlib.decompressobj()
    raw = decoder.decompress(bytes(compressed), expected + 1)
    if len(raw) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError('PNG pixel stream length mismatch')
    previous = bytearray(stride)
    pixels = bytearray()
    for row in range(height):
        method = raw[row * (stride+1)]
        if method not in range(5):
            raise ValueError('Unsupported PNG filter')
        scan = bytearray(raw[row*(stride+1)+1:(row+1)*(stride+1)])
        for x in range(stride):
            left = scan[x-channels] if x >= channels else 0
            up = previous[x]
            corner = previous[x-channels] if x >= channels else 0
            p = left + up - corner
            distances = (abs(p-left), abs(p-up), abs(p-corner))
            predictor = (0, left, up, (left+up)//2, (left, up, corner)[distances.index(min(distances))])[method]
            scan[x] = (scan[x] + predictor) % 256
        if channels == 3:
            pixels.extend(scan)
        else:
            for x in range(0, stride, 4):
                alpha = scan[x+3]
                pixels.extend((v*alpha + 255*(255-alpha) + 127)//255 for v in scan[x:x+3])
        previous = scan
    return width, height, bytes(pixels)


def prepare_docs(workspace, docs_directory='docs', entrypoint='index.adoc', output_directory='.docs-prepared', docs_version='', pdf_filename='manual.pdf'):
    workspace = Path(workspace).resolve()
    docs = safe(workspace, docs_directory)
    output = safe(workspace, output_directory)
    if output.exists():
        raise ValueError('Preparation output must be a new directory')
    if not re.fullmatch(r'[\w.-]+\.pdf', pdf_filename):
        raise ValueError('PDF filename must be a simple .pdf filename')
    registry_path = HERE / 'prepared-diagrams' / 'manifest.json'
    registry = json.loads(registry_path.read_text()) if registry_path.exists() else {'diagrams': []}
    diagrams = {d['source_sha256']: {**d, '_root': HERE / 'prepared-diagrams'} for d in registry['diagrams']}
    local_registry = docs / 'prepared-diagrams.json'
    if local_registry.exists():
        local = json.loads(read(safe(docs, 'prepared-diagrams.json')))
        if local.get('schema_version') != 1:
            raise ValueError('Unsupported prepared diagram registry schema')
        for d in local['diagrams']:
            diagrams[d['source_sha256']] = {**d, '_root': docs}
    converter = Converter(docs, 'pdf', pdf_filename, docs_version, diagrams, workspace)
    markdown_mode = Path(entrypoint).suffix == '.md'
    if markdown_mode:
        original = read(safe(docs, entrypoint))
        converter.sources[entrypoint] = sha(original)
        manual = original.decode('utf-8')
        title_match = re.search(r'^# (.+?)(?: \{#[^}]+\})?$', manual, re.M)
        converter.headings = [{'title': title_match[1] if title_match else Path(entrypoint).stem, 'level': 1}]
        expanded = []
    else:
        expanded = converter.expand(entrypoint)
        converter.index(expanded)
        manual = converter.convert(expanded, entrypoint)
    title = next((h['title'] for h in converter.headings if h['level'] == 1), Path(entrypoint).stem)
    pages = []
    outputs = {'manual.md': manual}
    # Retain every top-level AsciiDoc page URL, including the combined index.
    page_catalog = []
    routes = {}
    for source in sorted(docs.glob('*.md' if markdown_mode else '*.adoc')):
        page = Converter(docs, 'html5', pdf_filename, docs_version, diagrams, workspace)
        if markdown_mode:
            data = read(safe(docs, source.name))
            page.sources[source.name] = sha(data)
            rendered = data.decode('utf-8')
            visible = re.sub(r'^(`{3,}|~{3,}).*?^\1[ \t]*$', '', rendered, flags=re.M | re.S)
            page.images = [{'href': match[1]} for match in re.finditer(r'!\[[^\]]*\]\(([^\s)]+)\)', visible)]
        else:
            lines = page.expand(source.name)
            page.index(lines)
            # Cross-page links resolve against the complete manual's source labels.
            page.labels = {**converter.labels, **page.labels}
            rendered = None
            for key, anchor in page.targets.items():
                if key[0] == source.name or (key not in routes and source.name != entrypoint):
                    routes[key] = (source.with_suffix('.html').name, anchor)
        page_catalog.append((source, page, lines if not markdown_mode else None, rendered))
    for source, page, lines, rendered in page_catalog:
        if not markdown_mode:
            page.routes = routes
            rendered = page.convert(lines, source.name)
        name = 'pages/' + source.with_suffix('.md').name
        outputs[name] = rendered
        pages.append({'input': name, 'output': source.with_suffix('.html').name})
        converter.images.extend(page.images)
        converter.sources.update(page.sources)
    if sum(len(content.encode('utf-8')) for content in outputs.values()) > 64 * 1024 * 1024:
        raise ValueError('Prepared documentation exceeds output limit')
    # Validate and decode all assets before writing preparation output.
    assets = []
    payloads = {}
    total_asset_bytes = 0
    if len({i['href'] for i in converter.images}) > 256:
        raise ValueError('Too many image assets')
    for href in sorted({i['href'] for i in converter.images}):
        safe(docs, href)
        diagram = next((d for d in diagrams.values() if d['href'] == href), None)
        if diagram:
            png = read(safe(diagram['_root'], diagram['png']))
            if sha(png) != diagram['png_sha256']:
                raise ValueError(f'Prepared diagram hash mismatch: {href}')
        else:
            png = read(safe(docs, href))
        width, height, rgb = decode_png(png)
        total_asset_bytes += len(png) + len(rgb)
        if total_asset_bytes > 128 * 1024 * 1024:
            raise ValueError('Prepared assets exceed resource limit')
        stem = sha(png)[:24]
        png_name, rgb_name = f'assets/{stem}.png', f'assets/{stem}.rgb'
        payloads[png_name], payloads[rgb_name] = png, rgb
        assets.append({'href': href, 'png': png_name, 'rgb': rgb_name, 'width': width, 'height': height, 'png_sha256': sha(png), 'rgb_sha256': sha(rgb)})
    manifest = {
        'schema_version': 1,
        'metadata': {'title': title, 'author': converter.attributes.get('author', ''), 'version': docs_version, 'language': converter.attributes.get('lang', 'en')},
        'pages': pages,
        'manual': {'input': 'manual.md', 'output': pdf_filename},
        'assets': assets,
        'coverage': {'sources': converter.sources, 'headings': converter.headings, 'code_blocks': converter.code, 'tables': converter.tables, 'adaptations': ['Description lists become bold-label lists; sidebars become ordinary sections.', 'Column weights are recorded; Markdown tables use renderer column layout.', 'PDF excludes backend-html5 conditional content; HTML retains it.']},
    }
    output.mkdir(parents=True)
    for name, content in outputs.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    for name, content in payloads.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', default='.')
    parser.add_argument('--docs-directory', default='docs')
    parser.add_argument('--entrypoint', default='index.adoc')
    parser.add_argument('--output-directory', default='.docs-prepared')
    parser.add_argument('--docs-version', default='')
    parser.add_argument('--pdf-filename', default='manual.pdf')
    args = parser.parse_args()
    try:
        manifest = prepare_docs(**vars(args))
    except (ValueError, OSError, UnicodeError, zlib.error) as error:
        parser.exit(2, f'roc-docs preparation: {error}\n')
    print(f"Prepared {len(manifest['pages'])} pages and {len(manifest['assets'])} assets")


if __name__ == '__main__':
    main()
