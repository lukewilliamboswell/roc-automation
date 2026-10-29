// Optional real-browser acceptance audit. Uses Node and an installed Chrome only
// on the test host; the documentation utility has no browser or Node dependency.
import assert from 'node:assert/strict';
import { execFile, spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';

const site = path.resolve(process.argv[2]);
const screenshots = process.argv[3];
assert.ok(process.env.ROC_DOCS_BINARY, 'Set ROC_DOCS_BINARY to the native renderer under review');
const renderer = path.resolve(process.env.ROC_DOCS_BINARY);
const fixtures = fileURLToPath(new URL('./fixtures/visual/docs/', import.meta.url));
const sourceCases = {roc:'sample.roc.txt',yaml:'sample.yaml',yml:'sample.yaml',sh:'sample.sh',bash:'sample.sh',shell:'sample.sh',zsh:'sample.sh',json:'sample.json',diff:'sample.diff',patch:'sample.diff','unknown-language':'sample.txt'};
const sourceText = Object.fromEntries(await Promise.all(Object.entries(sourceCases).map(async ([language, file]) => [language, await fs.readFile(path.join(fixtures, file), 'utf8')])));
const corpus = Object.values(sourceText).join('');
assert.ok(corpus.includes('\t') && / {2}\n/.test(corpus) && corpus.includes('\n\n') && corpus.includes('café') && corpus.includes('<&>'), 'Visual fixture lost its whitespace, Unicode or escaping coverage');
const chrome = process.env.CHROME || (process.platform === 'darwin'
  ? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' : 'google-chrome');
const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'roc-docs-browser-'));
const fixtureRoot = path.join(profile, 'fixture');
const fixtureSite = path.join(fixtureRoot, 'out/site');
const markdownSite = path.join(profile, 'markdown');
const diagramSite = path.join(profile, 'diagrams');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const mime = { '.html': 'text/html', '.svg': 'image/svg+xml', '.png': 'image/png', '.pdf': 'application/pdf', '.ttf':'font/ttf' };
const server = http.createServer(async (req, res) => {
  try {
    let pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
    let root = site;
    for (const [prefix, candidate] of [['/__visual/', fixtureSite], ['/__markdown/', markdownSite], ['/__diagrams/', diagramSite]]) {
      if (pathname.startsWith(prefix)) { root = candidate; pathname = pathname.slice(prefix.length - 1); break; }
    }
    const file = path.resolve(root, '.' + pathname);
    if (!file.startsWith(root + path.sep)) throw new Error('Outside site');
    const data = await fs.readFile(file);
    res.writeHead(200, { 'Content-Type': mime[path.extname(file)] || 'application/octet-stream' }); res.end(data);
  } catch { res.writeHead(404); res.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}/`;
const child = spawn(chrome, ['--headless', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  `--user-data-dir=${profile}`, '--remote-debugging-port=0', 'about:blank'], { stdio: 'ignore' });
let browserError;
child.on('error', error => { browserError = error; });
let ws;
try {
  await fs.cp(fixtures, path.join(fixtureRoot, 'docs'), {recursive:true});
  const runRenderer = promisify(execFile);
  const renderOptions = {env:{...process.env,PATH:'/nonexistent'},timeout:60000,maxBuffer:1024*1024};
  await runRenderer(renderer, ['build','--workspace',fixtureRoot,'--output-directory','out'], renderOptions);
  // Both adapters must preserve the code they hand to the highlighter.
  // CommonMark normalizes CRLF, while the AsciiDoc fixture retains LF bytes.
  const markdown = '# Markdown sources\n\n' + Object.entries(sourceText).map(([language, text]) => '```' + language + '\n' + text + '```\n').join('\n');
  const markdownInput = path.join(profile, 'sources.md');
  await fs.writeFile(markdownInput, markdown.replaceAll('\n','\r\n'));
  await runRenderer(renderer, ['render',markdownInput,markdownSite,'--html-only'], renderOptions);
  const diagramBodies = [
    'flowchart LR\n A[Start] --> B[Done]\n',
    'sequenceDiagram\n Alice->>Bob: Request\n Bob-->>Alice: Reply\n',
    'erDiagram\n CUSTOMER ||--o{ ORDER : places\n',
    'classDiagram\n class Worker {\n +run()*\n +count$\n }\n Worker <|-- Task\n',
    'stateDiagram-v2\n [*] --> Idle\n Idle --> Working : start\n Working --> [*]\n'
  ];
  const diagramInput = path.join(profile, 'diagrams.md');
  await fs.writeFile(diagramInput, '# Diagram acceptance\n\n'+diagramBodies.map(body=>'```mermaid\n'+body+'```\n').join('\n'));
  await runRenderer(renderer, ['render',diagramInput,diagramSite], renderOptions);
  let port;
  for (let n = 0; n < 100; n++) {
    if (browserError) throw browserError;
    try { port = (await fs.readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]; break; }
    catch { await pause(100); }
  }
  assert.ok(port, 'Chrome did not expose a debugging port');
  const target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })).json();
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.addEventListener('open', resolve, { once: true }); ws.addEventListener('error', reject, { once: true }); });
  let serial = 0;
  const pending = new Map();
  ws.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (!message.id) return;
    const request = pending.get(message.id); pending.delete(message.id);
    if (!request) return;
    clearTimeout(request.timeout);
    if (message.error) request.reject(message.error); else request.resolve(message.result);
  });
  const cdp = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++serial;
    const timeout = setTimeout(() => { pending.delete(id); reject(new Error(`Browser timed out: ${method}`)); }, 10000);
    pending.set(id, { resolve, reject, timeout }); ws.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async expression => {
    const response = await cdp('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (response.exceptionDetails) throw new Error(JSON.stringify(response.exceptionDetails));
    return response.result.value;
  };
  const navigate = async url => {
    await cdp('Page.navigate', {url});
    let ready = false;
    for (let n = 0; n < 100; n++) {
      await pause(50);
      ready = await evaluate(`location.href === ${JSON.stringify(url)} && document.readyState === 'complete'`);
      if (ready) break;
    }
    assert.ok(ready, `${url}: page did not finish loading`);
    await evaluate('document.fonts.ready.then(() => true)');
  };
  // This family is deliberately unique: a successful font check cannot use a host font.
  const diagrams = (await fs.readdir(path.join(diagramSite,'images'))).filter(name=>name.endsWith('.svg'));
  assert.equal(diagrams.length,5);
  for (const name of diagrams) {
    await navigate(base+'__diagrams/images/'+name);
    const state = await evaluate(`(() => {
      const view=document.documentElement.viewBox.baseVal;
      const labels=[...document.querySelectorAll('text')];
      return {statuses:[...document.fonts].map(face=>face.status),faces:document.fonts.size,loaded:document.fonts.check('16px RocDocsDiagram'),
        overflow:labels.filter(text=>{const b=text.getBBox(); return b.x<view.x-.1 || b.y<view.y-.1 || b.x+b.width>view.x+view.width+.1 || b.y+b.height>view.y+view.height+.1}).map(text=>text.textContent)};
    })()`);
    assert.ok(state.faces>0 && state.loaded, name+': embedded font failed to load');
    assert.ok(state.statuses.every(status=>status==='loaded'),name+': embedded style font failed to load');
    assert.deepEqual(state.overflow,[],name+': label clipped at SVG view');
  }
  if (screenshots) {
    await fs.mkdir(screenshots,{recursive:true});
    await navigate(base+'__diagrams/index.html');
    await fs.writeFile(path.join(screenshots,'diagram-families.png'),Buffer.from((await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:true})).data,'base64'));
    await promisify(execFile)('pdftoppm',['-f','2','-singlefile','-scale-to','1800','-png',path.join(diagramSite,'manual.pdf'),path.join(screenshots,'diagram-pdf')]);
  }
  const palettes = {
    light:{comment:[82,101,115],name:[52,84,164],type:[0,90,133],keyword:[118,57,165],number:[156,61,16],string:[36,99,51],symbol:[70,83,105],invalid:[172,38,52]},
    dark:{comment:[165,180,196],name:[155,188,255],type:[134,213,235],keyword:[214,173,255],number:[255,192,138],string:[166,214,154],symbol:[196,204,214],invalid:[255,172,183]}
  };
  const checkSources = async (scheme, label, expected) => {
    const state = await evaluate(`({
      overflow:document.documentElement.scrollWidth > innerWidth,
      snippets:[...document.querySelectorAll('pre code[data-lang]')].map(code => ({
        language:code.dataset.lang,text:code.textContent,
        background:getComputedStyle(code).backgroundColor,
        panel:getComputedStyle(code.closest('pre')).backgroundColor,
        scrolls:code.closest('pre').scrollWidth > code.closest('pre').clientWidth,
        tokens:[...code.querySelectorAll('span')].map(span => ({name:span.className,color:getComputedStyle(span).color,background:getComputedStyle(span).backgroundColor}))
      }))
    })`);
    assert.ok(!state.overflow, `${label}: page overflow in ${scheme} mode`);
    const panel = scheme === 'dark' ? 'rgb(32, 37, 42)' : 'rgb(245, 245, 242)';
    for (const snippet of state.snippets) {
      assert.equal(snippet.panel, panel, `${label}: code panel has the wrong ${scheme} background`);
      assert.ok([panel,'rgba(0, 0, 0, 0)'].includes(snippet.background), `${label}: code background obscures its panel`);
      for (const token of snippet.tokens) {
        assert.equal(token.background, 'rgba(0, 0, 0, 0)', `${label}: opaque ${token.name} token background`);
        const colour = palettes[scheme][token.name.replace('syntax-','')];
        assert.ok(colour, `${label}: unknown syntax class ${token.name}`);
        assert.equal(token.color, `rgb(${colour.join(', ')})`, `${label}: ${token.name} has the wrong ${scheme} colour`);
      }
      if (expected) {
        assert.equal(snippet.text, expected[snippet.language], `${label}: ${snippet.language} source whitespace changed`);
        if (snippet.language === 'unknown-language') assert.equal(snippet.tokens.length, 0, 'Unknown languages must retain plain text');
        else assert.ok(snippet.tokens.length > 0, `${label}: missing ${snippet.language} highlighting`);
      }
    }
    if (expected) {
      assert.deepEqual(state.snippets.map(s=>s.language).sort(), Object.keys(expected).sort(), `${label}: source inventory changed`);
      assert.ok(state.snippets.some(s=>s.scrolls), `${label}: long source should scroll inside its block`);
    }
  };
  await cdp('Page.enable');
  await cdp('Page.bringToFront');
  await cdp('Emulation.setFocusEmulationEnabled', {enabled:true});
  await cdp('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  const pages = (await fs.readdir(site)).filter(name => name.endsWith('.html'));
  const inventory = {};
  for (const page of pages) {
    const url = base + page;
    await navigate(url);
    const state = await evaluate(`({
      ids: [...document.querySelectorAll('[id]')].map(e => e.id),
      links: [...document.querySelectorAll('a[href]')].map(e => e.getAttribute('href')),
      images: [...document.querySelectorAll('img')].map(e => ({src:e.currentSrc,loaded:e.complete && e.naturalWidth > 0})),
      snippets: [...document.querySelectorAll('code[data-lang]')].map(e => ({lang:e.dataset.lang,coloured:!!e.querySelector('.syntax-name,.syntax-string,.syntax-keyword,.syntax-number,.syntax-comment')})),
      nav: document.querySelector('.toc') ? getComputedStyle(document.querySelector('.toc')).position : null,
      nested: document.querySelectorAll('.toc ul ul').length,
      mainY: document.querySelector('main').getBoundingClientRect().top,
      captions: [...document.querySelectorAll('figcaption')].map(e=>e.innerText),
      sidebars: document.querySelectorAll('aside.sidebar dl').length,
      overflow: document.documentElement.scrollWidth > innerWidth
    })`);
    assert.equal(state.ids.length, new Set(state.ids).size, `${page}: duplicate IDs`);
    assert.ok(state.images.every(image => image.loaded), `${page}: missing image`);
    assert.ok(!state.overflow, `${page}: desktop horizontal overflow`);
    assert.ok(state.mainY < 600, `${page}: content displaced by contents list`);
    if (state.nav) assert.equal(state.nav, 'fixed', `${page}: desktop navigation is not persistent`);
    for (const snippet of state.snippets) {
      if (['yaml','yml','sh','bash','json','diff'].includes(snippet.lang)) assert.ok(snippet.coloured, `${page}: missing ${snippet.lang} colours`);
    }
    inventory[page] = state;
  }
  for (const [page, state] of Object.entries(inventory)) {
    for (const href of state.links) {
      const url = new URL(href, base + page);
      if (url.origin !== new URL(base).origin || !url.hash) continue;
      const targetPage = inventory[url.pathname.slice(1)];
      if (targetPage) assert.ok(targetPage.ids.includes(decodeURIComponent(url.hash.slice(1))), `${page}: broken link ${href}`);
    }
  }
  await navigate(base + 'index.html');
  const index = inventory['index.html'];
  assert.ok(index.nested > 0, 'Manual TOC has no hierarchy');
  assert.equal(index.images.filter(image => image.src.endsWith('.svg')).length, 6, 'Manual must use six vector diagrams');
  assert.equal(await evaluate(`['Space Grotesk','Plus Jakarta Sans'].every(name => [...document.fonts].some(face => face.family.includes(name) && face.status === 'loaded'))`), true, 'Vendored reading fonts not loaded');
  assert.ok(index.sidebars > 0, 'Introduction lost its sidebar/description list');
  assert.ok(index.captions[0].startsWith('Figure 1.'), 'Figure caption numbering missing');
  assert.equal(await evaluate(`document.querySelector('main h2 .section-number')?.innerText.trim()`), '1.', 'Body heading numbering missing');
  await evaluate(`document.querySelector('main h2 .anchor').focus()`);
  assert.equal(await evaluate(`document.activeElement === document.querySelector('main h2 .anchor')`), true, 'Permalink must receive keyboard focus');
  assert.equal(await evaluate(`getComputedStyle(document.querySelector('main h2 .anchor')).opacity`), '1', 'Keyboard-focused permalink must be visible');
  await evaluate('scrollTo({top:0,behavior:"instant"})');
  const proportions = await evaluate(`(()=>{const cells=[...document.querySelector('main table').querySelectorAll('th')];return cells[0].getBoundingClientRect().width/cells[1].getBoundingClientRect().width})()`);
  assert.ok(Math.abs(proportions - 1/3) < .02, 'Authored 1:3 column weights not respected');
  if (screenshots) {
    await fs.mkdir(screenshots, { recursive: true });
    await fs.writeFile(path.join(screenshots, 'desktop.png'), Buffer.from((await cdp('Page.captureScreenshot', { format: 'png' })).data, 'base64'));
  }
  await cdp('Emulation.setEmulatedMedia', {features:[{name:'prefers-color-scheme',value:'dark'}]});
  assert.equal(await evaluate('getComputedStyle(document.body).backgroundColor'), 'rgb(22, 24, 26)', 'Dark background missing');
  assert.equal(await evaluate('getComputedStyle(document.body).color'), 'rgb(236, 236, 234)', 'Dark text colour missing');
  if (screenshots) await fs.writeFile(path.join(screenshots,'dark.png'), Buffer.from((await cdp('Page.captureScreenshot',{format:'png'})).data,'base64'));
  await cdp('Emulation.setEmulatedMedia', {features:[{name:'prefers-color-scheme',value:'light'}]});
  await cdp('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await evaluate('scrollTo({top:0,behavior:"instant"})'); await pause(100);
  const mobile = await evaluate(`({width:innerWidth,documentWidth:document.documentElement.scrollWidth,open:document.querySelector('.toc details').open,mainY:document.querySelector('main').getBoundingClientRect().top,titleY:document.querySelector('header h1').getBoundingClientRect().top})`);
  assert.equal(mobile.width, 390, 'Mobile viewport expands beyond device width');
  assert.ok(mobile.documentWidth <= 390, 'Mobile page overflows horizontally');
  assert.equal(mobile.open, false, 'Mobile TOC should start collapsed');
  assert.ok(mobile.mainY < 600 && mobile.titleY < 100, 'Title and introduction must be visible on mobile');
  await evaluate(`document.querySelector('.toc summary').click()`);
  assert.equal(await evaluate(`document.querySelector('.toc details').open`), true, 'Mobile TOC must expand');
  assert.equal(await evaluate(`getComputedStyle(document.querySelector('.toc ul')).columnCount`), 'auto', 'Mobile TOC must use one column');
  await evaluate(`document.querySelector('.toc a').click()`); await pause(100);
  assert.equal(await evaluate(`document.querySelector('.toc details').open`), false, 'Choosing a mobile destination closes TOC');
  if (screenshots) {
    await evaluate('scrollTo({top:0,behavior:"instant"})');
    await fs.writeFile(path.join(screenshots, 'mobile.png'), Buffer.from((await cdp('Page.captureScreenshot', { format: 'png' })).data, 'base64'));
  }
  for (const scheme of ['light','dark']) {
    await cdp('Emulation.setEmulatedMedia', {features:[{name:'prefers-color-scheme',value:scheme}]});
    for (const width of [1440,390]) {
      await cdp('Emulation.setDeviceMetricsOverride', {width,height:1000,deviceScaleFactor:1,mobile:width===390});
      for (const page of pages) {
        await navigate(base + page);
        await checkSources(scheme, `${page} (${width}px)`);
      }
      for (const route of ['__visual/','__markdown/']) {
        await navigate(base + route + 'index.html');
        await checkSources(scheme, `${route} (${width}px)`, sourceText);
        if (route === '__visual/') {
          const alias = await evaluate(`(()=>{const aliases=[...document.querySelectorAll('.anchor-alias')];const alias=aliases.find(e=>e.id==='repeated-section-2');return {count:aliases.length,target:alias?.nextElementSibling?.id}})()`);
          assert.equal(alias.count, 1, 'Duplicate-heading fixture must retain its legacy alias');
          assert.equal(alias.target, 'second-repeated-section', 'Legacy alias must precede the second canonical heading');
          if (screenshots) {
            await evaluate(`document.querySelector('pre').scrollIntoView({block:'center'})`);
            await fs.writeFile(path.join(screenshots, `code-${scheme}-${width}.png`), Buffer.from((await cdp('Page.captureScreenshot',{format:'png'})).data,'base64'));
          }
        }
      }
    }
  }
  console.log(JSON.stringify({pages:pages.length,desktop:'passed',mobile:'passed',vectorDiagrams:6,darkMode:'passed',fonts:'passed',links:'passed',highlighting:'passed',sourceFixtures:2,displayCombinations:4}, null, 2));
} finally {
  // Let Chrome flush its profile and stop renderers before removing it. A
  // parent-only SIGTERM can leave Linux helper processes briefly writing files.
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({id: 2147483647, method:'Browser.close'}));
  await new Promise(resolve => {
    if (!child.pid || child.exitCode !== null || child.signalCode !== null) { resolve(); return; }
    const timeout = setTimeout(() => { child.kill(); resolve(); }, 5000);
    child.once('exit', () => { clearTimeout(timeout); resolve(); });
  });
  ws?.close(); server.close();
  await fs.rm(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
}
