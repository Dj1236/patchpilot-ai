// Interactive architecture: trust zones, traceable flows, node details.
// Works without GSAP; with it, flows re-frame the diagram and pulses travel the route.
import { NODES, EDGES, FLOWS, LAYOUTS, ZONES, STATE } from './data.js';
import { $, $$, esc, still } from './util.js';

export function initArchitecture({ gsap } = {}) {
  const svg = $('#arch-svg');
  if (!svg) return;
  const stage = $('#arch-stage');
  const detail = $('#arch-detail');
  const flowCap = $('#arch-flowcap');
  const segs = $$('.seg');
  const segInd = $('.seg-ind');
  let layoutName = '';
  let layout = LAYOUTS.wide;
  let flow = 'repair';
  let selected = 'sandbox';
  let visible = false;
  let raf = 0;
  let packets = [];

  const center = (id) => layout.at[id];
  const edgeId = (a, b) => `e-${a}-${b}`;
  const findEdge = (a, b) => EDGES.find(([x, y]) => (x === a && y === b) || (x === b && y === a));

  function render() {
    const want = stage.clientWidth < 700 ? 'narrow' : 'wide';
    if (want === layoutName) return;
    layoutName = want;
    layout = LAYOUTS[want];
    svg.setAttribute('viewBox', want === 'wide' ? FLOWS[flow].view.join(' ') : `0 0 ${layout.w} ${layout.h}`);
    svg.style.maxWidth = want === 'narrow' ? '520px' : '';
    let out = '<defs><marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 8 4 0 8z" fill="#5ee6d0"/></marker></defs>';
    layout.zones.forEach(([zone, , x, y, w, h], i) => {
      out += `<rect class="zbox zbox-${zone}" data-zone="${zone}" data-zi="${i}" x="${x}" y="${y}" width="${w}" height="${h}" rx="12"/>`;
    });
    for (const [a, b, kind, label] of EDGES) {
      const [x1, y1] = center(a);
      const [x2, y2] = center(b);
      out += `<path id="${edgeId(a, b)}" class="edge${kind === 'side' ? ' edge-side' : ''}" d="M${x1} ${y1}L${x2} ${y2}"/>`;
      if (label && layout.labels) {
        const vertical = Math.abs(x1 - x2) < 4;
        const lx = vertical ? x1 + 10 : (x1 + x2) / 2;
        const ly = vertical ? (y1 + y2) / 2 + 4 : (y1 + y2) / 2 - 8;
        out += `<text class="edge-label" data-edge="${edgeId(a, b)}" x="${lx}" y="${ly}" text-anchor="${vertical ? 'start' : 'middle'}">${label}</text>`;
      }
    }
    for (const [zone, label, x, y] of layout.zones) {
      out += `<text class="zlabel zlabel-${zone}" x="${x + 12}" y="${y + 18}">${label}</text>`;
    }
    for (const [id, node] of Object.entries(NODES)) {
      const [cx, cy, wOverride] = center(id);
      const w = wOverride || layout.nw;
      const h = layout.nh;
      const x = cx - w / 2;
      const y = cy - h / 2;
      out += `<g class="node" id="n-${id}" data-node="${id}" data-cursor="inspect" tabindex="0" role="button" aria-label="${esc(node.t)}: ${esc(ZONES[node.z][1])}. Show details."><g class="lift">`
        + `<rect class="ring" x="${x - 4}" y="${y - 4}" width="${w + 8}" height="${h + 8}" rx="12"/>`
        + `<rect class="nb" x="${x}" y="${y}" width="${w}" height="${h}" rx="9"/>`
        + `<rect class="stripe" x="${x}" y="${y + 12}" width="2.5" height="${h - 24}" rx="1" fill="${ZONES[node.z][0]}"/>`
        + `<text class="nt" x="${x + 14}" y="${cy - 3}">${esc(node.t)}</text>`
        + `<text class="nm" x="${x + 14}" y="${cy + 14}">${esc(node.m)}</text>`
        + '</g></g>';
    }
    out += '<g id="pk"></g>';
    svg.innerHTML = out;
    $$('.node', svg).forEach((g) => {
      g.addEventListener('click', () => select(g.dataset.node, true));
      g.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(g.dataset.node, true); }
      });
    });
    applyFlow(false);
    mark();
  }

  function moveSegIndicator() {
    const on = segs.find((b) => b.dataset.flow === flow);
    if (!on || !segInd) return;
    segInd.style.width = `${on.offsetWidth}px`;
    segInd.style.transform = `translateX(${on.offsetLeft}px)`;
  }

  function applyFlow(animate = true) {
    const f = FLOWS[flow];
    const nodes = new Set([...f.path, ...f.extra]);
    const edges = new Set();
    for (let i = 1; i < f.path.length; i += 1) {
      const e = findEdge(f.path[i - 1], f.path[i]);
      if (e) edges.add(edgeId(e[0], e[1]));
    }
    if (f.extra.includes('ledger')) edges.add(edgeId('model', 'ledger'));
    $$('.edge', svg).forEach((p) => { p.classList.toggle('on', edges.has(p.id)); p.classList.toggle('dim', !edges.has(p.id)); });
    $$('.edge-label', svg).forEach((t) => t.classList.toggle('on', edges.has(t.dataset.edge)));
    $$('.node', svg).forEach((g) => { g.classList.toggle('lit', nodes.has(g.dataset.node)); g.classList.toggle('dim', !nodes.has(g.dataset.node)); });
    $$('.zbox', svg).forEach((r) => r.classList.toggle('is-hot', f.zones.includes(r.dataset.zone) && r.dataset.zone !== 'host'));
    segs.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.flow === flow)));
    moveSegIndicator();
    flowCap.innerHTML = `<b>${f.name}</b>${esc(f.cap)}`;
    // camera: re-frame the wide diagram on the active flow
    if (layoutName === 'wide') {
      const target = f.view.join(' ');
      if (gsap && animate && !still()) gsap.to(svg, { attr: { viewBox: target }, duration: 1.1, ease: 'expo.inOut' });
      else svg.setAttribute('viewBox', target);
    }
    startPulses();
  }

  function mark() { $$('.node', svg).forEach((g) => g.classList.toggle('sel', g.dataset.node === selected)); }

  function select(id, fromUser) {
    selected = id;
    mark();
    const n = NODES[id];
    const [cls, label] = STATE[n.state];
    detail.innerHTML = `
      <div class="ad-head"><span class="ad-zone" style="color:${ZONES[n.z][0]}"><i class="lg" style="border-color:${ZONES[n.z][0]}"></i>${esc(ZONES[n.z][1])}</span><span class="tag ${cls}">${label}</span></div>
      <h3 class="ad-title">${esc(n.t)}</h3>
      <p class="ad-mod">${esc(n.mod)}</p>
      <p class="ad-rule">${esc(n.rule)}</p>
      <ul class="ad-list">${n.pts.map((p) => `<li>${esc(p)}</li>`).join('')}</ul>
      <p class="ad-ev">Evidence: ${n.ev.map((p) => `<code>${esc(p)}</code>`).join(' ')}</p>`;
    if (gsap && fromUser && !still()) {
      gsap.fromTo(detail.children, { opacity: 0, y: 10 }, { opacity: 1, y: 0, duration: 0.5, stagger: 0.04, ease: 'expo.out' });
    }
  }

  /* signal pulses: three packets spaced along the active route */
  function startPulses() {
    cancelAnimationFrame(raf);
    const group = $('#pk', svg);
    if (!group) return;
    group.innerHTML = '';
    packets = [];
    if (still() || !visible || document.hidden) return;
    const pts = FLOWS[flow].path.map(center);
    const segLen = pts.slice(1).map((p, i) => Math.hypot(p[0] - pts[i][0], p[1] - pts[i][1]));
    const total = segLen.reduce((a, b) => a + b, 0);
    const speed = layoutName === 'narrow' ? 0.3 : 0.4; // units per ms
    const period = total / speed + 900;
    const count = 3;
    for (let k = 0; k < count; k += 1) {
      const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      g.innerHTML = '<circle class="packet-halo" r="9"/><circle class="packet" r="3.6"/>';
      group.appendChild(g);
      packets.push({ g, offset: (period / count) * k });
    }
    const t0 = performance.now();
    let lit = '';
    const tick = (now) => {
      let lead = '';
      packets.forEach((p, k) => {
        let d = (((now - t0 + p.offset) % period) * speed);
        if (d > total) { p.g.setAttribute('opacity', '0'); return; }
        p.g.setAttribute('opacity', k === 0 ? '1' : '.55');
        let i = 0;
        while (i < segLen.length - 1 && d > segLen[i]) { d -= segLen[i]; i += 1; }
        const k2 = Math.min(1, d / segLen[i]);
        const x = pts[i][0] + (pts[i + 1][0] - pts[i][0]) * k2;
        const y = pts[i][1] + (pts[i + 1][1] - pts[i][1]) * k2;
        p.g.setAttribute('transform', `translate(${x} ${y})`);
        if (k === 0 && k2 > 0.92) lead = FLOWS[flow].path[i + 1];
      });
      if (lead !== lit) {
        if (lit) $(`#n-${lit}`, svg)?.classList.remove('pulse');
        if (lead) $(`#n-${lead}`, svg)?.classList.add('pulse');
        lit = lead;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
  }

  segs.forEach((b) => b.addEventListener('click', () => { if (flow !== b.dataset.flow) { flow = b.dataset.flow; applyFlow(true); } }));
  render();
  select(selected, false);
  requestAnimationFrame(moveSegIndicator);
  let timer = 0;
  window.addEventListener('resize', () => { clearTimeout(timer); timer = setTimeout(() => { render(); moveSegIndicator(); }, 140); });
  new IntersectionObserver(([e]) => { visible = e.isIntersecting; startPulses(); }, { threshold: 0.15 }).observe(stage);
  document.addEventListener('visibilitychange', startPulses);

  // subtle depth: the diagram tilts toward the pointer (desktop only)
  if (gsap && !still() && window.matchMedia('(pointer: fine)').matches) {
    const rx = gsap.quickTo(svg, 'rotationX', { duration: 0.8, ease: 'power3' });
    const ry = gsap.quickTo(svg, 'rotationY', { duration: 0.8, ease: 'power3' });
    let rect = null;
    stage.addEventListener('pointerenter', () => { rect = stage.getBoundingClientRect(); });
    stage.addEventListener('pointermove', (e) => {
      if (!rect) return;
      ry(((e.clientX - rect.left) / rect.width - 0.5) * 5);
      rx(-((e.clientY - rect.top) / rect.height - 0.5) * 4);
    });
    stage.addEventListener('pointerleave', () => { rx(0); ry(0); });
  }
}
