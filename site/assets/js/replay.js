// The seven-step scripted offline replay of a recorded repair run.
import { diffFiles, FEEDBACK, ISSUE, TEST_OUTPUT } from './data.js';
import { $, $$, esc, still } from './util.js';

const STEP_MS = 5600;

function lineClass(line, kind) {
  if (kind === 'diff') {
    if (line.startsWith('@@')) return 'ln-hunk';
    if (line.startsWith('+')) return 'ln-add';
    return '';
  }
  if (kind === 'msg') return /^<\/?untrusted/.test(line) ? 'ln-u' : (line.startsWith('Proposal') ? 'ln-b' : '');
  if (kind === 'fail') return line.startsWith('AssertionError') ? 'ln-fail' : '';
  return '';
}
const spans = (lines, kind) => lines.map((l) => `<span class="${lineClass(l, kind)}">${esc(l) || ' '}</span>`).join('');

function codeBlock(lines, { head = '', kind = 'diff', files = null } = {}) {
  const wrap = kind === 'diff' ? '' : ' wrap';
  const top = files
    ? `<span class="tabs-mini" role="group" aria-label="Files in this proposal">${files.map((f, i) => `<button type="button" data-file="${i}" aria-pressed="${i === 0}">${esc(f.name)}</button>`).join('')}</span><span>computed by PatchPilot</span>`
    : `<span>${esc(head)}</span>`;
  return `<div class="code${wrap}"><div class="code-h">${top}</div><pre>${spans(files ? files[0].lines : lines, kind)}</pre></div>`;
}
const checks = (rows) => `<table class="checks"><thead><tr><th>Check</th><th>Expected</th><th>Result</th><th>Holds</th></tr></thead><tbody>${
  rows.map(([c, e, r, ok]) => `<tr class="${ok ? '' : 'row-bad'}"><td>${c}</td><td>${e}</td><td>${r}</td><td class="${ok ? 'held' : 'nothold'}">${ok ? '✓ yes' : '✗ no'}</td></tr>`).join('')
}</tbody></table>`;

