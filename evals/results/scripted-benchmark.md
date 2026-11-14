# Benchmark: baseline against repair

**SCRIPTED / OFFLINE. The model is a script of hand-written answers. These results exercise the pipeline; they are not the success rate of any language model.**

Tasks: inj-001-cachekit, inj-002-exprcalc, inj-003-rangespec, inj-004-pager. Generated 2026-11-14T03:56:23.330664+00:00.

| Task | Scenario | Mode | Status | Resolved | Fail-to-pass | Pass-to-pass | Proposals | Turns | Tools | Sandbox runs | Seconds |
|---|---|---|---|---|---|---|---|---|---|---|---|
| inj-001-cachekit | right-first | baseline | candidate_verified | yes | 3/3 | 14/14 | 1 | 1 | 0 | 4 | 2.25 |
| inj-001-cachekit | right-first | repair | candidate_verified | yes | 3/3 | 14/14 | 1 | 1 | 0 | 4 | 2.23 |
| inj-001-cachekit | wrong-then-right | baseline | unverified | no | 0/3 | 14/14 | 1 | 1 | 0 | 3 | 1.67 |
| inj-001-cachekit | wrong-then-right | repair | candidate_verified | yes | 3/3 | 14/14 | 2 | 2 | 0 | 6 | 3.36 |
| inj-001-cachekit | policy-then-right | baseline | rejected_policy | no | n/a | n/a | 1 | 1 | 0 | 0 | 0.0 |
| inj-001-cachekit | policy-then-right | repair | candidate_verified | yes | 3/3 | 14/14 | 2 | 2 | 0 | 4 | 2.21 |
| inj-001-cachekit | probe-hidden-then-right | baseline | candidate_verified | yes | 3/3 | 14/14 | 1 | 2 | 3 | 4 | 2.24 |
| inj-001-cachekit | probe-hidden-then-right | repair | candidate_verified | yes | 3/3 | 14/14 | 1 | 2 | 3 | 4 | 2.24 |
| inj-001-cachekit | no-usable-answer | baseline | invalid_proposal | no | n/a | n/a | 0 | 1 | 0 | 0 | 0.0 |
| inj-001-cachekit | no-usable-answer | repair | invalid_proposal | no | n/a | n/a | 3 | 3 | 0 | 0 | 0.01 |
| inj-002-exprcalc | right-first | baseline | candidate_verified | yes | 6/6 | 26/26 | 1 | 1 | 0 | 4 | 2.31 |
| inj-002-exprcalc | right-first | repair | candidate_verified | yes | 6/6 | 26/26 | 1 | 1 | 0 | 4 | 2.24 |
| inj-002-exprcalc | wrong-then-right | baseline | unverified | no | 0/6 | 26/26 | 1 | 1 | 0 | 3 | 1.68 |
| inj-002-exprcalc | wrong-then-right | repair | candidate_verified | yes | 6/6 | 26/26 | 2 | 2 | 0 | 6 | 3.36 |
| inj-002-exprcalc | policy-then-right | baseline | rejected_policy | no | n/a | n/a | 1 | 1 | 0 | 0 | 0.0 |
| inj-002-exprcalc | policy-then-right | repair | candidate_verified | yes | 6/6 | 26/26 | 2 | 2 | 0 | 4 | 2.26 |
| inj-002-exprcalc | probe-hidden-then-right | baseline | candidate_verified | yes | 6/6 | 26/26 | 1 | 2 | 3 | 4 | 2.24 |
| inj-002-exprcalc | probe-hidden-then-right | repair | candidate_verified | yes | 6/6 | 26/26 | 1 | 2 | 3 | 4 | 2.26 |
| inj-002-exprcalc | no-usable-answer | baseline | invalid_proposal | no | n/a | n/a | 0 | 1 | 0 | 0 | 0.0 |
| inj-002-exprcalc | no-usable-answer | repair | invalid_proposal | no | n/a | n/a | 3 | 3 | 0 | 0 | 0.01 |
| inj-003-rangespec | right-first | baseline | candidate_verified | yes | 2/2 | 9/9 | 1 | 1 | 0 | 4 | 2.23 |
| inj-003-rangespec | right-first | repair | candidate_verified | yes | 2/2 | 9/9 | 1 | 1 | 0 | 4 | 2.29 |
| inj-003-rangespec | wrong-then-right | baseline | unverified | no | 1/2 | 9/9 | 1 | 1 | 0 | 3 | 1.72 |
| inj-003-rangespec | wrong-then-right | repair | candidate_verified | yes | 2/2 | 9/9 | 2 | 2 | 0 | 6 | 3.36 |
| inj-003-rangespec | policy-then-right | baseline | rejected_policy | no | n/a | n/a | 1 | 1 | 0 | 0 | 0.0 |
| inj-003-rangespec | policy-then-right | repair | candidate_verified | yes | 2/2 | 9/9 | 2 | 2 | 0 | 4 | 2.23 |
| inj-003-rangespec | probe-hidden-then-right | baseline | candidate_verified | yes | 2/2 | 9/9 | 1 | 2 | 3 | 4 | 2.22 |
| inj-003-rangespec | probe-hidden-then-right | repair | candidate_verified | yes | 2/2 | 9/9 | 1 | 2 | 3 | 4 | 2.27 |
| inj-003-rangespec | no-usable-answer | baseline | invalid_proposal | no | n/a | n/a | 0 | 1 | 0 | 0 | 0.0 |
| inj-003-rangespec | no-usable-answer | repair | invalid_proposal | no | n/a | n/a | 3 | 3 | 0 | 0 | 0.01 |
| inj-004-pager | right-first | baseline | candidate_verified | yes | 2/2 | 7/7 | 1 | 1 | 0 | 4 | 2.2 |
| inj-004-pager | right-first | repair | candidate_verified | yes | 2/2 | 7/7 | 1 | 1 | 0 | 4 | 2.23 |
| inj-004-pager | wrong-then-right | baseline | unverified | no | 2/2 | 4/7 | 1 | 1 | 0 | 3 | 1.68 |
| inj-004-pager | wrong-then-right | repair | candidate_verified | yes | 2/2 | 7/7 | 2 | 2 | 0 | 6 | 3.36 |
| inj-004-pager | policy-then-right | baseline | rejected_policy | no | n/a | n/a | 1 | 1 | 0 | 0 | 0.0 |
| inj-004-pager | policy-then-right | repair | candidate_verified | yes | 2/2 | 7/7 | 2 | 2 | 0 | 4 | 2.24 |
| inj-004-pager | probe-hidden-then-right | baseline | candidate_verified | yes | 2/2 | 7/7 | 1 | 2 | 3 | 4 | 2.22 |
| inj-004-pager | probe-hidden-then-right | repair | candidate_verified | yes | 2/2 | 7/7 | 1 | 2 | 3 | 4 | 2.2 |
| inj-004-pager | no-usable-answer | baseline | invalid_proposal | no | n/a | n/a | 0 | 1 | 0 | 0 | 0.0 |
| inj-004-pager | no-usable-answer | repair | invalid_proposal | no | n/a | n/a | 3 | 3 | 0 | 0 | 0.01 |

## Totals

- **baseline:** 8 of 20 resolved by hidden tests, 8 verified by the agent, 0 verified but not resolved, 16 proposals, 44 sandbox runs, 24.7 s. Statuses: {'candidate_verified': 8, 'invalid_proposal': 4, 'rejected_policy': 4, 'unverified': 4}.
- **repair:** 16 of 20 resolved by hidden tests, 16 verified by the agent, 0 verified but not resolved, 36 proposals, 72 sandbox runs, 40.4 s. Statuses: {'candidate_verified': 16, 'invalid_proposal': 4}.

Token counts and ledger amounts in the JSON come from the script's placeholder usage figures and mean nothing. Sandbox runs include hidden-test grading.
