// Entry point. Content is already in the HTML; everything here only enhances it.
import { $, still, onMotionChange } from './util.js';
import { initArchitecture } from './architecture.js';
import { initReplay } from './replay.js';
import { initSections } from './sections.js';
import { initStory, initChrome, initPointer, initEntrances } from './motion.js';

const gsap = window.gsap; // vendored, loaded before this module; may be absent if a file failed
const boot = (name, state = 'done') => $(`[data-boot="${name}"]`)?.classList.add(`is-${state}`);

// boot line: each step is ticked when it has really happened
document.fonts?.ready.then(() => boot('fonts'));
boot('motion', gsap && !still() ? 'done' : 'skip');

const ctx = { gsap: still() ? null : gsap };
initChrome();
initStory();
initArchitecture(ctx);
initReplay(ctx);
initSections(ctx);
initPointer(ctx.gsap);

let hero = null;
initEntrances(ctx.gsap, { onHeroScroll: (p) => hero?.setScroll(p) });

/* ---------- the 3D hero: only where it can run well ---------- */
function canRunWebGL() {
  if (still()) return false;
  const conn = navigator.connection;
  if (conn?.saveData) return false;
  if ((navigator.deviceMemory ?? 8) < 4 || (navigator.hardwareConcurrency ?? 8) < 4) return false;
  try {
    const probe = document.createElement('canvas');
    return Boolean(probe.getContext('webgl2') || probe.getContext('webgl'));
  } catch { return false; }
}

async function loadHero() {
  if (!canRunWebGL()) { boot('core', 'skip'); $('[data-boot="core"]').textContent = 'repair core · static'; return; }
  try {
    const { startHero } = await import('./hero-scene.js');
    const lite = window.matchMedia('(max-width: 820px)').matches;
    hero = startHero({
      canvas: $('#core-canvas'), labels: $('#core-labels'), status: $('#core-status'), lite,
      onFirstFrame: () => { document.documentElement.classList.add('webgl-on'); boot('core'); },
      onLost: () => { document.documentElement.classList.remove('webgl-on'); hero?.dispose(); hero = null; },
    });
  } catch (error) {
    // the static illustration stays in place
    boot('core', 'skip');
    console.warn('Repair core not started:', error);
  }
}
// after first paint and idle time, so the headline never waits for WebGL
const later = window.requestIdleCallback || ((fn) => setTimeout(fn, 200));
window.addEventListener('load', () => later(loadHero, { timeout: 1500 }), { once: true });
onMotionChange(() => { if (still() && hero) { hero.dispose(); hero = null; document.documentElement.classList.remove('webgl-on'); } });