const STEPS = [
  { phase: 'intake', label: 'Bug report', sub: 'untrusted text', title: 'The bug report',
    html: () => `<p>A synthetic library with an injected bug. The agent sees this issue and the repository snapshot. The hidden tests and the reference fix stay with the evaluator.</p>${codeBlock(ISSUE, { head: 'issue · inj-001-cachekit · shown to the model as untrusted text', kind: 'text' })}` },
  { phase: 'proposal', label: 'Proposal 1', sub: 'plausible, wrong', title: 'Proposal 1: a plausible wrong fix', files: () => diffFiles('self.hits += 0'),
    html: (s) => `<p>The scripted answer edits the right branch of <code>put()</code> but does not change recency. Edits are search-and-replace and must match exactly once; PatchPilot applies them to the original files and computes the diff itself.</p>${codeBlock([], { files: s.files() })}` },
  { phase: 'sandbox', label: 'Sandbox checks', sub: 'did not hold', bad: true, title: 'Sandbox: the checks do not hold',
    html: () => `<p>Policy passed, so both versions run in the locked-down container. The new test fails before the patch, as it should, but still fails after it.</p>${checks([
      ['regression tests on the original code', 'at least one test fails', 'failed', true],
      ['regression tests on the patched code', 'all tests pass', 'failed', false],
    ])}${codeBlock(TEST_OUTPUT, { head: 'visible test output', kind: 'fail' })}<p class="rp-note warn">In <b>baseline</b> mode the run ends here as <code>unverified</code>. Hidden tests confirm it later: 0 of 3 fail-to-pass.</p>` },
  { phase: 'feedback', label: 'Feedback', sub: 'own checks only', title: 'Feedback from the agent’s own checks',
    html: () => `<p>In <b>repair</b> mode the model is told what its own checks found, inside labelled untrusted blocks. Nothing from the hidden tests exists at this point. The exact message from the recorded run:</p>${codeBlock(FEEDBACK, { head: 'message to the model · request 2', kind: 'msg' })}` },
  { phase: 'proposal', label: 'Proposal 2', sub: 'the fix', title: 'Proposal 2: the fix', files: () => diffFiles('self._entries.move_to_end(key)'),
    html: (s) => `<p>A complete new proposal, applied to the original files again, not on top of proposal 1. One line: rewriting a key now marks it as most recently used.</p>${codeBlock([], { files: s.files() })}` },
  { phase: 'sandbox', label: 'Sandbox checks', sub: 'candidate_verified', title: 'Sandbox: candidate_verified',
    html: () => `<p>All three checks hold.</p>${checks([
      ['regression tests on the original code', 'at least one test fails', 'failed', true],
      ['regression tests on the patched code', 'all tests pass', 'passed', true],
      ['existing tests on the patched code', 'all tests pass', 'passed', true],
    ])}<p class="rp-note ok"><code>candidate_verified</code> is the agent’s own evidence: the same agent wrote the fix and the test. It is not <code>resolved</code>.</p>` },
  { phase: 'evaluator', label: 'Independent grading', sub: 'resolved', title: 'Independent grading: resolved',
    html: () => `<p><code>grade_candidate</code> runs after the run has ended. It rebuilds the tree from the pinned start, adds the hidden tests last, and runs them in a fresh sandbox.</p>
      <dl class="score"><div><dt>Fail-to-pass</dt><dd class="okv">3/3</dd></div><div><dt>Pass-to-pass</dt><dd class="okv">14/14</dd></div><div><dt>Proposals</dt><dd>2 <small>of at most 3</small></dd></div></dl>
      <div class="flowline" aria-label="Run summary"><span>2 model turns</span><span>0 tool calls</span><span class="hl">6 sandbox runs, grading included</span></div>
      <p class="done-stamp"><svg viewBox="0 0 34 34" aria-hidden="true"><circle cx="17" cy="17" r="15" pathLength="1" class="ds-c"/><path d="m10 17.5 5 5 9-10" pathLength="1" class="ds-p"/></svg>replay complete · resolved</p>
      <p class="rp-note vio">The grading result goes into the private run record. It is never fed back to the model.</p>` },
];

