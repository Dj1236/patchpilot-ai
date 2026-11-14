// Page choreography. Layout features (story stage, nav state, progress) work without GSAP;
// entrances, scroll scrubbing and pointer effects need GSAP and are skipped for reduced motion.
import { $, $$, still, scramble } from './util.js';

/* ---------- story: one sticky stage, seven scenes (IntersectionObserver, no library) ---------- */
export function initStory() {
  const grid = $('.story-grid');
  const frame = $('#stage-frame');
  if (!grid || !frame) return;
  const scenes = $$('.scene', grid);
  const figs = scenes.map((s) => $('.scene-fig', s));
  const counter = $('#stage-n');
  const desktop = window.matchMedia('(min-width: 1024px)');
  let io = null;

  const activate = (i) => {
    scenes.forEach((s, k) => s.classList.toggle('is-on', k === i));
    figs.forEach((f, k) => f.classList.toggle('is-on', k === i));
    counter.textContent = `${String(i + 1).padStart(2, '0')} / ${String(scenes.length).padStart(2, '0')}`;
  };
  const stageOn = () => {
    figs.forEach((f) => frame.appendChild(f));
    grid.classList.add('is-staged');
    io = new IntersectionObserver((entries) => {
      entries.forEach((e) => { if (e.isIntersecting) activate(scenes.indexOf(e.target)); });
    }, { rootMargin: '-45% 0px -45% 0px' });
    scenes.forEach((s) => io.observe(s));
    activate(0);
  };
  const stageOff = () => {
    io?.disconnect();
    figs.forEach((f, k) => { f.classList.remove('is-on'); scenes[k].appendChild(f); });
    grid.classList.remove('is-staged');
  };
  const apply = () => (desktop.matches ? stageOn() : stageOff());
  desktop.addEventListener('change', apply);
  apply();

  // rail progress follows scroll through the scenes
  const fill = $('#rail-fill');
  const list = $('.scenes', grid);
  let ticking = false;
  const update = () => {
    ticking = false;
    if (!grid.classList.contains('is-staged')) return;
    const r = list.getBoundingClientRect();
    const p = Math.min(1, Math.max(0, (window.innerHeight * 0.5 - r.top) / r.height));
    fill.style.transform = `scaleY(${p})`;
  };
  window.addEventListener('scroll', () => { if (!ticking) { ticking = true; requestAnimationFrame(update); } }, { passive: true });
  update();
}

/* ---------- reading progress and navigation state ---------- */
export function initChrome() {
  const bar = $('#read-progress');
  let ticking = false;
  const update = () => {
    ticking = false;
    const max = document.documentElement.scrollHeight - window.innerHeight;
    bar.style.transform = `scaleX(${max > 0 ? window.scrollY / max : 0})`;
  };
  window.addEventListener('scroll', () => { if (!ticking) { ticking = true; requestAnimationFrame(update); } }, { passive: true });
  update();

  const nav = $('.nav-links');
  const ind = $('.nav-ind');
  const links = $$('a', nav);
  let current = null;
  const moveTo = (a) => {
    if (!a) { ind.style.opacity = '0'; return; }
    ind.style.opacity = '1';
    ind.style.width = `${a.offsetWidth - 22}px`;
    ind.style.transform = `translateX(${a.offsetLeft + 11}px)`;
  };
  links.forEach((a) => a.addEventListener('pointerenter', () => moveTo(a)));
  nav.addEventListener('pointerleave', () => moveTo(current));
  const byId = new Map(links.map((a) => [a.getAttribute('href').slice(1), a]));
  const spy = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (!e.isIntersecting) return;
      links.forEach((a) => a.removeAttribute('aria-current'));
      current = byId.get(e.target.id) || null;
      current?.setAttribute('aria-current', 'true');
      moveTo(current);
    });
  }, { rootMargin: '-45% 0px -50% 0px' });
  $$('main > section[id]').forEach((s) => spy.observe(s));
}

