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

/* ---------- the 3D hero ---------- */
// Only hard blockers turn the 3D scene off. Core count and device memory are not used for
// that: browsers such as Brave randomize them per site against fingerprinting, so they only
// pick the lighter scene.
function heroBlocker() {
  if (still()) return 'reduced motion';
  if (navigator.connection?.saveData) return 'data saver';
  try {
    const probe = document.createElement('canvas');
    if (!(probe.getContext('webgl2') || probe.getContext('webgl'))) return 'WebGL unavailable';
  } catch { return 'WebGL unavailable'; }
  return null;
}
const lowPower = () => (navigator.hardwareConcurrency ?? 8) < 4 || (navigator.deviceMemory ?? 8) < 4;

function staticCore(reason) {
  const step = $('[data-boot="core"]');
  step.classList.remove('is-busy', 'is-done');
  boot('core', 'skip');
  step.textContent = 'repair core · static';
  console.info(`PatchPilot: showing the static repair core (${reason}).`);
}

async function loadHero() {
  const reason = heroBlocker();
  if (reason) { staticCore(reason); return; }
  const step = $('[data-boot="core"]');
  step.classList.add('is-busy');
  try {
    const { startHero } = await import('./hero-scene.js');
    const lite = window.matchMedia('(max-width: 820px)').matches || lowPower();
    hero = startHero({
      canvas: $('#core-canvas'), labels: $('#core-labels'), status: $('#core-status'), lite,
      onFirstFrame: () => { document.documentElement.classList.add('webgl-on'); step.classList.remove('is-busy'); boot('core'); },
      onLost: () => { document.documentElement.classList.remove('webgl-on'); hero?.dispose(); hero = null; staticCore('WebGL context lost'); },
    });
  } catch (error) {
    // the static illustration stays in place
    staticCore(`could not start: ${error?.message || error}`);
  }
}
// after first paint and idle time, so the headline never waits for WebGL
const later = window.requestIdleCallback || ((fn) => setTimeout(fn, 200));
window.addEventListener('load', () => later(loadHero, { timeout: 1500 }), { once: true });
onMotionChange(() => { if (still() && hero) { hero.dispose(); hero = null; document.documentElement.classList.remove('webgl-on'); } });
