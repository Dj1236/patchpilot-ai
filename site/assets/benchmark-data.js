// Copied from evals/results/scripted-benchmark.json; tests/unit/test_site.py checks it matches.
window.PP_BENCHMARK = {
 "label": "SCRIPTED / OFFLINE. The model is a script of hand-written answers. These results exercise the pipeline; they are not the success rate of any language model.",
 "generated_at": "2026-11-14T03:56:23.330664+00:00",
 "tasks": [
  "inj-001-cachekit",
  "inj-002-exprcalc",
  "inj-003-rangespec",
  "inj-004-pager"
 ],
 "summary": {
  "baseline": {
   "runs": 20,
   "resolved_by_hidden_tests": 8,
   "verified_by_agent": 8,
   "proposals": 16,
   "sandbox_runs_including_grading": 44
  },
  "repair": {
   "runs": 20,
   "resolved_by_hidden_tests": 16,
   "verified_by_agent": 16,
   "proposals": 36,
   "sandbox_runs_including_grading": 72
  }
 },
 "rows": [
  {
   "task": "inj-001-cachekit",
   "scenario": "right-first",
   "mode": "baseline",
   "status": "candidate_verified",
   "resolved": true,
   "fail_to_pass": [
    3,
    3
   ],
   "pass_to_pass": [
    14,
    14
   ],
   "proposals": 1
  },
  {
   "task": "inj-001-cachekit",
   "scenario": "right-first",
   "mode": "repair",
   "status": "candidate_verified",
   "resolved": true,
   "fail_to_pass": [
    3,
    3
   ],
   "pass_to_pass": [
    14,
    14
   ],
   "proposals": 1
  },
  {
   "task": "inj-001-cachekit",
   "scenario": "wrong-then-right",
   "mode": "baseline",
   "status": "unverified",
   "resolved": false,
   "fail_to_pass": [
    0,
    3
   ],
   "pass_to_pass": [
    14,
    14
   ],
   "proposals": 1
  },
  {
   "task": "inj-001-cachekit",
   "scenario": "wrong-then-right",
   "mode": "repair",
   "status": "candidate_verified",
   "resolved": true,
   "fail_to_pass": [
    3,
    3
   ],
   "pass_to_pass": [
    14,
    14
   ],
   "proposals": 2
  },
  {
   "task": "inj-001-cachekit",
   "scenario": "policy-then-right",
   "mode": "baseline",
   "status": "rejected_policy",
   "resolved": false,
   "fail_to_pass": null,
   "pass_to_pass": null,
   "proposals": 1
  },
  {
   "task": "inj-001-cachekit",
   "scenario": "policy-then-right",
   "mode": "repair",
   "status": "candidate_verified",
   "resolved": true,
   "fail_to_pass": [
    3,
    3
   ],
   "pass_to_pass": [
    14,
    14
   ],
   "proposals": 2
  },
  {
   "task": "inj-001-cachekit",
   "scenario": "probe-hidden-then-right",
   "mode": "baseline",
   "status": "candidate_verified",
   "resolved": true,
   "fail_to_pass": [
    3,
    3
   ],
   "pass_to_pass": [
    14,
    14
   ],
   "proposals": 1
  },
  {
   "task": "inj-001-cachekit",
   "scenario": "probe-hidden-then-right",
   "mode": "repair",
   "status": "candidate_verified",
   "resolved": true,
   "fail_to_pass": [
    3,
    3
   ],
   "pass_to_pass": [
    14,
    14
   ],
   "proposals": 1
  },
  {
   "task": "inj-001-cachekit",
   "scenario": "no-usable-answer",
   "mode": "baseline",
   "status": "invalid_proposal",
   "resolved": false,
   "fail_to_pass": null,
   "pass_to_pass": null,
   "proposals": 0
  },
  {
   "task": "inj-001-cachekit",
   "scenario": "no-usable-answer",
   "mode": "repair",
   "status": "invalid_proposal",
   "resolved": false,
   "fail_to_pass": null,
   "pass_to_pass": null,
   "proposals": 3
  },
  {
   "task": "inj-002-exprcalc",
   "scenario": "right-first",
   "mode": "baseline",
   "status": "candidate_verified",
   "resolved": true,
   "fail_to_pass": [
    6,
    6
   ],
   "pass_to_pass": [
    26,
    26
   ],
   "proposals": 1
  },
  {
   "task": "inj-002-exprcalc",
   "scenario": "right-first",
   "mode": "repair",
   "status": "candidate_verified",
   "resolved": true,
   "fail_to_pass": [
    6,
    6
   ],
   "pass_to_pass": [
    26,
    26
   ],
   "proposals": 1
  },
  {
   "task": "inj-002-exprcalc",
   "scenario": "wrong-then-right",
   "mode": "baseline",
   "status": "unverified",
   "resolved": false,
   "fail_to_pass": [
    0,
    6
   ],
   "pass_to_pass": [
    26,
    26
   ],
   "proposals": 1
  },
  {
   "task": "inj-002-exprcalc",
   "scenario": "wrong-then-right",
   "mode": "repair",
   "status": "candidate_verified",
   "resolved": true,
   "fail_to_pass": [
    6,
    6
   ],
   "pass_to_pass": [
    26,
    26
   ],
   "proposals": 2
  },
  {
   "task": "inj-002-exprcalc",
   "scenario": "policy-then-right",
   "mode": "baseline",
   "status": "rejected_policy",
   "resolved": false,
   "fail_to_pass": null,
   "pass_to_pass": null,
   "proposals": 1
  },
  {
   "task": "inj-002-exprcalc",
   "scenario": "policy-then-right",
   "mode": "repair",
   "status": "candidate_verified",
   "resolved": true,
   "fail_to_pass": [
    6,
    6
   ],
   "pass_to_pass": [
    26,
    26
   ],
   "proposals": 2
  },
  {
   "task": "inj-002-exprcalc",
   "scenario": "probe-hidden-then-right",
   "mode": "baseline",
   "status": "candidate_verified",
   "resolved": true,
   "fail_to_pass": [
    6,
    6
   ],
   "pass_to_pass": [
    26,
    26
   ],
   "proposals": 1
  },
  {
   "task": "inj-002-exprcalc",
   "scenario": "probe-hidden-then-right",
   "mode": "repair",
   "status": "candidate_verified",
   "resolved": true,
   "fail_to_pass": [
    6,
    6
   ],
   "pass_to_pass": [
    26,
    26
   ],
   "proposals": 1
  },
  {
   "task": "inj-002-exprcalc",
   "scenario": "no-usable-answer",
   "mode": "baseline",
   "status": "invalid_proposal",
   "resolved": false,
   "fail_to_pass": null,
   "pass_to_pass": null,
   "proposals": 0
  },
  {
   "task": "inj-002-exprcalc",
   "scenario": "no-usable-answer",
   "mode": "repair",
   "status": "invalid_proposal",
   "resolved": false,
   "fail_to_pass": null,
   "pass_to_pass": null,
   "proposals": 3
  },
  {
   "task": "inj-003-rangespec",
   "scenario": "right-first",
   "mode": "baseline",
   "status": "candidate_verified",
   "resolved": true,
   "fail_to_pass": [
    2,
    2
   ],
   "pass_to_pass": [
    9,
    9
   ],
   "proposals": 1
  },
  {
   "task": "inj-003-rangespec",
   "scenario": "right-first",
   "mode": "repair",
   "status": "candidate_verified",
   "resolved": true,
   "fail_to_pass": [
    2,
    2
   ],
   "pass_to_pass": [
    9,
    9
   ],
   "proposals": 1
  },
  {
   "task": "inj-003-rangespec",
   "scenario": "wrong-then-right",
   "mode": "baseline",
   "status": "unverified",
   "resolved": false,
   "fail_to_pass": [
    1,
    2
   ],
   "pass_to_pass": [
    9,
    9
   ],
   "proposals": 1
  },
  {
   "task": "inj-003-rangespec",
   "scenario": "wrong-then-right",
   "mode": "repair",
   "status": "candidate_verified",
   "resolved": true,
   "fail_to_pass": [
    2,
    2
   ],
   "pass_to_pass": [
    9,
    9
   ],
   "proposals": 2
  },
  {
   "task": "inj-003-rangespec",
   "scenario": "policy-then-right",
   "mode": "baseline",
   "status": "rejected_policy",
   "resolved": false,
   "fail_to_pass": null,
   "pass_to_pass": null,
   "proposals": 1
  },
  {
   "task": "inj-003-rangespec",
   "scenario": "policy-then-right",
   "mode": "repair",
   "status": "candidate_verified",
   "resolved": true,
   "fail_to_pass": [
    2,
    2
   ],
   "pass_to_pass": [
    9,
    9
   ],
   "proposals": 2
  },
  {
   "task": "inj-003-rangespec",
   "scenario": "probe-hidden-then-right",
   "mode": "baseline",
   "status": "candidate_verified",
   "resolved": true,
   "fail_to_pass": [
    2,
    2
   ],
   "pass_to_pass": [
    9,
    9
   ],
   "proposals": 1
  },
  {
   "task": "inj-003-rangespec",
   "scenario": "probe-hidden-then-right",
   "mode": "repair",
   "status": "candidate_verified",
   "resolved": true,
   "fail_to_pass": [
    2,
    2
   ],
   "pass_to_pass": [
    9,
    9
   ],
   "proposals": 1
  },
  {
   "task": "inj-003-rangespec",
   "scenario": "no-usable-answer",
   "mode": "baseline",
   "status": "invalid_proposal",
   "resolved": false,
   "fail_to_pass": null,
   "pass_to_pass": null,
   "proposals": 0
  },
  {
   "task": "inj-003-rangespec",
   "scenario": "no-usable-answer",
   "mode": "repair",
   "status": "invalid_proposal",
   "resolved": false,
   "fail_to_pass": null,
   "pass_to_pass": null,
   "proposals": 3
  },
  {
   "task": "inj-004-pager",
   "scenario": "right-first",
   "mode": "baseline",
   "status": "candidate_verified",
   "resolved": true,
   "fail_to_pass": [
    2,
    2
   ],
   "pass_to_pass": [
    7,
    7
   ],
   "proposals": 1
  },
  {
   "task": "inj-004-pager",
   "scenario": "right-first",
   "mode": "repair",
   "status": "candidate_verified",
   "resolved": true,
   "fail_to_pass": [
    2,
    2
   ],
   "pass_to_pass": [
    7,
    7
   ],
   "proposals": 1
  },
  {
   "task": "inj-004-pager",
   "scenario": "wrong-then-right",
   "mode": "baseline",
   "status": "unverified",
   "resolved": false,
   "fail_to_pass": [
    2,
    2
   ],
   "pass_to_pass": [
    4,
    7
   ],
   "proposals": 1
  },
  {
   "task": "inj-004-pager",
   "scenario": "wrong-then-right",
   "mode": "repair",
   "status": "candidate_verified",
   "resolved": true,
   "fail_to_pass": [
    2,
    2
   ],
   "pass_to_pass": [
    7,
    7
   ],
   "proposals": 2
  },
  {
   "task": "inj-004-pager",
   "scenario": "policy-then-right",
   "mode": "baseline",
   "status": "rejected_policy",
   "resolved": false,
   "fail_to_pass": null,
   "pass_to_pass": null,
   "proposals": 1
  },
  {
   "task": "inj-004-pager",
   "scenario": "policy-then-right",
   "mode": "repair",
   "status": "candidate_verified",
   "resolved": true,
   "fail_to_pass": [
    2,
    2
   ],
   "pass_to_pass": [
    7,
    7
   ],
   "proposals": 2
  },
  {
   "task": "inj-004-pager",
   "scenario": "probe-hidden-then-right",
   "mode": "baseline",
   "status": "candidate_verified",
   "resolved": true,
   "fail_to_pass": [
    2,
    2
   ],
   "pass_to_pass": [
    7,
    7
   ],
   "proposals": 1
  },
  {
   "task": "inj-004-pager",
   "scenario": "probe-hidden-then-right",
   "mode": "repair",
   "status": "candidate_verified",
   "resolved": true,
   "fail_to_pass": [
    2,
    2
   ],
   "pass_to_pass": [
    7,
    7
   ],
   "proposals": 1
  },
  {
   "task": "inj-004-pager",
   "scenario": "no-usable-answer",
   "mode": "baseline",
   "status": "invalid_proposal",
   "resolved": false,
   "fail_to_pass": null,
   "pass_to_pass": null,
   "proposals": 0
  },
  {
   "task": "inj-004-pager",
   "scenario": "no-usable-answer",
   "mode": "repair",
   "status": "invalid_proposal",
   "resolved": false,
   "fail_to_pass": null,
   "pass_to_pass": null,
   "proposals": 3
  }
 ]
};
