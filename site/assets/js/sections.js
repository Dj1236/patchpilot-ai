// Engineering demonstrations and the scripted evaluation, all from static data.
import { SCENARIOS, STATUS_TEXT, DIGEST, CONFIRM_CHARS } from './data.js';
import { $, $$, esc, still, scramble } from './util.js';

/* state machine with an interrupt switch (memory) */
function initStateMachine() {
  const states = $$('.sm-state');
  const sw = $('#sm-switch');
  const out = $('#sm-out');
  const json = $('#sm-json');
  if (!sw) return;
  const order = ['propose', 'evaluate', 'feedback', 'propose', 'evaluate', 'done'];
  let i = 0;
  let attempt = 1;
  let timer = 0;
  let visible = false;
  const paint = () => {
    const name = order[i];
    states.forEach((s) => { s.classList.toggle('is-on', s.dataset.state === name); s.classList.remove('is-saved'); });
    states.find((s) => s.dataset.state === name)?.classList.add('is-saved');
    if (name === 'propose' && i > 0) attempt = 2;
    if (i === 0) attempt = 1;
    json.textContent = `{ "schema_version": 1, "step": "${name}", "attempt": ${attempt}, "max_attempts": 3, "sha256": "…" }`;
  };
  const tick = () => { i = (i + 1) % order.length; paint(); };
  const run = () => {
    clearInterval(timer);
    if (sw.getAttribute('aria-checked') === 'true' || !visible || still()) return;
    timer = setInterval(tick, 1300);
  };
  sw.addEventListener('click', () => {
    const stopped = sw.getAttribute('aria-checked') !== 'true';
    sw.setAttribute('aria-checked', String(stopped));
    out.classList.toggle('is-stop', stopped);
    out.textContent = stopped
      ? `stopped at "${order[i]}" · checkpoint on disk · resume refused if the ledger shows a later request`
      : 'resumed from the checkpoint · limits and counters restored';
    run();
  });
  paint();
  new IntersectionObserver(([e]) => { visible = e.isIntersecting; run(); }).observe(sw);
}

/* secret redaction: [redacted] is the marker used by src/patchpilot/agent/trace.py */
function initRedaction() {
  const btn = $('#redact-btn');
  const secret = $('#secret-text');
  if (!btn) return;
  const original = secret.textContent;
  btn.addEventListener('click', async () => {
    const redacted = !secret.classList.contains('is-redacted');
    btn.disabled = true;
    await scramble(secret, redacted ? '[redacted]' : original, { duration: 650 });
    secret.classList.toggle('is-redacted', redacted);
    $('span', btn).textContent = redacted ? 'Show the original' : 'Write to trace';
    btn.disabled = false;
  });
}

/* approval: same rule as approve() in src/patchpilot/submit/approval.py */
function initApproval({ gsap }) {
  const input = $('#ap-input');
  const out = $('#ap-out');
  if (!input) return;
  $('#ap-digest').innerHTML = `<mark>${DIGEST.slice(0, CONFIRM_CHARS)}</mark>${DIGEST.slice(CONFIRM_CHARS)}`;
  const tryApprove = () => {
    const ok = input.value.trim().toLowerCase() === DIGEST.slice(0, CONFIRM_CHARS);
    out.className = `approve-out ${ok ? 'ok' : 'bad'}`;
    out.textContent = ok
      ? 'Approved (demo). A real approval is written to approval.json, bound to this exact proposal, and expires in 24 hours.'
      : 'the confirmation does not match the patch digest; nothing approved';
    input.classList.toggle('is-ok', ok);
    input.classList.toggle('is-bad', !ok);
    if (!ok && gsap && !still()) gsap.fromTo(input, { x: -6 }, { x: 0, duration: 0.5, ease: 'elastic.out(1, 0.3)' });
  };
  $('#ap-go').addEventListener('click', tryApprove);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') tryApprove(); });
}

