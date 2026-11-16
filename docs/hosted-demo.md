# Hosted demo (release gate F)

The public demo runs PatchPilot's normal repair pipeline on the four
synthetic tasks, with `openai/gpt-oss-120b` on Groq's free API tier and the
Docker sandbox on one AWS EC2 instance. Every run below is reported,
including the failures. These are single runs, **not a success rate**.

URL: <https://54-252-135-177.sslip.io/> (an access code is required to
start a run; ask the owner).

## Why Groq

Free-tier Gemini answered all nine requests with HTTP 503. The local
`qwen3:8b` model (gate B) needs more memory than the largest instance the
AWS Free plan allows, and `qwen3:4b` failed locally on 2026-11-15: three
`invalid_proposal` answers, 0 tool calls, 16,275 output tokens, 1,184 s.
Groq serves the model remotely, so the host needs only Docker.

## Setup

| | |
|---|---|
| Provider | `groq` (`src/patchpilot/llm/groq_client.py`), only `https://api.groq.com/openai/v1` |
| Model | `openai/gpt-oss-120b`, a Groq production model with tool use |
| Free-tier limits | Read from Groq's response headers on 2026-11-15: 1,000 requests a day, 8,000 tokens a minute |
| Pacing | Requests wait for room in a rolling 60 s window; Groq counts the requested output, so output is capped at 2,048 tokens |
| Price in the ledger | List price rounded up (1 USD per million tokens) as an upper bound; Groq's free tier charges nothing |
| Limits | The normal repair limits: 3 proposals, 400,000 tokens, 30 generation attempts, 1,200 s; tool budgets unchanged |
| Key | `GROQ_API_KEY` from the process environment only; redacted from traces; root-only file on the host |

## Runs

| # | Where | Task | Outcome | Hidden tests | Turns, tools | Tokens in / out | Time |
|---|---|---|---|---|---|---|---|
| 1 | laptop | `inj-001-cachekit` | `failed_infrastructure`: per-minute limit (no pacing yet) | not graded | 4, 4 | 9,293 / 741 | 46 s |
| 2 | laptop | `inj-001-cachekit` | `candidate_verified` | **resolved** | 5, 4 | 14,772 / 2,064 | 246 s |
| 3 | public URL | `inj-004-pager` | `failed_infrastructure`: Groq HTTP 400 `tool_use_failed` | not graded | 4, 4 | 7,191 / 553 | 242 s |
| 4 | public URL | `inj-004-pager` | `invalid_proposal`: the refused answer ended the loop | not graded | 6, 5 | 14,517 / 2,917 | 302 s |
| 5 | public URL | `inj-004-pager` | `candidate_verified` | **resolved** | 7, 5 | 18,120 / 3,609 | 375 s |
| 6 | public URL (owner) | `inj-001-cachekit` | `failed_infrastructure`: one request exceeded the per-minute limit | not graded | 6, 5 | not recorded | 301 s |
| 7 | public URL | `inj-001-cachekit` | `candidate_verified` (first proposal) | **resolved** | 5, 4 | 12,885 / 1,657 | 246 s |

After each failure, one focused change was made to the adapter, never to the
sandbox, the patch policy, the budgets or the grading:

- After run 1: pacing to the per-minute token limit and the 2,048-token
  output cap.
- After run 3: `tool_use_failed` (Groq refusing the model's own malformed
  tool call) is a model answer, not an outage.
- After run 4: the model's raw output, which Groq returns as
  `failed_generation`, becomes that answer, so the agent rejects it with
  feedback like any invalid proposal.

- After run 6 (PR #29): the request-size estimate is calibrated on Groq's
  reported prompt count; when a request would exceed the per-request limit,
  the oldest tool output is replaced by a note to call the tool again and
  the requested output is lowered, never below 1,024 tokens; an answer
  wrapped in a call to a tool named `json` is unwrapped and validated as a
  proposal. Replaying run 6's trace offline, every request fits and its
  rejected answer parses as a valid proposal. Run 7 used the deployed fix,
  but stayed small enough not to need either path.

In run 5, proposal 1 was that raw output and was rejected; proposal 2
changed `page_count` to `(total + size - 1) // size` and added a regression
test. The three sandbox checks held and independent hidden-test grading
resolved it. Every run cost $0.

## Host

| | |
|---|---|
| Account | AWS Free plan (`accountPlanType` `FREE`), `ap-southeast-2`; no charges while on this plan |
| Instance | `m7i-flex.large` (Free-tier eligible), 2 vCPU, 8 GiB, Ubuntu 24.04, IMDSv2 required |
| Disk, address | 20 GB encrypted gp3, one Elastic IP; HTTPS by Caddy with a Let's Encrypt certificate for the sslip.io name |
| Credit use | About 3.06 USD a day from the 120 USD credit |
| Spend guard | An AWS Budget of 75 USD (gross cost) emails the owner and stops the instance at 100% |
| Network | Only 80 and 443 are open; SSH was removed after setup |
| Memory | About 640 MB in use during a run |

Security checks made before the first public run: TLS valid and HTTP
redirects to HTTPS; HSTS, `nosniff`, `no-referrer` and CSP headers; no
`Server` header; `/docs` and `/openapi.json` return 404; a missing or wrong
access code returns 401; an 8 KB body is refused by the proxy; the app
listens on 127.0.0.1 only; Docker and the app port are not reachable from
outside; the service runs as `patchpilot` with `NoNewPrivileges` and
`ProtectSystem=strict`; the key file is `600 root:root` and the key is in no
log or run file. The residual risk in `deploy/README.md` still applies: the
service account can start containers.

Public limits: 5 runs a day, 2 per address an hour, a queue of 1, one run
at a time. The counters are in memory, so a restart resets them. The page
also offers a recorded copy of run 7, labelled as recorded and not live,
for visitors who cannot wait for a run.

## Limitations

- Seven single runs on synthetic tasks; no success rate is claimed.
- Groq's free tier allows 8,000 tokens a minute, so a run waits between
  turns and takes four to seven minutes; its daily quota is not shown in
  Groq's headers and, if reached, stops a run as `quota_exhausted`.
- The budget stops the instance after about 24 days of continuous running;
  the AWS Free plan ends on 2027-05-15 or when the credit is used up.
- The URL is an sslip.io name for the Elastic IP, not a custom domain.