/* ---------- pointer effects (desktop, fine pointer, motion allowed) ---------- */
export function initPointer(gsap) {
  const fine = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
  // direction-aware fill and spotlight only set CSS variables: safe everywhere
  $$('.dir-fill').forEach((el) => el.addEventListener('pointerenter', (e) => {
    const r = el.getBoundingClientRect();
    el.style.setProperty('--fx', `${e.clientX - r.left}px`);
    el.style.setProperty('--fy', `${e.clientY - r.top}px`);
  }));
  $$('.spot').forEach((el) => {
    let r = null;
    el.addEventListener('pointerenter', () => { r = el.getBoundingClientRect(); });
    el.addEventListener('pointermove', (e) => {
      if (!r) return;
      el.style.setProperty('--mx', `${e.clientX - r.left}px`);
      el.style.setProperty('--my', `${e.clientY - r.top}px`);
    });
  });
  if (!gsap || !fine || still()) return;

  // cursor light with contextual label
  document.documentElement.classList.add('has-cursor');
  const cursor = $('.cursor');
  const label = $('#cursor-label');
  const cx = gsap.quickTo(cursor, 'x', { duration: 0.35, ease: 'power3' });
  const cy = gsap.quickTo(cursor, 'y', { duration: 0.35, ease: 'power3' });
  window.addEventListener('pointermove', (e) => { cx(e.clientX); cy(e.clientY); cursor.classList.add('is-live'); }, { passive: true });
  document.addEventListener('pointerover', (e) => {
    const t = e.target.closest('a, button, [data-cursor], input');
    cursor.classList.toggle('is-active', Boolean(t) && !t.matches('input'));
    const text = t?.closest('[data-cursor]')?.dataset.cursor || '';
    label.textContent = text;
    cursor.classList.toggle('has-label', Boolean(text));
  });
  document.documentElement.addEventListener('pointerleave', () => cursor.classList.remove('is-live'));

  // magnetic controls
  $$('.magnetic').forEach((el) => {
    const mx = gsap.quickTo(el, 'x', { duration: 0.5, ease: 'power3' });
    const my = gsap.quickTo(el, 'y', { duration: 0.5, ease: 'power3' });
    let r = null;
    el.addEventListener('pointerenter', () => { r = el.getBoundingClientRect(); });
    el.addEventListener('pointermove', (e) => {
      if (!r) return;
      mx((e.clientX - (r.left + r.width / 2)) * 0.22);
      my((e.clientY - (r.top + r.height / 2)) * 0.32);
    });
    el.addEventListener('pointerleave', () => { gsap.to(el, { x: 0, y: 0, duration: 0.7, ease: 'elastic.out(1, 0.4)' }); });
  });

  // 3D tilt on documentation cards
  $$('.tilt').forEach((el) => {
    gsap.set(el, { transformPerspective: 800 });
    const rx = gsap.quickTo(el, 'rotationX', { duration: 0.5, ease: 'power3' });
    const ry = gsap.quickTo(el, 'rotationY', { duration: 0.5, ease: 'power3' });
    let r = null;
    el.addEventListener('pointerenter', () => { r = el.getBoundingClientRect(); });
    el.addEventListener('pointermove', (e) => {
      if (!r) return;
      ry(((e.clientX - r.left) / r.width - 0.5) * 8);
      rx(-((e.clientY - r.top) / r.height - 0.5) * 8);
    });
    el.addEventListener('pointerleave', () => { rx(0); ry(0); });
  });
}

