(() => {
  const toc = document.querySelector('.toc details');
  if (!toc) return;
  const desktop = matchMedia('(min-width:1100px)');
  const resize = () => { toc.open = desktop.matches; };
  resize();
  desktop.addEventListener('change', resize);
  toc.addEventListener('click', event => {
    if (!desktop.matches && event.target.closest('a')) toc.open = false;
  });
  const links = new Map([...toc.querySelectorAll('a[href^="#"]')].map(a => [decodeURIComponent(a.hash.slice(1)), a]));
  const headings = [...document.querySelectorAll('main h2,main h3,main h4,main h5,main h6')].filter(h => links.has(h.id));
  let scheduled = false;
  const update = () => {
    scheduled = false;
    let current = headings[0];
    for (const heading of headings) {
      if (heading.getBoundingClientRect().top > 100) break;
      current = heading;
    }
    for (const [id, link] of links) {
      if (current && id === current.id) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    }
  };
  addEventListener('scroll', () => {
    if (!scheduled) { scheduled = true; requestAnimationFrame(update); }
  }, { passive: true });
  update();
})();
