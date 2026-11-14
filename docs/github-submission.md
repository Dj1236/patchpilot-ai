# GitHub pull request submission

A verified run can become a **draft** pull request, and only after a person
approved that exact proposal. Status: **verified against a fake GitHub API
in tests, and validated once against real GitHub (release gate C).** On
2026-11-14 the owner approved and submitted run `inj-001-cachekit-1` to the
private synthetic repository `Dj1236/patchpilot-demo-cachekit`: one draft pull
request (#2), commit `c664799` on base `3f3c79f`. Its branch matched the
approved proposal byte for byte, `main` was unchanged, and a second `submit`
created nothing. Every further submission needs its own approval and token.

```
verified run -> propose-pr -> (person reviews) -> approve -> submit -> draft PR
```

## Commands

```
uv run patchpilot propose-pr <run dir> --repository owner/name --issue 12 [--base-branch main]
uv run patchpilot approve <run dir>
PATCHPILOT_GITHUB_WRITE_TOKEN=... uv run patchpilot submit <run dir>
```

The run directory must be inside the runs folder. `propose-pr` and
`approve` send nothing. `submit` is the only command that writes to GitHub.

## The proposal

`propose-pr` writes `pr-proposal.json` and a readable `pr-proposal.md` next
to the run. Nothing is asked of a model: the proposal is computed from the
run's `record.json` and the files the run started from.

- Only a `candidate_verified` run with a verified proposal is accepted.
- The patch is re-applied to the original files. The result must give the
  recorded diff, must pass the patch rules again, and the files must be the
  ones the run started from (by digest).
- The target must be in the allowlist. The base commit is the commit pinned
  there, or the run's commit; if both exist they must agree.
- It holds: repository and id, issue, base branch and commit, run id, task
  and mode, a digest of `record.json`, every changed file with its full new
  content and digest, the diff, the visible validation steps, limitations,
  the branch name, title, body, and the list of intended GitHub operations.
- **It never holds** the hidden-test result, anything from hidden tests or
  reference fixes, the trace, prompts, thinking, or any credential. Known
  secrets are redacted, and a changed file that contains one is refused.
- The title is fixed text with the issue number. The model's summary and
  root cause appear in the body only as escaped block quotes, with mentions
  and markup neutralised.
- The branch is `patchpilot/issue-<n>-<first 12 of the patch digest>`.

## The approval

`approve` prints the repository, issue, base, files, the intended operations
and the patch digest, and asks the person to **type the first 12 characters
of the patch digest**. A plain "yes" does not approve. It writes
`approval.json`, readable by the owner only, holding:

- an approval id, who approved (local user name) and when, and an expiry
  (24 hours);
- the digest of the whole proposal, and, repeated for auditing, the
  repository and id, issue, base commit, patch digest, run evidence digest,
  branch and operation.

`submit` checks every one of these against the proposal. A different patch,
target, issue, base, branch, body or operation is not covered. No code path
of the agent creates an approval: the model has no shell and its only tools
read the snapshot.

## Submission

`submit` uses `PATCHPILOT_GITHUB_WRITE_TOKEN`, separate from the read-only
token. It must be a **fine-grained token for that one repository** with
contents and pull requests write access. A classic token is refused.

Before any write, it reads:

1. the repository: id and name must match the proposal, the token must have
   push permission, and the branch must not be the default or base branch;
2. the base branch: its head must still be the approved base commit, or the
   approval is stale;
3. the base commit's tree.

Then it creates the blobs (checking each returned SHA against the SHA-1 it
computes locally), the tree, a commit whose only parent is the base commit,
the branch, and one draft pull request with `maintainer_can_modify` off. It
reads the branch back to check it points at the approved commit, and checks
the opened pull request's head and base.

The writer can only send these requests, to the bound repository: `GET` of
the repository, a branch, a commit, a ref and the pull request list, and
`POST` of blobs, trees, commits, refs and pulls. Anything else, including
merges, ref updates, deletions and other repositories, is refused before it
is sent. Nothing is ever merged, and no branch is ever updated or forced.

## Repeats and failures

Every step is written to `submission.json` with an event log.

| Situation | What happens |
|---|---|
| Submit again after success | Returns the recorded pull request; sends nothing |
| Record lost, PR exists | Finds the PR by branch with reads; creates nothing |
| Branch exists with our commit | Reused |
| Branch exists with other content | Refused; left alone |
| Timeout or dropped connection while creating the branch or the PR | Marked `uncertain`; the write is not repeated in that process |
| Submit after `uncertain` | Reads the branch and pull requests first, then continues only from what is there |
| Base branch moved | Refused as stale before any write |
| Wrong repository id or name, no push permission, 401, 403 | Refused before any write |
| Approval expired, missing or for another proposal | Refused before any request |
| Another approval for an already submitted run | Refused |

Blobs and trees are content-addressed, so creating them again is harmless.
A repeated commit is simply unused.

## Limits

- **The remote files are not compared with the run's files.** `submit` checks
  the base commit id, but not that the files at that commit are the ones the
  run started from. If they differ, the pull request on GitHub replaces whole
  files and its real diff differs from the reviewed one. Use only a target
  whose pinned commit holds exactly the run's files, such as a repository
  created from a fixture's `repo/` folder.
- Token permissions needed, per GitHub's documentation for fine-grained
  tokens, on the one target repository only: **Contents: read and write**
  (blobs, trees, commits, refs, branches), **Pull requests: read and write**,
  and **Metadata: read** (always included). Creating refs also lists
  Workflows permission; it is needed only when a commit changes workflow
  files, which the patch policy forbids, so it should not be granted.
- A fine-grained token's responses did not carry the `X-OAuth-Scopes`
  header in the gate C submission, so the writer's classic-token check let
  it through, as intended.
- If GitHub refuses a draft pull request for the repository's plan, `submit`
  stops; it never retries without `draft`.
- One real submission has been made (gate C). GitHub's rate limits, its
  eventual consistency after an `uncertain` write, and drafts on other plans
  remain unverified.
- Proposals are built for evaluation fixtures, whose files come from the
  task. A run on a real repository would build the proposal from the intake
  snapshot; that wiring is not done.
- After `uncertain`, a read that shows no pull request is trusted. If
  GitHub's list lagged behind a pull request that was created, a second one
  could be opened. This is unverified.
- The approval is an unsigned local file bound to the local user name, not
  an identity check: anyone who can write the run directory can approve.
- The token's repository scope is not checked by code; only classic tokens
  are refused.
- If a write is left `uncertain` and the approval expires before `submit` is
  run again, it cannot be reconciled by `submit`: a new approval is refused
  for an already started submission. Check GitHub by hand in that case.
