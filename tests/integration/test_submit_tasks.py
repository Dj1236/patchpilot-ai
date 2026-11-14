"""From a repair run on a real fixture to a draft pull request on a fake GitHub.

The model is scripted and GitHub is a local fake: no request leaves the
machine. This is the whole offline path: a wrong patch, feedback, a fixed
patch, independent grading, a stop and resume, a proposal, a person's
approval, and the submission.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

import patchpilot.cli as cli
from patchpilot.agent.checkpoint import load_checkpoint
from patchpilot.agent.trace import TRACE_FILE
from patchpilot.submit.proposal import load_proposal
from tests.fake_github import BASE_COMMIT, NAME, OWNER, REPOSITORY_ID, TOKEN, FakeGitHub
from tests.integration.test_baseline_tasks import TASKS_ROOT
from tests.integration.test_resume_tasks import Processes

pytestmark = pytest.mark.sandbox


def test_a_repaired_run_becomes_one_approved_draft_pull_request(
    sandbox: object,
    cachekit_fixture: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs = tmp_path / "runs"
    processes = Processes(cachekit_fixture, sandbox, runs)  # type: ignore[arg-type]
    assert processes.start(interrupt_after=5) is None
    graded = processes.resume()
    assert graded is not None and graded.resolved
    allowlist = tmp_path / "allowlist.toml"
    allowlist.write_text(
        f'schema_version = 1\n\n[[repositories]]\nowner = "{OWNER}"\nname = "{NAME}"\n'
        f'repository_id = {REPOSITORY_ID}\ncommit_sha = "{BASE_COMMIT}"\n'
    )
    monkeypatch.setenv("PATCHPILOT_RUNS_DIR", str(runs))
    monkeypatch.setenv("PATCHPILOT_ALLOWLIST_PATH", str(allowlist))
    github = FakeGitHub()
    monkeypatch.setattr(cli, "_github_writer", lambda repository, token: github.writer())
    runner = CliRunner()
    run_dir = str(processes.directory)
    target = ["--repository", f"{OWNER}/{NAME}", "--issue", "12", "--tasks-root", str(TASKS_ROOT)]

    proposed = runner.invoke(cli.app, ["propose-pr", run_dir, *target])
    proposal = load_proposal(processes.directory)
    approved = runner.invoke(cli.app, ["approve", run_dir], input=f"{proposal.patch_sha256[:12]}\n")
    monkeypatch.setenv("PATCHPILOT_GITHUB_WRITE_TOKEN", TOKEN)
    submitted = runner.invoke(cli.app, ["submit", run_dir])
    again = runner.invoke(cli.app, ["submit", run_dir])

    assert proposed.exit_code == 0, proposed.output
    assert approved.exit_code == 0, approved.output
    assert submitted.exit_code == 0 and again.exit_code == 0, submitted.output + again.output
    assert len(github.pulls) == 1 and github.pulls[0]["draft"] is True
    assert proposal.mode == "repair" and proposal.run_id == "inj-001-cachekit-1"
    assert [change.path for change in proposal.changes] == [
        "src/cachekit/lru.py",
        "tests/test_rewrite_recency.py",
    ]
    assert github.files_on(proposal.branch)["src/cachekit/lru.py"].count(b"move_to_end(key)") >= 1
    assert load_checkpoint(processes.directory).state.phase == "done"
    sent = "".join(github.bodies) + proposal.model_dump_json()
    for content in cachekit_fixture.hidden_test_files().values():  # type: ignore[attr-defined]
        for line in content.decode().splitlines():
            if len(line) > 40:
                assert line not in sent, line
    assert "hidden" not in sent.lower() and "resolved" not in sent.lower()
    assert "self.hits += 0" not in sent, "the rejected first proposal is not submitted"
    assert (processes.directory / TRACE_FILE).read_text() not in sent