/* ---------- entrances and scroll choreography ---------- */
export function initEntrances(gsap, hooks = {}) {
  const ST = window.ScrollTrigger;
  const Split = window.SplitText;
  if (!gsap || !ST || still()) {
    $$('.meters').forEach((m) => m.classList.add('is-in'));
    return;
  }
  gsap.registerPlugin(ST, ...(Split ? [Split] : []));
  document.documentElement.classList.add('js-motion');

  // hero: masked line reveal, then the supporting elements
  const lines = $$('.ht-line');
  lines.forEach((l) => { l.innerHTML = `<span class="ht-in" style="display:inline-block">${l.innerHTML}</span>`; l.style.overflow = 'clip'; l.style.paddingBottom = '.08em'; l.style.marginBottom = '-.08em'; });
  const intro = gsap.timeline({ defaults: { ease: 'expo.out' } });
  // the headline is never fully hidden: it is readable from the first frame
  intro.from($$('.ht-in'), { yPercent: 38, opacity: 0.3, duration: 1.1, stagger: 0.09 })
    .from(['.hero-lede', '.cta', '.hero-status'], { y: 18, opacity: 0.3, duration: 0.8, stagger: 0.08 }, '-=0.75')
    .from('.evidence', { y: 30, opacity: 0, duration: 0.9 }, '-=0.6')
    .from($$('#hero-console li'), { opacity: 0, x: -8, duration: 0.5, stagger: 0.12, ease: 'power3.out' }, '-=0.5')
    .from('.scroll-cue', { opacity: 0, duration: 0.6 }, '-=0.3');

  // hero exit: content drifts up and fades as the scene pulls back
  gsap.to('.hero-inner', { yPercent: -10, opacity: 0.2, ease: 'none', scrollTrigger: { trigger: '.hero', start: 'top top', end: 'bottom top', scrub: true } });
  ST.create({ trigger: '.hero', start: 'top top', end: 'bottom top', onUpdate: (self) => hooks.onHeroScroll?.(self.progress) });

  // section headings: line masks
  $$('h2.split').forEach((h) => {
    if (!Split) return;
    Split.create(h, {
      type: 'lines', mask: 'lines', autoSplit: true,
      onSplit: (self) => gsap.from(self.lines, { yPercent: 105, duration: 1, stagger: 0.08, ease: 'expo.out', scrollTrigger: { trigger: h, start: 'top 86%', once: true } }),
    });
  });
  // short mono labels: one scramble on first view
  $$('[data-scramble]').forEach((el) => {
    const text = el.textContent;
    ST.create({ trigger: el, start: 'top 90%', once: true, onEnter: () => scramble(el, text, { duration: 600 }) });
  });
  // blocks: one entrance each, batched
  const blocks = '.section-lede, .arch, .replay, .eng-copy, .contain-vis, .term-panel, .budget, .sm-panel, .sep-vis, .redact, .approve, .statuses, .scripted-banner, .ev-mode, .ev-right, .ev-notes > div, .st, .gate-list li, .doc, .quick';
  gsap.set(blocks, { opacity: 0, y: 32 });
  ST.batch(blocks, {
    start: 'top 88%', once: true,
    onEnter: (els) => gsap.to(els, { opacity: 1, y: 0, duration: 0.8, stagger: 0.08, ease: 'expo.out', overwrite: true, clearProps: 'opacity,transform' }),
  });
  // budget counters and resource meters
  $$('.count').forEach((n) => {
    const to = Number(n.dataset.to);
    const dec = Number(n.dataset.dec || 0);
    const o = { v: 0 };
    n.textContent = (0).toFixed(dec);
    gsap.to(o, { v: to, duration: 1.3, ease: 'expo.out', scrollTrigger: { trigger: n, start: 'top 90%', once: true }, onUpdate: () => { n.textContent = o.v.toFixed(dec); } });
  });
  $$('.meters').forEach((m) => ST.create({ trigger: m, start: 'top 85%', once: true, onEnter: () => m.classList.add('is-in') }));
  // story stage figures: a small accent when a scene becomes active
  const net = $('.net-cut i');
  if (net) gsap.from(net, { scaleX: 0, transformOrigin: 'left center', duration: 1.2, ease: 'expo.out', scrollTrigger: { trigger: net, start: 'top 90%', once: true } });
  window.addEventListener('load', () => ST.refresh());
}
