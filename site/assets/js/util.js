// Small shared helpers. All page content is static author data; esc() guards text placed in markup.
export const $ = (selector, scope = document) => scope.querySelector(selector);
export const $$ = (selector, scope = document) => Array.from(scope.querySelectorAll(selector));
export const esc = (text) => String(text).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
export const still = () => reduced.matches;
export const onMotionChange = (fn) => reduced.addEventListener?.('change', fn);

const GLYPHS = 'abcdef0123456789·/_-<>';
// Character scramble for short mono labels only (see DESIGN.md rule 4).
export function scramble(el, finalText, { duration = 700 } = {}) {
  if (still()) { el.textContent = finalText; return Promise.resolve(); }
  return new Promise((resolve) => {
    const start = performance.now();
    const step = (now) => {
      const k = Math.min(1, (now - start) / duration);
      const fixed = Math.floor(finalText.length * k);
      let out = finalText.slice(0, fixed);
      for (let i = fixed; i < finalText.length; i += 1) {
        out += finalText[i] === ' ' ? ' ' : GLYPHS[(Math.random() * GLYPHS.length) | 0];
      }
      el.textContent = out;
      if (k < 1) requestAnimationFrame(step); else { el.textContent = finalText; resolve(); }
    };
    requestAnimationFrame(step);
  });
}
