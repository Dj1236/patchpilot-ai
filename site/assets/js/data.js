// Static, sanitized content for the interactive parts of the page.
// Facts come from the repository: module paths, defaults in code, and one
// recorded offline scripted run (inj-001-cachekit, wrong-then-right, repair mode).

export const ZONES = {
  host: ['var(--zone-host)', 'Host · trusted'],
  sandbox: ['var(--zone-sandbox)', 'Sandbox · untrusted'],
  eval: ['var(--zone-eval)', 'Evaluator · hidden'],
  ext: ['var(--zone-ext)', 'External'],
};

export const STATE = {
  verified: ['tag-ok', 'verified locally'],
  github: ['tag-ok', 'validated on GitHub'],
  live: ['tag-ok', 'one live run'],
};

export const NODES = {
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
    t: 'Model adapter', m: 'no fallback', z: 'host', state: 'live', mod: 'src/patchpilot/llm/',
    rule: 'One approved model per process, from a fixed list. No fallback to another model or provider; SDK retries are off.',
    pts: ['Adapters: free-tier Gemini, Anthropic, a local Ollama model, and the scripted fake that every test uses', 'The reported model identity must match the requested one', 'One live repair passed with local qwen3:8b; free-tier Gemini answered every request with HTTP 503'],
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
    t: 'Draft PR', m: 'drafts only', z: 'ext', state: 'github', mod: 'src/patchpilot/submit/submission.py',
    rule: 'The writer allows only listed requests to one repository. It reads before every write, creates one branch and one draft pull request, and never merges.',
    pts: ['Classic tokens are refused; a separate fine-grained write token is used', 'A write with an unknown outcome is marked uncertain and never repeated', 'Validated once: an approved draft PR on a private synthetic repository; a second submission created nothing'],
    ev: ['tests/unit/test_submit.py', 'tests/fake_github.py'],
  },
};

export const EDGES = [
  ['intake', 'snapshot'], ['snapshot', 'tools'], ['tools', 'model'],
  ['model', 'ledger', 'side', 'reserve → settle'], ['model', 'proposal'],
  ['proposal', 'sandbox', '', 'policy ok'], ['sandbox', 'feedback', '', 'not verified'], ['feedback', 'model'],
  ['sandbox', 'grading', '', 'after the run'], ['sandbox', 'review', '', 'verified'],
  ['review', 'approval'], ['approval', 'draft', '', 'approved'],
];

// view: the part of the wide diagram each flow frames (same aspect ratio, so no layout shift)
export const FLOWS = {
  baseline: {
    name: 'Baseline', path: ['intake', 'snapshot', 'tools', 'model', 'proposal', 'sandbox', 'grading'], extra: ['ledger'],
    zones: ['host', 'sandbox', 'eval'], view: [0, 24, 1060, 477],
    cap: 'One proposal, no feedback. If the sandbox checks do not hold, the run ends unverified. Grading runs once, after the run.',
  },
  repair: {
    name: 'Repair loop', path: ['intake', 'snapshot', 'tools', 'model', 'proposal', 'sandbox', 'feedback', 'model', 'proposal', 'sandbox', 'grading'], extra: ['ledger'],
    zones: ['host', 'sandbox', 'eval'], view: [0, 0, 1200, 540],
    cap: 'When the checks do not hold, the agent’s own findings go back to the model, for up to 5 proposals (default 3). Grading still runs once, after the run, and never feeds back.',
  },
  submit: {
    name: 'Submission', path: ['sandbox', 'review', 'approval', 'draft'], extra: [],
    zones: ['sandbox', 'ext'], view: [150, 30, 1050, 472.5],
    cap: 'Only a verified run can become a proposal. A person approves its exact digest, then one draft PR is opened. Tested against a fake GitHub API and validated once on a private synthetic repository.',
  },
};

export const LAYOUTS = {
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

/* ---- replay: the recorded run ---- */
const LRU_BEFORE = ['@@ -62,6 +62,7 @@', '         expires_at = None if self._ttl is None else self._clock() + self._ttl', '         if key in self._entries:', '             self._entries[key] = (value, expires_at)'];
const LRU_AFTER = ['             return', '         self._purge_expired()', '         while len(self._entries) >= self._capacity:'];
const TEST_FILE = ['@@ -0,0 +1,10 @@', '+from cachekit import LRUCache', '+', '+', '+def test_rewritten_key_survives_eviction():', '+    cache = LRUCache(capacity=2)', "+    cache.put('a', 1)", "+    cache.put('b', 2)", "+    cache.put('a', 3)", "+    cache.put('c', 4)", "+    assert 'a' in cache"];
export const diffFiles = (added) => [
  { name: 'src/cachekit/lru.py', lines: [...LRU_BEFORE, `+            ${added}`, ...LRU_AFTER] },
  { name: 'tests/test_rewrite_recency.py', lines: TEST_FILE },
];
export const FEEDBACK = [
  'Proposal 1 was not accepted: the checks in the sandbox did not hold.', '',
  '<untrusted_check_result>',
  'regression tests on the patched code: expected that all tests pass, but the result was failed',
  '- regression tests on the original code: expected that at least one test fails; result failed; held',
  '- regression tests on the patched code: expected that all tests pass; result failed; did not hold',
  '</untrusted_check_result>',
  '<untrusted_test_output>',
  'tests/test_rewrite_recency.py::test_rewritten_key_survives_eviction',
  "AssertionError: assert 'a' in <cachekit.lru.LRUCache object at 0x...>",
  '</untrusted_test_output>', '',
  'The snapshot is unchanged: your edits were discarded. You have 2 proposals left. Look at the code again if you need to, then answer with a new, complete proposal.',
];
export const ISSUE = [
  'Frequently updated cache entry gets evicted', '',
  'We use LRUCache to hold the latest reading per sensor. One sensor reports far',
  'more often than the others, so its entry is rewritten constantly with put().',
  'Even so, that entry keeps disappearing from the cache as soon as a couple of',
  'other sensors report, and we fall back to the slow path for our busiest key.', '',
  'The class docstring says that reading or writing an entry counts as using it,',
  'so I would expect an entry that was just written to be the last one evicted.',
];
export const TEST_OUTPUT = ['tests/test_rewrite_recency.py::test_rewritten_key_survives_eviction', "AssertionError: assert 'a' in <cachekit.lru.LRUCache object at 0x...>"];

export const SCENARIOS = {
  'right-first': 'The reference fix and a regression test.',
  'wrong-then-right': 'A plausible wrong fix, then the reference fix.',
  'policy-then-right': 'The fix plus a forbidden conftest.py, then the fix alone.',
  'probe-hidden-then-right': 'Tool calls aimed at hidden files, then the fix. Nothing hidden is returned.',
  'no-usable-answer': 'Text that is not a proposal, every time. Never resolves.',
};
export const STATUS_TEXT = {
  candidate_verified: 'candidate_verified', unverified: 'unverified',
  rejected_policy: 'rejected by policy', invalid_proposal: 'no usable proposal',
};
export const DIGEST = '3f9c2a7be1d40c86a5e2f71b9d03c4e8a6f15b27c9e0d3a4b8f26e1c7d5a9b03';
export const CONFIRM_CHARS = 12; // as in src/patchpilot/submit/approval.py
