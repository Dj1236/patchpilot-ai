# PatchPilot AI: threat model

## Untrusted inputs

Three kinds of content are untrusted, and all three reach the model:

- GitHub issue text.
- Repository source code and file names.
- Test output.

Every control below is enforced in code. None of them depends on
instructions in a model prompt.

## Trust boundaries

| Zone | Contains | Trust |
|---|---|---|
| Host orchestrator | CLI, config, agent, policy, store, model client, secrets | Trusted. Never imports or executes repository code. |
| Sandbox container | Workspace copy, pytest | Untrusted. No secrets, no network. |
| Evaluator | Hidden tests, grading | Trusted, and unreachable by the agent. |

## Risks and controls

| Risk | Control |
|---|---|
| Hostile build step | A repository's own Dockerfile and install scripts are never used. Images come from a PatchPilot base image pinned by digest. |
| Hostile dependency install | Dependencies come from a PatchPilot lock file with hashes, installed as prebuilt wheels only, so no package build scripts run. The repository runs from its source tree instead of being installed. |
| Hostile code at test time | Non-root user, all capabilities dropped, no new privileges, read-only root filesystem, no Docker socket, no network, limits on memory, CPU, processes and time. |
| Host filesystem access | Only a copy of the workspace and one output directory are mounted. |
| Secret theft | The model key and GitHub token stay in the host process, are never passed to a container, and are redacted from logs and traces. |
| Excess GitHub access | Read-only fine-grained token in V1. No write token exists until Milestone 7. |
| Prompt injection | Fixed tool set, no shell, confined paths, capped and escaped test output. |
| Weakened tests | Patches may add tests but may not change or delete existing tests, CI workflows, test configuration or protected paths. |
| Guessed edits | An edit must match its expected original text exactly once, or it is rejected. |
| Self-graded results | Hidden tests are outside the agent's workspace, images and prompts. The agent's own regression test never decides `resolved`. |
| Unauthorized GitHub writes | Every write requires explicit approval. |
| Runaway spend | Hard caps on attempts, tool calls, tokens, cost and time. |
| Malicious report content | Untrusted text is escaped in reports and in any pull request body. |

## Implementation status

The sandbox controls in the table above are implemented and verified by
tests that attack a real container; see [sandbox.md](sandbox.md) for the
control-by-control evidence and the remaining limitations. Read-only issue
intake, the allowlist, hardened fetching and the confined repository tools
are implemented too; see [intake.md](intake.md). No write access to GitHub
exists anywhere in the code. The patch policy, the
prompt builder and the spend ledger are implemented as well; see
[baseline.md](baseline.md). No live model call has been made yet.

## The add-only test rule

V1 patches may add regression tests. They may not modify or delete existing
tests, CI workflows, test configuration or protected files. This is a
deliberate V1 restriction that stops an agent "fixing" a bug by weakening
the tests. It is not a general rule for legitimate software maintenance,
where changing a wrong test is sometimes the right fix.

## Stated limitations

- These measures reduce risk. They do not guarantee isolation against
  hostile code, because a container shares the host kernel.
- V1 therefore accepts only approved repositories with a maintainer-written
  environment manifest.
- Docker on macOS runs inside a virtual machine. Linux CI is the reference
  environment for sandbox safety tests.
- Source code from approved repositories is sent to the model provider.
- On the Gemini free tier the provider may use prompts and responses to
  improve its products, and people may review them. Only synthetic fixtures
  or approved public repositories may be sent there. Hidden tests and
  reference fixes are never sent to any provider.
- Whether a Gemini key is billed depends on its Google project, which code
  cannot see. The owner confirms the project has no billing account; the
  spend ledger bounds the exposure if that is wrong.