/* evaluation: every number comes from window.PP_BENCHMARK (a checked copy of the results file) */
function initEvaluation({ gsap }) {
  const data = window.PP_BENCHMARK;
  const totals = $('#ev-totals');
  if (!data || !totals) return;
  const modes = ['baseline', 'repair'];
  const order = Object.keys(SCENARIOS);
  totals.innerHTML = modes.map((mode) => {
    const s = data.summary[mode];
    const rows = data.rows.filter((r) => r.mode === mode).sort((x, y) => Number(y.resolved) - Number(x.resolved));
    const bar = rows.map((r) => `<i class="${r.resolved ? 'r' : ''}" data-scn="${esc(r.scenario)}"></i>`).join('');
    return `<article class="ev-mode spot"><h3>${mode === 'baseline' ? 'Baseline' : 'Repair'}<span>${mode === 'baseline' ? 'one proposal' : 'up to 3 proposals'}</span></h3>
      <p class="ev-big"><span class="ev-num" data-to="${s.resolved_by_hidden_tests}">${s.resolved_by_hidden_tests}</span><small> / ${s.runs} resolved by hidden tests</small></p>
      <div class="ev-bar" aria-hidden="true">${bar}</div>
      <p class="ev-sub"><span><b>${s.verified_by_agent}</b> verified by the agent</span><span><b>${s.proposals}</b> proposals</span><span><b>${s.sandbox_runs_including_grading}</b> sandbox runs</span></p></article>`;
  }).join('');

  const table = $('#ev-matrix');
  let html = '<thead><tr><th scope="col">Scenario</th><th scope="col">Baseline</th><th scope="col"></th><th scope="col">Repair</th><th scope="col"></th></tr></thead><tbody>';
  for (const scenario of order) {
    html += `<tr data-scn="${esc(scenario)}"><th scope="row">${esc(scenario)}</th>`;
    for (const mode of modes) {
      const runs = data.rows.filter((r) => r.scenario === scenario && r.mode === mode);
      const resolved = runs.filter((r) => r.resolved).length;
      const cells = runs.map((r) => {
        const kind = r.resolved ? 'resolved' : (r.status in STATUS_TEXT && r.status !== 'candidate_verified' ? r.status : 'unverified');
        const f2p = r.fail_to_pass ? `fail-to-pass ${r.fail_to_pass[0]}/${r.fail_to_pass[1]}, pass-to-pass ${r.pass_to_pass[0]}/${r.pass_to_pass[1]}` : 'not graded';
        const tip = `${r.task} · ${mode} · ${STATUS_TEXT[r.status] || r.status} · ${r.resolved ? 'resolved' : 'not resolved'} · ${f2p} · ${r.proposals} proposal${r.proposals === 1 ? '' : 's'}`;
        return `<button type="button" class="sq sq-${kind}" data-tip="${esc(tip)}" aria-label="${esc(tip)}"></button>`;
      }).join('');
      html += `<td><span class="cells">${cells}</span></td><td class="cnt">${resolved}/${runs.length}</td>`;
    }
    html += '</tr>';
  }
  table.insertAdjacentHTML('beforeend', `${html}</tbody>`);
  const tip = $('#ev-tip');
  const show = (e) => { const b = e.target.closest('[data-tip]'); if (b) tip.textContent = b.dataset.tip; };
  table.addEventListener('mouseover', show);
  table.addEventListener('focusin', show);

  // scenario selector: highlights its rows and its squares in both bars
  const chips = $('#ev-chips');
  const desc = $('#ev-desc');
  chips.innerHTML = ['all', ...order].map((s) => `<button type="button" class="chip" data-scn="${esc(s)}" aria-pressed="${s === 'all'}">${s === 'all' ? 'all scenarios' : esc(s)}</button>`).join('');
  const pick = (scn) => {
    $$('.chip', chips).forEach((c) => c.setAttribute('aria-pressed', String(c.dataset.scn === scn)));
    $$('tbody tr', table).forEach((tr) => tr.classList.toggle('fade', scn !== 'all' && tr.dataset.scn !== scn));
    $$('.ev-bar i', totals).forEach((i) => i.classList.toggle('fade', scn !== 'all' && i.dataset.scn !== scn));
    if (scn === 'all') { desc.textContent = 'Five scripted scenarios on four synthetic tasks, in both modes: 40 runs.'; return; }
    const res = modes.map((m) => `${m} ${data.rows.filter((r) => r.scenario === scn && r.mode === m && r.resolved).length}/4`).join(' · ');
    desc.textContent = `${SCENARIOS[scn]} Resolved: ${res}.`;
  };
  chips.addEventListener('click', (e) => { const c = e.target.closest('.chip'); if (c) pick(c.dataset.scn); });
  pick('all');

  if (gsap && !still() && window.ScrollTrigger) {
    gsap.from($$('.ev-bar i', totals), { scaleY: 0.15, opacity: 0.2, duration: 0.6, stagger: 0.018, ease: 'expo.out', scrollTrigger: { trigger: totals, start: 'top 80%', once: true } });
    $$('.ev-num', totals).forEach((n) => {
      const to = Number(n.dataset.to);
      const o = { v: 0 };
      n.textContent = '0';
      gsap.to(o, { v: to, duration: 1.4, ease: 'expo.out', scrollTrigger: { trigger: n, start: 'top 85%', once: true }, onUpdate: () => { n.textContent = Math.round(o.v); } });
    });
  }
}

export function initSections(ctx = {}) {
  initStateMachine();
  initRedaction();
  initApproval(ctx);
  initEvaluation(ctx);
}
