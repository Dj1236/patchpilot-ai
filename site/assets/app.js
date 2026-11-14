// PatchPilot AI landing page. Static, scripted data only: no network requests, no API keys.
(() => {
  'use strict';

  const root = document.documentElement;
  root.classList.add('js');
  const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const still = () => motion.matches;
  const $ = (selector, scope = document) => scope.querySelector(selector);
  const $$ = (selector, scope = document) => Array.from(scope.querySelectorAll(selector));
  const esc = (text) => String(text).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const SVG = 'http://www.w3.org/2000/svg';

  /* ---------- section reveal and nav state ---------- */
  const sections = $$('[data-reveal]');
  if ('IntersectionObserver' in window) {
    const reveal = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) { entry.target.classList.add('in'); reveal.unobserve(entry.target); }
      }
    }, { rootMargin: '0px 0px -12% 0px', threshold: 0.05 });
    sections.forEach((s) => reveal.observe(s));

    const links = new Map($$('.nav nav a').map((a) => [a.getAttribute('href').slice(1), a]));
    const spy = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        const link = links.get(entry.target.id);
        if (link && entry.isIntersecting) {
          links.forEach((a) => a.removeAttribute('aria-current'));
          link.setAttribute('aria-current', 'true');
        }
      }
    }, { rootMargin: '-45% 0px -50% 0px' });
    $$('section[id]').forEach((s) => spy.observe(s));
  } else {
    sections.forEach((s) => s.classList.add('in'));
  }

  /* ---------- hero console ---------- */
  const heroConsole = $('#hero-console');
  let heroTimers = [];
  function playConsole() {
    heroTimers.forEach(clearTimeout);
    heroTimers = [];
    const lines = $$('li', heroConsole);
    if (still()) { heroConsole.classList.remove('typing'); return; }
    heroConsole.classList.add('typing');
    lines.forEach((li) => li.classList.remove('on'));
    const gaps = [0, 450, 900, 1500, 1900, 2500, 3300, 3900, 4500, 5300];
    lines.forEach((li, i) => heroTimers.push(setTimeout(() => li.classList.add('on'), 250 + (gaps[i] ?? i * 500))));
  }
  playConsole();
  $('#hero-replay').addEventListener('click', playConsole);

  /* ---------- architecture ---------- */
  const STATE = {
    verified: ['tag-ok', 'verified locally'],
    scripted: ['tag-sim', 'scripted model only'],
    fake: ['tag-sim', 'fake GitHub only'],
  };
  const ZONES = {
    host: ['var(--z-host)', 'Host · trusted'],
    sandbox: ['var(--z-sandbox)', 'Sandbox · untrusted'],
    eval: ['var(--z-eval)', 'Evaluator · hidden'],
    ext: ['var(--z-ext)', 'External'],
  };
  const NODES = {
    intake: {
      t: 'Issue intake', m: 'allowlist first', z: 'host', state: 'verified', mod: 'src/patchpilot/intake/',
      rule: 'The allowlist is checked before any network request, and the repository id must match the entry.',
      pts: ['Remote URLs come only from the allowlist entry, never from issue text', 'Issue text is held as untrusted text and shown to the model in labelled blocks', 'Evaluation runs use local fixture tasks of the same shape'],
      ev: ['tests/unit/test_intake_allowlist.py', 'tests/integration/test_intake_pipeline.py'],
    },
    snapshot: {
      t: 'Pinned snapshot', m: 'no checkout', z: 'host', state: 'verified', mod: 'src/patchpilot/repo/',
      rule: 'The commit is pinned and fetched into a bare repository with a fixed configuration. It is never checked out on the host.',
      pts: ['Files are read from git objects into an in-memory snapshot', 'Hooks, submodules, LFS and filters cannot run', 'Size limit per repository'],
      ev: ['tests/integration/test_repo_fetch.py', 'tests/integration/test_git_task.py'],
    },
    tools: {
      t: 'Read-only tools', m: '4 tools, no shell', z: 'host', state: 'verified', mod: 'src/patchpilot/repo/tools.py',
      rule: 'The model gets four tools and no shell: list_files, read_file, search_code and outline.',
      pts: ['Paths are resolved and confined in code; symlinks and special entries are skipped', 'outline parses Python with ast; target code is never imported', 'Budgets: 60 tool calls, 32,000 bytes per call, 600,000 bytes in all'],
      ev: ['tests/unit/test_repo_tools.py'],
    },
    model: {
      t: 'Model adapter', m: 'no fallback', z: 'host', state: 'scripted', mod: 'src/patchpilot/llm/',
      rule: 'One approved model per process, from a fixed list. No fallback to another model or provider; SDK retries are off.',
      pts: ['Adapters: free-tier Gemini, Anthropic, and the scripted fake that every test uses', 'The reported model identity must match the requested one', 'Every completed run so far used the scripted model: all 8 live Gemini generation requests got HTTP 503'],
      ev: ['tests/unit/test_llm_gemini.py', 'tests/unit/test_model_selection.py'],
    },
    ledger: {
      t: 'Spend ledger', m: 'reserve, settle', z: 'host', state: 'verified', mod: 'src/patchpilot/llm/budget.py',
      rule: 'Every model request is reserved before it is sent and settled after. A failed request is charged in full, because it may have been billed.',
      pts: ['Caps: $0.75 per run and $5.00 across all runs', 'Gemini pacing: at most 4 requests in any 60 seconds, 8 generation attempts in all', 'A resume is refused if the ledger shows work after the checkpoint'],
      ev: ['tests/unit/test_llm_budget.py', 'tests/unit/test_resume.py'],
    },
    proposal: {
      t: 'Proposal + policy', m: 'exact-match edits', z: 'host', state: 'verified', mod: 'src/patchpilot/patch/',
      rule: 'The answer must fit a strict schema. Each edit is a search-and-replace that must match exactly once, and PatchPilot computes the diff, never the model.',
      pts: ['Edits apply all or nothing, always to the original files', 'No edits to existing tests and no new runner config such as conftest.py', 'At most 5 files and 300 changed lines'],
      ev: ['tests/unit/test_patch.py', 'tests/unit/test_baseline.py'],
    },
    feedback: {
      t: 'Repair feedback', m: 'own checks only', z: 'host', state: 'verified', mod: 'src/patchpilot/agent/repair.py',
      rule: 'In repair mode, a rejected proposal is answered with what the agent’s own checks found, and the model tries again.',
      pts: ['Only the proposal’s errors, the patch rules and visible tests; nothing from grading', 'Up to 5 proposals (default 3), each applied to the original files', 'State is checkpointed before every request and after every step'],
      ev: ['tests/unit/test_repair.py', 'tests/integration/test_repair_tasks.py'],
    },
    sandbox: {
      t: 'Sandbox checks', m: '--network none', z: 'sandbox', state: 'verified', mod: 'src/patchpilot/sandbox/',
      rule: 'Candidate code runs only here: a Docker container with no network, a read-only root, a non-root user and no capabilities.',
      pts: ['The new regression test must fail on the original code and pass on the patched code', 'Existing tests must still pass', 'Test commands come from the task manifest, never from model output', 'Attacked in a real container locally (Colima on macOS); not yet on Linux CI'],
      ev: ['tests/integration/test_sandbox_security.py', 'tests/integration/test_sandbox_limits.py'],
    },
    grading: {
      t: 'Hidden tests', m: 'grade_candidate', z: 'eval', state: 'verified', mod: 'src/patchpilot/tasks/',
      rule: 'After the run ends, the tree is rebuilt from the pinned start and the hidden tests are added last. Only this decides resolved.',
      pts: ['Every fail-to-pass test must pass and every pass-to-pass test must keep passing', 'Hidden tests never enter the agent’s files, images or prompts', 'The result goes into the private run record and is never sent back into a run'],
      ev: ['tests/integration/test_fixture_tasks.py', 'tests/integration/test_grading_integrity.py'],
    },
    review: {
      t: 'PR proposal', m: 'every file in full', z: 'host', state: 'verified', mod: 'src/patchpilot/submit/proposal.py',
      rule: 'propose-pr writes exactly what would be sent to GitHub, built from a verified run only, with every changed file shown in full.',
      pts: ['Refused if the diff or a file would have to be cut', 'Refused if a file holds hidden, bidirectional or control characters', 'Refused on secrets, policy breaks or a mismatch with the run record'],
      ev: ['tests/unit/test_submit.py'],
    },
    approval: {
      t: 'Human approval', m: 'type the digest', z: 'host', state: 'verified', mod: 'src/patchpilot/submit/approval.py',
      rule: 'A person types the first 12 characters of the patch digest. The approval is bound to the digest of the whole proposal and expires after 24 hours.',
      pts: ['No code path of the agent can create an approval', 'A different patch, target, base commit or branch is not covered', 'Limit: an unsigned local file; it guards against the model, not a malicious local user'],
      ev: ['tests/unit/test_submit.py'],
    },
    draft: {
      t: 'Draft PR', m: 'drafts only', z: 'ext', state: 'fake', mod: 'src/patchpilot/submit/submission.py',
      rule: 'The writer allows only listed requests to one repository. It reads before every write, creates one branch and one draft pull request, and never merges.',
      pts: ['Classic tokens are refused; a separate fine-grained write token is used', 'A write with an unknown outcome is marked uncertain and never repeated', 'No real GitHub write has been made yet'],
      ev: ['tests/unit/test_submit.py', 'tests/fake_github.py'],
    },
  };
  const EDGES = [
    ['intake', 'snapshot'], ['snapshot', 'tools'], ['tools', 'model'],
    ['model', 'ledger', 'side', 'reserve → settle'], ['model', 'proposal'],
    ['proposal', 'sandbox', '', 'policy ok'], ['sandbox', 'feedback', '', 'not verified'], ['feedback', 'model'],
    ['sandbox', 'grading', '', 'after the run'], ['sandbox', 'review', '', 'verified'],
    ['review', 'approval'], ['approval', 'draft', '', 'approved'],
  ];
  const FLOWS = {
    baseline: {
      path: ['intake', 'snapshot', 'tools', 'model', 'proposal', 'sandbox', 'grading'],
      extra: ['ledger'],
      cap: 'One proposal, no feedback. If the sandbox checks do not hold, the run ends unverified. Grading runs once, after the run.',
    },
    repair: {
      path: ['intake', 'snapshot', 'tools', 'model', 'proposal', 'sandbox', 'feedback', 'model', 'proposal', 'sandbox', 'grading'],
      extra: ['ledger'],
      cap: 'When the checks do not hold, the agent’s own findings go back to the model, for up to 5 proposals (default 3). Grading still runs once, after the run, and never feeds back.',
    },
    submit: {
      path: ['sandbox', 'review', 'approval', 'draft'],
      extra: [],
      cap: 'Only a verified run can become a proposal. A person approves its exact digest, then one draft PR is opened. Tested against a fake GitHub API only.',
    },
  };
  const LAYOUTS = {
    wide: {
      w: 1200, h: 540, nw: 160, nh: 58, labels: true,
      zones: [
        ['host', 'HOST · TRUSTED ORCHESTRATOR', 20, 40, 960, 290],
        ['host', 'HOST · REVIEW', 990, 230, 180, 280],
        ['sandbox', 'SANDBOX · UNTRUSTED', 750, 370, 200, 140],
        ['eval', 'EVALUATOR · HIDDEN', 400, 370, 200, 140],
        ['ext', 'GITHUB', 990, 40, 180, 130],
      ],
      at: {
        intake: [110, 100], snapshot: [295, 100], tools: [480, 100], model: [665, 100], proposal: [850, 100],
        ledger: [480, 245], feedback: [665, 245], sandbox: [850, 448], grading: [500, 448],
        review: [1080, 448], approval: [1080, 300], draft: [1080, 110],
      },
    },
    narrow: {
      w: 420, h: 1000, nw: 210, nh: 54, labels: false,
      zones: [
        ['host', 'HOST · TRUSTED', 10, 20, 400, 460],
        ['sandbox', 'SANDBOX', 10, 500, 250, 120],
        ['eval', 'EVALUATOR', 270, 500, 140, 120],
        ['host', 'HOST · REVIEW', 10, 640, 400, 210],
        ['ext', 'GITHUB', 10, 870, 400, 110],
      ],
      at: {
        intake: [135, 75], snapshot: [135, 160], tools: [135, 245], model: [135, 330], proposal: [135, 425],
        ledger: [330, 330, 140], feedback: [330, 425, 140], sandbox: [135, 568], grading: [340, 568, 126],
        review: [135, 705], approval: [135, 795], draft: [135, 932],
      },
    },
  };

  const svg = $('#arch-svg');
  const detail = $('#arch-detail');
  const flowCap = $('#arch-flowcap');
  const stage = $('.arch-stage');
  let layoutName = '';
  let layout = LAYOUTS.wide;
  let flow = 'repair';
  let selected = 'sandbox';
  let packet = null;
  let archVisible = false;
  let rafId = 0;

  const center = (id) => layout.at[id];
  const edgeId = (a, b) => `e-${a}-${b}`;
  const findEdge = (a, b) => EDGES.find(([x, y]) => (x === a && y === b) || (x === b && y === a));

  function renderArch() {
    const want = stage.clientWidth < 700 ? 'narrow' : 'wide';
    if (want === layoutName) return;
    layoutName = want;
    layout = LAYOUTS[want];
    svg.setAttribute('viewBox', `0 0 ${layout.w} ${layout.h}`);
    svg.style.maxWidth = want === 'narrow' ? '520px' : '';
    let out = '';
    for (const [zone, label, x, y, w, h] of layout.zones) {
      out += `<rect class="zbox zbox-${zone}" x="${x}" y="${y}" width="${w}" height="${h}" rx="12"/>`;
    }
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
      out += `<g class="node" id="n-${id}" data-node="${id}" tabindex="0" role="button" aria-label="${esc(node.t)}: ${esc(ZONES[node.z][1])}. Show details.">`
        + `<rect class="ring" x="${x - 4}" y="${y - 4}" width="${w + 8}" height="${h + 8}" rx="12"/>`
        + `<rect class="nb" x="${x}" y="${y}" width="${w}" height="${h}" rx="9"/>`
        + `<rect x="${x}" y="${y + 12}" width="2.5" height="${h - 24}" rx="1" fill="${ZONES[node.z][0]}"/>`
        + `<text class="nt" x="${x + 14}" y="${cy - 3}">${esc(node.t)}</text>`
        + `<text class="nm" x="${x + 14}" y="${cy + 14}">${esc(node.m)}</text>`
        + '</g>';
    }
    out += '<g id="packet" opacity="0"><circle class="packet-halo" r="9"/><circle class="packet" r="4"/></g>';
    svg.innerHTML = out;
    packet = $('#packet', svg);
    $$('.node', svg).forEach((g) => {
      g.addEventListener('click', () => select(g.dataset.node));
      g.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(g.dataset.node); }
      });
    });
    applyFlow();
    markSelected();
  }

  function applyFlow() {
    const f = FLOWS[flow];
    const nodes = new Set([...f.path, ...f.extra]);
    const edges = new Set();
    for (let i = 1; i < f.path.length; i += 1) {
      const e = findEdge(f.path[i - 1], f.path[i]);
      if (e) edges.add(edgeId(e[0], e[1]));
    }
    if (f.extra.includes('ledger')) edges.add(edgeId('model', 'ledger'));
    $$('.edge', svg).forEach((p) => {
      p.classList.toggle('on', edges.has(p.id));
      p.classList.toggle('dim', !edges.has(p.id));
    });
    $$('.edge-label', svg).forEach((t) => t.classList.toggle('on', edges.has(t.dataset.edge)));
    $$('.node', svg).forEach((g) => {
      g.classList.toggle('lit', nodes.has(g.dataset.node));
      g.classList.toggle('dim', !nodes.has(g.dataset.node));
    });
    $$('.seg').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.flow === flow)));
    const name = { baseline: 'Baseline', repair: 'Repair loop', submit: 'Submission' }[flow];
    flowCap.innerHTML = `<b>${name}</b>${esc(f.cap)}`;
    restartPacket();
  }

  function markSelected() {
    $$('.node', svg).forEach((g) => g.classList.toggle('sel', g.dataset.node === selected));
  }

  function select(id) {
    selected = id;
    markSelected();
    const n = NODES[id];
    const [cls, label] = STATE[n.state];
    detail.style.animation = 'none'; void detail.offsetWidth; detail.style.animation = '';
    detail.innerHTML = `
      <div class="ad-head">
        <span class="ad-zone" style="color:${ZONES[n.z][0]}"><i class="lg" style="border-color:${ZONES[n.z][0]}"></i>${esc(ZONES[n.z][1])}</span>
        <span class="tag ${cls}">${label}</span>
      </div>
      <h3 class="ad-title">${esc(n.t)}</h3>
      <p class="ad-mod">${esc(n.mod)}</p>
      <p class="ad-rule">${esc(n.rule)}</p>
      <ul class="ad-list">${n.pts.map((p) => `<li>${esc(p)}</li>`).join('')}</ul>
      <p class="ad-ev">Evidence: ${n.ev.map((p) => `<code>${esc(p)}</code>`).join(' ')}</p>`;
  }

  /* packet travelling along the selected flow */
  function restartPacket() {
    cancelAnimationFrame(rafId);
    if (!packet) return;
    if (still() || !archVisible || document.hidden) { packet.setAttribute('opacity', '0'); return; }
    const pts = FLOWS[flow].path.map(center);
    const lengths = pts.slice(1).map((p, i) => Math.hypot(p[0] - pts[i][0], p[1] - pts[i][1]));
    const speed = layoutName === 'narrow' ? 0.32 : 0.42; // viewBox units per ms
    const dwell = 380;
    const plan = [];
    let t = 0;
    lengths.forEach((len, i) => {
      plan.push({ from: pts[i], to: pts[i + 1], start: t, end: t + len / speed, arrive: FLOWS[flow].path[i + 1] });
      t += len / speed + dwell;
    });
    const total = t + 900;
    const startAt = performance.now();
    let lastArrive = '';
    packet.setAttribute('opacity', '1');
    const tick = (now) => {
      const el = (now - startAt) % total;
      let pos = pts[pts.length - 1];
      let arrive = '';
      for (const seg of plan) {
        if (el < seg.start) break;
        if (el <= seg.end) {
          const k = (el - seg.start) / (seg.end - seg.start);
          const e = k < 0.5 ? 2 * k * k : 1 - ((-2 * k + 2) ** 2) / 2;
          pos = [seg.from[0] + (seg.to[0] - seg.from[0]) * e, seg.from[1] + (seg.to[1] - seg.from[1]) * e];
          arrive = '';
          break;
        }
        pos = seg.to;
        arrive = el <= seg.end + dwell ? seg.arrive : '';
      }
      if (el < plan[0].start + 1 && !arrive) arrive = FLOWS[flow].path[0];
      packet.setAttribute('transform', `translate(${pos[0]} ${pos[1]})`);
      packet.setAttribute('opacity', el > t ? '0' : '1');
      if (arrive !== lastArrive) {
        if (lastArrive) $(`#n-${lastArrive}`, svg)?.classList.remove('pulse');
        if (arrive) $(`#n-${arrive}`, svg)?.classList.add('pulse');
        lastArrive = arrive;
      }
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
  }

  $$('.seg').forEach((b) => b.addEventListener('click', () => { flow = b.dataset.flow; applyFlow(); }));
  renderArch();
  select(selected);
  let resizeTimer = 0;
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(renderArch, 120); });
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(([entry]) => { archVisible = entry.isIntersecting; restartPacket(); }, { threshold: 0.2 }).observe(stage);
  }
  document.addEventListener('visibilitychange', restartPacket);
  motion.addEventListener?.('change', () => { restartPacket(); playConsole(); });

  /* ---------- scripted replay ---------- */
  const LRU_CONTEXT_BEFORE = [
    '@@ -62,6 +62,7 @@',
    '         expires_at = None if self._ttl is None else self._clock() + self._ttl',
    '         if key in self._entries:',
    '             self._entries[key] = (value, expires_at)',
  ];
  const LRU_CONTEXT_AFTER = [
    '             return',
    '         self._purge_expired()',
    '         while len(self._entries) >= self._capacity:',
  ];
  const TEST_FILE = [
    '@@ -0,0 +1,10 @@',
    '+from cachekit import LRUCache',
    '+',
    '+',
    '+def test_rewritten_key_survives_eviction():',
    '+    cache = LRUCache(capacity=2)',
    "+    cache.put('a', 1)",
    "+    cache.put('b', 2)",
    "+    cache.put('a', 3)",
    "+    cache.put('c', 4)",
    "+    assert 'a' in cache",
  ];
  const lruDiff = (added) => [...LRU_CONTEXT_BEFORE, `+            ${added}`, ...LRU_CONTEXT_AFTER];
  const FEEDBACK = [
    'Proposal 1 was not accepted: the checks in the sandbox did not hold.',
    '',
    '<untrusted_check_result>',
    'regression tests on the patched code: expected that all tests pass, but the result was failed',
    '- regression tests on the original code: expected that at least one test fails; result failed; held',
    '- regression tests on the patched code: expected that all tests pass; result failed; did not hold',
    '</untrusted_check_result>',
    '<untrusted_test_output>',
    'tests/test_rewrite_recency.py::test_rewritten_key_survives_eviction',
    "AssertionError: assert 'a' in <cachekit.lru.LRUCache object at 0x...>",
    '</untrusted_test_output>',
    '',
    'The snapshot is unchanged: your edits were discarded. You have 2 proposals left. Look at the code again if you need to, then answer with a new, complete proposal.',
  ];
  const ISSUE = [
    'Frequently updated cache entry gets evicted',
    '',
    'We use LRUCache to hold the latest reading per sensor. One sensor reports far',
    'more often than the others, so its entry is rewritten constantly with put().',
    'Even so, that entry keeps disappearing from the cache as soon as a couple of',
    'other sensors report, and we fall back to the slow path for our busiest key.',
    '',
    'The class docstring says that reading or writing an entry counts as using it,',
    'so I would expect an entry that was just written to be the last one evicted.',
  ];

  function codeBlock(lines, { head = '', kind = 'diff', files = null } = {}) {
    const cls = (line) => {
      if (kind === 'diff') {
        if (line.startsWith('@@')) return 'ln-hunk';
        if (line.startsWith('+')) return 'ln-add';
        if (line.startsWith('-')) return 'ln-del';
        return '';
      }
      if (kind === 'msg') return /^<\/?untrusted/.test(line) ? 'ln-u' : (line.startsWith('Proposal') ? 'ln-b' : '');
      return '';
    };
    const body = lines.map((l) => `<span class="${cls(l)}">${esc(l) || ' '}</span>`).join('');
    const tabs = files
      ? `<span class="tabs-mini" role="group" aria-label="Files in this proposal">${files.map((f, i) => `<button type="button" data-file="${i}" aria-pressed="${i === 0}">${esc(f.name)}</button>`).join('')}</span>`
      : `<span>${esc(head)}</span>`;
    return `<div class="code${kind === 'diff' ? '' : ' wrap'}"><div class="code-h">${tabs}<span>${files ? 'computed by PatchPilot' : ''}</span></div><pre>${body}</pre></div>`;
  }
  const diffFiles = (added) => [
    { name: 'src/cachekit/lru.py', lines: lruDiff(added) },
    { name: 'tests/test_rewrite_recency.py', lines: TEST_FILE },
  ];
  const checksTable = (rows) => `<table class="checks"><thead><tr><th>Check</th><th>Expected</th><th>Result</th><th>Holds</th></tr></thead><tbody>${
    rows.map(([c, e, r, ok]) => `<tr><td>${c}</td><td>${e}</td><td>${r}</td><td class="${ok ? 'held' : 'nothold'}">${ok ? '✓ yes' : '✗ no'}</td></tr>`).join('')
  }</tbody></table>`;

  const STEPS = [
    {
      phase: 'intake', label: 'Bug report', sub: 'untrusted text',
      title: 'The bug report',
      html: () => `<p>A synthetic library with an injected bug. The agent sees this issue and the repository snapshot. The hidden tests and the reference fix stay with the evaluator.</p>${codeBlock(ISSUE, { head: 'issue · inj-001-cachekit · shown to the model as untrusted text', kind: 'text' })}`,
    },
    {
      phase: 'proposal', label: 'Proposal 1', sub: 'plausible, wrong',
      title: 'Proposal 1: a plausible wrong fix',
      html: () => `<p>The scripted answer edits the right branch of <code>put()</code> but does not change recency. Edits are search-and-replace and must match exactly once; PatchPilot applies them to the original files and computes the diff itself.</p>${codeBlock([], { files: diffFiles('self.hits += 0') })}`,
      files: () => diffFiles('self.hits += 0'),
    },
    {
      phase: 'sandbox', label: 'Sandbox checks', sub: 'did not hold', bad: true,
      title: 'Sandbox: the checks do not hold',
      html: () => `<p>Policy passed, so both versions run in the locked-down container. The new test fails before the patch, as it should, but still fails after it.</p>${checksTable([
        ['regression tests on the original code', 'at least one test fails', 'failed', true],
        ['regression tests on the patched code', 'all tests pass', 'failed', false],
      ])}${codeBlock(['tests/test_rewrite_recency.py::test_rewritten_key_survives_eviction', "AssertionError: assert 'a' in <cachekit.lru.LRUCache object at 0x...>"], { head: 'visible test output', kind: 'text' })}
      <p class="rp-note warn">In <b>baseline</b> mode the run ends here as <code>unverified</code>. Hidden tests confirm it later: 0 of 3 fail-to-pass.</p>`,
    },
    {
      phase: 'feedback', label: 'Feedback', sub: 'own checks only',
      title: 'Feedback from the agent’s own checks',
      html: () => `<p>In <b>repair</b> mode the model is told what its own checks found, inside labelled untrusted blocks. Nothing from the hidden tests exists at this point. This is the exact message from the recorded run:</p>${codeBlock(FEEDBACK, { head: 'message to the model · request 2', kind: 'msg' })}`,
    },
    {
      phase: 'proposal', label: 'Proposal 2', sub: 'the fix',
      title: 'Proposal 2: the fix',
      html: () => `<p>A complete new proposal, applied to the original files again, not on top of proposal 1. One line: rewriting a key now marks it as most recently used.</p>${codeBlock([], { files: diffFiles('self._entries.move_to_end(key)') })}`,
      files: () => diffFiles('self._entries.move_to_end(key)'),
    },
    {
      phase: 'sandbox', label: 'Sandbox checks', sub: 'candidate_verified',
      title: 'Sandbox: candidate_verified',
      html: () => `<p>All three checks hold.</p>${checksTable([
        ['regression tests on the original code', 'at least one test fails', 'failed', true],
        ['regression tests on the patched code', 'all tests pass', 'passed', true],
        ['existing tests on the patched code', 'all tests pass', 'passed', true],
      ])}<p class="rp-note ok"><code>candidate_verified</code> is the agent’s own evidence: the same agent wrote the fix and the test. It is not <code>resolved</code>.</p>`,
    },
    {
      phase: 'evaluator', label: 'Independent grading', sub: 'resolved',
      title: 'Independent grading: resolved',
      html: () => `<p><code>grade_candidate</code> runs after the run has ended. It rebuilds the tree from the pinned start, adds the hidden tests last, and runs them in a fresh sandbox.</p>
      <dl class="score"><div><dt>Fail-to-pass</dt><dd class="okv">3/3</dd></div><div><dt>Pass-to-pass</dt><dd class="okv">14/14</dd></div><div><dt>Proposals</dt><dd>2 <small>of at most 3</small></dd></div></dl>
      <div class="flowline" aria-label="Run summary"><span>2 model turns</span><span>0 tool calls</span><span class="hl">6 sandbox runs, grading included</span><span>status: resolved</span></div>
      <p class="rp-note vio">The grading result goes into the private run record. It is never fed back to the model.</p>`,
    },
  ];

  const stepList = $('#rp-steps');
  const rpTitle = $('#rp-title');
  const rpBody = $('#rp-body');
  const rpKicker = $('#rp-kicker');
  const rpBar = $('#rp-bar');
  const rpPlay = $('#rp-play');
  let step = 0;
  let playTimer = 0;

  stepList.innerHTML = STEPS.map((s, i) => `<li class="rp-step${s.bad ? ' badstep' : ''}"><button type="button" data-step="${i}"><span class="n">${i + 1}</span><span class="l">${esc(s.label)}<span class="s">${esc(s.sub)}</span></span></button></li>`).join('');

  function showStep(i, { focus = false } = {}) {
    step = Math.max(0, Math.min(STEPS.length - 1, i));
    const s = STEPS[step];
    rpKicker.textContent = `Step ${step + 1} of ${STEPS.length} · ${s.phase}`;
    rpTitle.textContent = s.title;
    rpBody.innerHTML = s.html();
    rpBody.style.animation = 'none';
    void rpBody.offsetWidth; // restart the entrance animation
    rpBody.style.animation = '';
    rpBar.style.width = `${((step + 1) / STEPS.length) * 100}%`;
    $$('.rp-step', stepList).forEach((li, k) => {
      li.classList.toggle('cur', k === step);
      li.classList.toggle('done', k < step);
      const b = $('button', li);
      if (k === step) b.setAttribute('aria-current', 'step'); else b.removeAttribute('aria-current');
    });
    $('#rp-prev').disabled = step === 0;
    $('#rp-next').disabled = step === STEPS.length - 1;
    const cur = $$('.rp-step', stepList)[step];
    if (stepList.scrollWidth > stepList.clientWidth) {
      stepList.scrollTo({ left: cur.offsetLeft - 10, behavior: still() ? 'auto' : 'smooth' });
    }
    if (s.files) wireFileTabs(s.files());
    if (focus) rpTitle.focus({ preventScroll: true });
  }

  function wireFileTabs(files) {
    const block = $('.code', rpBody);
    const pre = $('pre', block);
    $$('.tabs-mini button', block).forEach((b) => b.addEventListener('click', () => {
      $$('.tabs-mini button', block).forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
      const lines = files[Number(b.dataset.file)].lines;
      pre.innerHTML = lines.map((l) => {
        const c = l.startsWith('@@') ? 'ln-hunk' : l.startsWith('+') ? 'ln-add' : '';
        return `<span class="${c}">${esc(l) || ' '}</span>`;
      }).join('');
    }));
    $$('.tabs-mini button', block)[0].click();
  }

  function stopPlay() {
    clearInterval(playTimer);
    playTimer = 0;
    rpPlay.textContent = '▶';
    rpPlay.setAttribute('aria-label', 'Play replay');
  }
  function startPlay() {
    if (step === STEPS.length - 1) showStep(0);
    rpPlay.textContent = '❚❚';
    rpPlay.setAttribute('aria-label', 'Pause replay');
    playTimer = setInterval(() => {
      if (step >= STEPS.length - 1) { stopPlay(); return; }
      showStep(step + 1);
    }, 5200);
  }
  rpPlay.addEventListener('click', () => (playTimer ? stopPlay() : startPlay()));
  $('#rp-prev').addEventListener('click', () => { stopPlay(); showStep(step - 1); });
  $('#rp-next').addEventListener('click', () => { stopPlay(); showStep(step + 1); });
  stepList.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-step]');
    if (b) { stopPlay(); showStep(Number(b.dataset.step), { focus: true }); }
  });
  $('.replay').addEventListener('keydown', (e) => {
    if (e.target.matches('input, textarea')) return;
    if (e.key === 'ArrowRight') { stopPlay(); showStep(step + 1); e.preventDefault(); }
    if (e.key === 'ArrowLeft') { stopPlay(); showStep(step - 1); e.preventDefault(); }
  });
  showStep(0);

  /* ---------- engineering deep dive ---------- */
  const TOPICS = [
    {
      id: 'sandbox', tab: 'Sandbox isolation', sub: 'docker, no network',
      html: `<h3>Sandbox isolation</h3>
      <p>Repository code, including the candidate’s tests, runs only inside a container started with these flags. The host never imports or executes it.</p>
      <div class="term"><span class="c"># src/patchpilot/sandbox/docker.py · build_run_argv (abridged)</span>
docker run --detach --rm --pull never --init \\
  <span class="f">--network none</span> \\
  <span class="f">--user 10001:10001 --cap-drop ALL</span> \\
  <span class="f">--security-opt no-new-privileges:true --read-only</span> \\
  --tmpfs /workspace:rw,nosuid,nodev,noexec,size=64m \\
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m \\
  --memory 512m --memory-swap 512m --cpus 1 --pids-limit 128 \\
  --ulimit core=0 --ulimit nofile=1024:1024 --ipc private \\
  --stop-signal KILL &lt;pinned image&gt; sleep</div>
      <div class="dd-cols">
        <dl class="kv"><dt>timeout</dt><dd>60 s per run</dd><dt>output</dt><dd>capped at 64 KiB</dd><dt>commands</dt><dd>from the task manifest only</dd><dt>cleanup</dt><dd>process group killed, container removal checked</dd></dl>
        <ul class="bul"><li>Security tests attack a real container: network, writes outside the workspace, privilege, resource limits, leftover processes</li><li class="lim">The container shares the host kernel</li><li class="lim">Tests can forge their own results in the same process; kept as a known, expected failure</li></ul>
      </div>`,
    },
    {
      id: 'budgets', tab: 'Bounded budgets', sub: 'turns, tokens, money',
      html: `<h3>Bounded agent budgets</h3>
      <p>Every loop has a ceiling, enforced outside the model. A run that hits one ends with its own status; nothing is retried silently.</p>
      <div class="dd-cols">
        <dl class="kv"><dt>model turns</dt><dd>20</dd><dt>output tokens</dt><dd>16,000 per turn</dd><dt>tool calls</dt><dd>60</dd><dt>tool output</dt><dd>32,000 bytes per call, 600,000 in all</dd><dt>proposals</dt><dd>3 by default, 5 at most</dd><dt>repair tokens</dt><dd>400,000</dd><dt>wall time</dt><dd>1,200 s</dd></dl>
        <dl class="kv"><dt>per run</dt><dd>$0.75 ledger cap</dd><dt>all runs</dt><dd>$5.00 ledger cap</dd><dt>pacing</dt><dd>4 Gemini requests per 60 s</dd><dt>attempts</dt><dd>8 Gemini generation attempts in all</dd><dt>retries</dt><dd>SDK retries off; one bounded retry on 5xx; quota errors not retried</dd></dl>
      </div>
      <ul class="bul" style="margin-top:18px"><li>Each request is reserved in the ledger before it is sent and settled after; a failed request is charged in full</li><li>One approved model per process, never a fallback after a failure</li></ul>`,
    },
    {
      id: 'checkpoint', tab: 'Checkpoint / resume', sub: 'JSON state machine',
      html: `<h3>Checkpoints and resume</h3>
      <p>The repair loop is plain functions over a small JSON state machine. State is saved before every model request and after every step, so a stopped run can continue without repeating a request whose outcome is unknown.</p>
      <div class="sm" aria-label="Repair states"><span>propose</span><i>→</i><span>evaluate</span><i>→</i><span>feedback</span><i>→</i><span>propose</span><i>…</i><span>done</span></div>
      <div class="dd-cols">
        <dl class="kv"><dt>format</dt><dd>JSON with a schema version</dd><dt>integrity</dt><dd>SHA-256 over the state</dd><dt>write</dt><dd>atomic replace and fsync</dd><dt>size</dt><dd>16 MB at most</dd></dl>
        <ul class="bul"><li>Resume cross-checks the spend ledger and is refused if any request happened after the checkpoint</li><li>An in-flight or stale checkpoint fails closed</li><li>Limits and counters are restored, so restarting cannot reset a budget</li><li class="lim">Resume is offline only for now, replaying recorded answers</li></ul>
      </div>
      <div class="term">uv run patchpilot run --task inj-001-cachekit --replay &lt;trace&gt; --mode repair --interrupt-after 2
uv run patchpilot resume &lt;run dir&gt; --replay &lt;trace&gt;</div>`,
    },
    {
      id: 'tools', tab: 'Tool calling', sub: 'four read-only tools',
      html: `<h3>Tool calling</h3>
      <p>The model explores the snapshot through four read-only tools and answers with one structured proposal. It never gets a shell, and every argument is checked in code.</p>
      <div class="dd-cols">
        <dl class="kv"><dt>list_files</dt><dd>files in the snapshot, optionally under a prefix</dd><dt>read_file</dt><dd>a range of lines from one text file</dd><dt>search_code</dt><dd>lines containing a literal text, not a regex</dd><dt>outline</dt><dd>classes, functions and methods, parsed with <code>ast</code></dd></dl>
        <ul class="bul"><li>Paths are resolved and confined to the snapshot; symlinks and special entries are skipped</li><li>Per-call and total output budgets</li><li>In the <code>probe-hidden-then-right</code> scenario, calls aimed at hidden files get errors, nothing hidden is returned, and the run continues</li></ul>
      </div>
      <div class="term"><span class="c"># scripted probe from evals: every call is refused or finds nothing</span>
list_files  {"prefix": "internal"}
read_file   {"path": "../internal/hidden_tests/README"}
search_code {"query": "fail_to_pass"}</div>`,
    },
    {
      id: 'security', tab: 'Security', sub: 'untrusted by default',
      html: `<h3>Security model</h3>
      <p>The design assumes the model can be manipulated and limits what a manipulated model can do. None of this makes prompt injection or sandbox escape impossible.</p>
      <div class="dd-cols">
        <ul class="bul"><li>Issue text, repository content and test output stay untrusted text, shown in labelled blocks</li><li>Known secret values are redacted from traces, reports, checkpoints and proposals; canary tests check it</li><li>Vertex AI, enterprise mode and a second key variable are refused</li><li>Hidden tests never enter the agent’s files, images or prompts; tests check every message sent</li></ul>
        <ul class="bul"><li>Raw traces stay local; only sanitized <code>report.md</code> files are shared</li><li>Nothing is built into a command, path or URL from issue or file content</li><li class="lim">Labelling untrusted text helps the model; it is not a control</li><li class="lim">Free-tier Gemini may use prompts for product improvement, so only synthetic or approved public code is sent</li></ul>
      </div>`,
    },
    {
      id: 'approval', tab: 'Approval gate', sub: 'try it',
      html: `<h3>Approval-gated pull requests</h3>
      <p>Only <code>patchpilot approve</code> creates an approval, and only when a person types the start of the proposal’s patch digest. Try the same check below. It runs only in your browser and sends nothing.</p>
      <div class="approve">
        <div class="approve-h"><p>PATCH DIGEST · SHA-256 · example value, not from a real proposal</p><span class="tag tag-sim">demo</span></div>
        <p class="digest" id="ap-digest"></p>
        <label for="ap-input">Type the first 12 characters of the digest to approve</label>
        <div class="approve-row"><input id="ap-input" autocomplete="off" spellcheck="false" maxlength="64" placeholder="e.g. 3f9c…"><button type="button" class="btn btn-ghost" id="ap-go">Approve</button></div>
        <p class="approve-out" id="ap-out" aria-live="polite"></p>
      </div>
      <ul class="bul" style="margin-top:18px"><li>Bound to the digest of the whole proposal, plus repository, issue, base commit, branch and operation</li><li>Expires after 24 hours; <code>submit</code> refuses before any request without a valid approval</li><li>Drafts only, one allowlisted repository, never a merge or force push</li><li class="lim">Verified against a local fake GitHub API only; no real GitHub write has been made</li></ul>`,
    },
  ];
  const tabs = $('#dd-tabs');
  const panels = $('#dd-panels');
  tabs.innerHTML = TOPICS.map((t, i) => `<button type="button" role="tab" class="dd-tab" id="tab-${t.id}" aria-controls="panel-${t.id}" aria-selected="${i === 0}" tabindex="${i === 0 ? 0 : -1}">${t.tab}<span>${t.sub}</span></button>`).join('');
  panels.innerHTML = TOPICS.map((t, i) => `<div class="dd-panel" role="tabpanel" id="panel-${t.id}" aria-labelledby="tab-${t.id}" tabindex="0"${i === 0 ? '' : ' hidden'}>${t.html}</div>`).join('');
  const tabButtons = $$('.dd-tab', tabs);
  function openTab(btn, focus) {
    tabButtons.forEach((b) => {
      const on = b === btn;
      b.setAttribute('aria-selected', String(on));
      b.tabIndex = on ? 0 : -1;
      $(`#${b.getAttribute('aria-controls')}`).hidden = !on;
    });
    if (focus) btn.focus();
  }
  tabButtons.forEach((b, i) => {
    b.addEventListener('click', () => openTab(b, false));
    b.addEventListener('keydown', (e) => {
      const keys = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 };
      if (e.key in keys) { e.preventDefault(); openTab(tabButtons[(i + keys[e.key] + tabButtons.length) % tabButtons.length], true); }
      if (e.key === 'Home') { e.preventDefault(); openTab(tabButtons[0], true); }
      if (e.key === 'End') { e.preventDefault(); openTab(tabButtons[tabButtons.length - 1], true); }
    });
  });

  /* approval demo: the same rule as approve() in src/patchpilot/submit/approval.py */
  const DIGEST = '3f9c2a7be1d40c86a5e2f71b9d03c4e8a6f15b27c9e0d3a4b8f26e1c7d5a9b03';
  const CONFIRM_CHARS = 12;
  $('#ap-digest').innerHTML = `<mark>${DIGEST.slice(0, CONFIRM_CHARS)}</mark>${DIGEST.slice(CONFIRM_CHARS)}`;
  const apInput = $('#ap-input');
  const apOut = $('#ap-out');
  function tryApprove() {
    const typed = apInput.value.trim().toLowerCase();
    if (typed === DIGEST.slice(0, CONFIRM_CHARS)) {
      apOut.className = 'approve-out ok';
      apOut.textContent = 'Approved (demo). A real approval is written to approval.json, bound to this exact proposal, and expires in 24 hours.';
    } else {
      apOut.className = 'approve-out bad';
      apOut.textContent = 'the confirmation does not match the patch digest; nothing approved';
    }
  }
  $('#ap-go').addEventListener('click', tryApprove);
  apInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') tryApprove(); });

  /* ---------- evaluation ---------- */
  const data = window.PP_BENCHMARK;
  const SCENARIOS = {
    'right-first': 'The reference fix and a regression test',
    'wrong-then-right': 'A plausible wrong fix, then the reference fix',
    'policy-then-right': 'The fix plus a forbidden conftest.py, then the fix alone',
    'probe-hidden-then-right': 'Tool calls aimed at hidden files, then the fix',
    'no-usable-answer': 'Text that is not a proposal, every time',
  };
  const STATUS_TEXT = {
    candidate_verified: 'candidate_verified',
    unverified: 'unverified',
    rejected_policy: 'rejected by policy',
    invalid_proposal: 'no usable proposal',
  };
  if (data) {
    const modes = ['baseline', 'repair'];
    $('#ev-totals').innerHTML = modes.map((mode) => {
      const s = data.summary[mode];
      const rows = data.rows.filter((r) => r.mode === mode).sort((x, y) => Number(y.resolved) - Number(x.resolved));
      const bar = rows.map((r, i) => `<i class="${r.resolved ? 'r' : ''}" style="transition-delay:${i * 25}ms"></i>`).join('');
      return `<article class="ev-mode"><h3>${mode === 'baseline' ? 'Baseline' : 'Repair'}<span>${mode === 'baseline' ? 'one proposal' : 'up to 3 proposals'}</span></h3>
        <p class="ev-big">${s.resolved_by_hidden_tests}<small> / ${s.runs} resolved by hidden tests</small></p>
        <div class="ev-bar" aria-hidden="true">${bar}</div>
        <p class="ev-sub"><span><b>${s.verified_by_agent}</b> verified by the agent</span><span><b>${s.proposals}</b> proposals</span><span><b>${s.sandbox_runs_including_grading}</b> sandbox runs</span></p></article>`;
    }).join('');

    const table = $('#ev-matrix');
    const order = Object.keys(SCENARIOS);
    let html = '<thead><tr><th scope="col">Scenario</th><th scope="col">Baseline</th><th scope="col"></th><th scope="col">Repair</th><th scope="col"></th></tr></thead><tbody>';
    for (const scenario of order) {
      html += `<tr><th scope="row">${esc(scenario)}<small>${esc(SCENARIOS[scenario])}</small></th>`;
      for (const mode of modes) {
        const runs = data.rows.filter((r) => r.scenario === scenario && r.mode === mode);
        const resolved = runs.filter((r) => r.resolved).length;
        const cells = runs.map((r) => {
          const kind = r.resolved ? 'resolved' : (STATUS_TEXT[r.status] && r.status !== 'candidate_verified' ? r.status : 'unverified');
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
  }
})();
