// Static-demo shim for the edgar-itemize viewer (manual/demo/; loaded by demo/viewer.html,
// which the manual's build hook makes from src/edgar_itemize/viewer/static/index.html).
// It answers the viewer's API calls from files baked by scripts/demo_bake.py:
//   /api/doc/<acc>?...                -> data/<key>/doc.json
//   /api/original/<acc>?... (iframe)  -> data/<key>/original.html
//   /api/raw/<acc>?start=&end=        -> a slice of data/<key>/raw.txt, as {start, end, text}
// <key> is <acc>_<sequence> when the manifest has that key, else <acc>. Every other /api/
// call gets an empty answer (corpora, search) or a 404, so the viewer degrades the way it
// does against a server without those endpoints. The browse drawer, review and gold modes,
// the search box and the parse switch are hidden.
(function(){
  'use strict';
  const HERE = new URL('.', document.currentScript ? document.currentScript.src : location.href).href;
  const DATA = HERE + 'data/';
  // The rewrite table; tests/test_demo.py reads the JSON between the markers.
  const ROUTES = /*ROUTES*/[
    {"name": "doc", "re": "^/api/doc/(\\d{10}-\\d{2}-\\d{6})$", "file": "doc.json", "type": "application/json"},
    {"name": "original", "re": "^/api/original/(\\d{10}-\\d{2}-\\d{6})$", "file": "original.html", "type": "text/html"},
    {"name": "raw", "re": "^/api/raw/(\\d{10}-\\d{2}-\\d{6})$", "file": "raw.txt", "type": "application/json"},
    {"name": "corpora", "re": "^/api/corpora$", "body": {"corpora": ["10k", "10q", "ex10", "ex13"], "text": null}},
    {"name": "search", "re": "^/api/search$", "body": []},
    {"name": "capabilities", "re": "^/api/capabilities$", "body": {"banks": false, "runs": false, "manifests": false, "gold": false, "review": false, "text_corpus": false}}
  ]/*END*/.map(r => ({...r, rx: new RegExp(r.re)}));
  const NOT_HERE = 'not available in the static demo (pre-baked documents only)';

  const native = window.fetch.bind(window);
  const manifest = native(DATA + 'manifest.json').then(r => r.json());
  let MANIFEST = null; manifest.then(m => { MANIFEST = m; }).catch(() => {});
  const rawCache = {};

  function entryFor(m, acc, seq){
    const docs = (m && m.documents) || [];
    return docs.find(d => seq && d.key === `${acc}_${seq}`) || docs.find(d => d.key === acc) || null;
  }
  function route(u){
    for(const r of ROUTES){ const m = r.rx.exec(u.pathname); if(m) return {r, acc: m[1] || null}; }
    return null;
  }
  function json(body, status){
    return new Response(JSON.stringify(body), {status: status || 200, headers: {'Content-Type': 'application/json'}});
  }
  function apiPath(input){
    const s = typeof input === 'string' ? input : (input && input.url) || '';
    const u = new URL(s, location.href);
    // the viewer calls root-relative /api/...; on the demo site that resolves to the site root
    return u.origin === location.origin && u.pathname.startsWith('/api/') ? u : null;
  }
  async function raw(e){
    if(!rawCache[e.key]) rawCache[e.key] = native(DATA + e.key + '/raw.txt').then(r => { if(!r.ok) throw new Error(r.status); return r.text(); });
    return rawCache[e.key];
  }

  window.fetch = async function(input, init){
    const u = apiPath(input);
    if(!u) return native(input, init);
    const hit = route(u);
    if(!hit) return new Response(NOT_HERE, {status: 404, headers: {'Content-Type': 'text/plain'}});
    if(hit.r.body !== undefined) return json(hit.r.body);
    const m = await manifest;
    const e = entryFor(m, hit.acc, u.searchParams.get('sequence'));
    if(!e) return new Response(`${hit.acc} is not one of the demo documents`, {status: 404});
    if(hit.r.name === 'raw'){
      const text = await raw(e);
      const start = +u.searchParams.get('start'), end = +u.searchParams.get('end');
      const a = Math.max(0, start - e.raw_base), b = Math.max(a, Math.min(text.length, end - e.raw_base));
      return json({start, end, text: text.slice(a, b)});
    }
    return native(DATA + e.key + '/' + hit.r.file);
  };

  // The original pane is an <iframe src="/api/original/...">, not a fetch: rewrite the src.
  const desc = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'src');
  Object.defineProperty(HTMLIFrameElement.prototype, 'src', {
    configurable: true, enumerable: desc.enumerable, get(){ return desc.get.call(this); },
    set(v){
      const u = apiPath(String(v)); const hit = u && route(u);
      if(hit && hit.r.name === 'original'){
        const e = entryFor(MANIFEST, hit.acc, u.searchParams.get('sequence'));
        v = e ? DATA + e.key + '/original.html' : 'about:blank';
      }
      desc.set.call(this, v);
    }
  });

  // Hide what the demo does not serve; keep 'b' (browse drawer) from opening it.
  const css = document.createElement('style');
  css.textContent = `#q, #kind, #year, #agent, #go, #parsesw, #keyshelp, a.hlink[href="/turns"], #btab, #browse,
    #review-panel, #gold-panel, label:has(#review), label:has(#gold), #side h3:first-of-type, #results { display:none !important; }
    #demopick{ font:inherit; padding:.3rem .45rem; border:1px solid var(--rule); background:var(--band); color:var(--ink); border-radius:2px; max-width:46ch; }`;
  document.head.appendChild(css);
  document.addEventListener('keydown', e => { if(e.key === 'b' && !e.ctrlKey && !e.metaKey && !e.altKey) e.stopImmediatePropagation(); }, true);

  // A document picker in place of the search box, when the viewer is opened on its own
  // (inside the demo page the page's own picker drives it).
  if(window.parent === window){
    document.addEventListener('DOMContentLoaded', async () => {
      const m = await manifest; const h = document.querySelector('header'); if(!h) return;
      const sel = document.createElement('select'); sel.id = 'demopick'; sel.setAttribute('aria-label', 'demo document');
      sel.innerHTML = '<option value="">choose a demo filing…</option>' + m.documents.map(d =>
        `<option value="${d.key}">${d.company} · ${d.form} · ${d.year}</option>`).join('');
      const cur = new URLSearchParams(location.search);
      const ce = entryFor(m, cur.get('acc'), cur.get('seq')); if(ce) sel.value = ce.key;
      sel.addEventListener('change', () => {
        const d = m.documents.find(x => x.key === sel.value); if(!d) return;
        const q = new URLSearchParams({acc: d.accession, corpus: d.corpus}); if(d.key !== d.accession) q.set('seq', d.sequence);
        location.search = q.toString();
      });
      h.prepend(sel);
    });
  }
})();