export function initReplay({ gsap } = {}) {
  const list = $('#rp-steps');
  if (!list) return;
  const title = $('#rp-title');
  const body = $('#rp-body');
  const kicker = $('#rp-kicker');
  const bar = $('#rp-bar');
  const timerBar = $('#rp-timer');
  const play = $('#rp-play');
  const prev = $('#rp-prev');
  const next = $('#rp-next');
  let step = 0;
  let timer = 0;
  let timerTween = null;

  list.innerHTML = STEPS.map((s, i) => `<li class="rp-step${s.bad ? ' badstep' : ''}"><button type="button" data-step="${i}"><span class="n">${i + 1}</span><span class="l">${esc(s.label)}<span class="s">${esc(s.sub)}</span></span></button></li>`).join('');

  function animateIn() {
    if (!gsap || still()) return;
    gsap.fromTo(title, { opacity: 0, y: 12 }, { opacity: 1, y: 0, duration: 0.6, ease: 'expo.out' });
    const blocks = $$(':scope > p, .checks, .score, .flowline', body);
    const codeLines = $$('pre span', body);
    if (blocks.length) gsap.fromTo(blocks, { opacity: 0, y: 14 }, { opacity: 1, y: 0, duration: 0.6, stagger: 0.07, ease: 'expo.out', delay: 0.05 });
    if (codeLines.length) gsap.fromTo(codeLines, { opacity: 0, x: -10 }, { opacity: 1, x: 0, duration: 0.45, stagger: 0.025, ease: 'power3.out', delay: 0.15 });
    const bad = $$('.row-bad td, .ln-fail', body);
    if (bad.length) gsap.fromTo(bad, { backgroundColor: 'rgba(255,107,98,.35)' }, { backgroundColor: 'rgba(255,107,98,.06)', duration: 1.2, delay: 0.6, ease: 'power2.out', clearProps: 'backgroundColor' });
    const stamp = $('.done-stamp', body);
    if (stamp) {
      gsap.fromTo($$('.ds-c, .ds-p', stamp), { strokeDasharray: 1, strokeDashoffset: 1 }, { strokeDashoffset: 0, duration: 0.8, stagger: 0.25, ease: 'power2.inOut', delay: 0.5 });
      gsap.fromTo(stamp, { opacity: 0, scale: 0.92 }, { opacity: 1, scale: 1, duration: 0.6, ease: 'back.out(2)', delay: 0.45 });
    }
  }

  function wireFiles(s) {
    if (!s.files) return;
    const files = s.files();
    const block = $('.code', body);
    const buttons = $$('.tabs-mini button', block);
    buttons.forEach((b) => b.addEventListener('click', () => {
      buttons.forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
      const pre = $('pre', block);
      pre.innerHTML = spans(files[Number(b.dataset.file)].lines, 'diff');
      if (gsap && !still()) gsap.fromTo($$('span', pre), { opacity: 0, x: -8 }, { opacity: 1, x: 0, duration: 0.35, stagger: 0.02, ease: 'power3.out' });
    }));
  }

  function show(i, { focus = false } = {}) {
    step = Math.max(0, Math.min(STEPS.length - 1, i));
    const s = STEPS[step];
    kicker.textContent = `Step ${step + 1} of ${STEPS.length} · ${s.phase}`;
    title.textContent = s.title;
    body.innerHTML = s.html(s);
    bar.style.transform = `scaleX(${(step + 1) / STEPS.length})`;
    $$('.rp-step', list).forEach((li, k) => {
      li.classList.toggle('cur', k === step);
      li.classList.toggle('done', k < step);
      const b = $('button', li);
      if (k === step) b.setAttribute('aria-current', 'step'); else b.removeAttribute('aria-current');
    });
    prev.disabled = step === 0;
    next.disabled = step === STEPS.length - 1;
    const cur = $$('.rp-step', list)[step];
    if (list.scrollWidth > list.clientWidth) list.scrollTo({ left: cur.offsetLeft - 10, behavior: still() ? 'auto' : 'smooth' });
    wireFiles(s);
    animateIn();
    if (focus) title.focus({ preventScroll: true });
    if (timer) runTimer();
  }

  function runTimer() {
    timerTween?.kill?.();
    timerBar.style.transition = 'none';
    timerBar.style.transform = 'scaleX(0)';
    if (!timer) return;
    void timerBar.offsetWidth;
    timerBar.style.transition = `transform ${STEP_MS}ms linear`;
    timerBar.style.transform = 'scaleX(1)';
  }
  function stop() {
    clearInterval(timer);
    timer = 0;
    runTimer();
    play.setAttribute('aria-label', 'Play replay');
  }
  function start() {
    if (step === STEPS.length - 1) show(0);
    play.setAttribute('aria-label', 'Pause replay');
    timer = setInterval(() => {
      if (step >= STEPS.length - 1) { stop(); return; }
      show(step + 1);
    }, STEP_MS);
    runTimer();
  }
  play.addEventListener('click', () => (timer ? stop() : start()));
  prev.addEventListener('click', () => { stop(); show(step - 1); });
  next.addEventListener('click', () => { stop(); show(step + 1); });
  list.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-step]');
    if (b) { stop(); show(Number(b.dataset.step), { focus: true }); }
  });
  $('.replay').addEventListener('keydown', (e) => {
    if (e.target.matches('input, textarea')) return;
    if (e.key === 'ArrowRight') { stop(); show(step + 1); e.preventDefault(); }
    if (e.key === 'ArrowLeft') { stop(); show(step - 1); e.preventDefault(); }
  });
  // stop autoplay when the replay leaves the viewport
  new IntersectionObserver(([e]) => { if (!e.isIntersecting && timer) stop(); }, { threshold: 0 }).observe($('.replay'));
  show(0);
}
