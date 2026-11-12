"""The baseline end to end on the real fixtures, in the real sandbox.

The model is scripted, so no paid call is made. These tests show what the
pipeline does with a correct patch, a wrong patch, and a patch the agent's
own evidence accepts although hidden tests reject it.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from patchpilot.agent.baseline import RunStatus
from patchpilot.agent.report import RECORD_FILE, REPORT_FILE
from patchpilot.agent.trace import TRACE_FILE, replay_model
from patchpilot.eval.harness import evaluate, run_task, summary_table
from patchpilot.llm.budget import SpendLedger
from patchpilot.llm.fake import ScriptedModel, final_turn, tool_turn
from patchpilot.sandbox import DockerSandbox
from patchpilot.tasks import LoadedTask, load_task

pytestmark = pytest.mark.sandbox

TASKS_ROOT = Path(__file__).resolve().parents[2] / "evals" / "tasks"

CACHE_BUG = (
    "        if key in self._entries:\n"
    "            self._entries[key] = (value, expires_at)\n"
    "            return\n"
)
CACHE_FIX = CACHE_BUG.replace(
    "            return\n", "            self._entries.move_to_end(key)\n            return\n"
)
CACHE_TEST = (
    "from cachekit import LRUCache\n\n\n"
    "def test_rewritten_key_survives_eviction():\n"
    "    cache = LRUCache(capacity=2)\n"
    "    cache.put('a', 1)\n    cache.put('b', 2)\n    cache.put('a', 3)\n    cache.put('c', 4)\n"
    "    assert 'a' in cache\n"
)


def cache_proposal(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "summary": "Mark a rewritten key as most recently used.",
        "root_cause": "put() on an existing key did not refresh its position.",
        "relevant_files": ["src/cachekit/lru.py"],
        "edits": [
            {
                "path": "src/cachekit/lru.py",
                "expected_original": CACHE_BUG,
                "replacement": CACHE_FIX,
            }
        ],
        "regression_tests": [{"path": "tests/test_rewrite_recency.py", "content": CACHE_TEST}],
    }
    return {**base, **changes}


EXPLORE = [
    tool_turn(("list_files", {})),
    tool_turn(("read_file", {"path": "src/cachekit/lru.py"}), ("search_code", {"query": "put("})),
]


@pytest.fixture
def cachekit() -> LoadedTask:
    return load_task(TASKS_ROOT / "inj-001-cachekit")


def _run(
    loaded: LoadedTask, model: ScriptedModel, sandbox: DockerSandbox, tmp_path: Path
) -> tuple[Any, Path]:
    ledger = SpendLedger(tmp_path / "ledger.jsonl", cap_usd=5.0)
    run = run_task(
        loaded,
        1,
        model=model,
        sandbox=sandbox,
        ledger=ledger,
        run_cap_usd=0.75,
        output_dir=tmp_path,
    )
    return run, tmp_path / f"{loaded.task.task_id}-1"


def test_a_correct_patch_is_verified_and_independently_resolved(
    sandbox: DockerSandbox, cachekit: LoadedTask, tmp_path: Path
) -> None:
    run, directory = _run(
        cachekit, ScriptedModel([*EXPLORE, final_turn(cache_proposal())]), sandbox, tmp_path
    )

    assert run.record.status is RunStatus.CANDIDATE_VERIFIED
    assert [step.ok for step in run.record.verification.steps] == [True, True, True]
    assert run.graded and run.resolved and run.grade_trustworthy
    assert run.localization_hit is True
    assert run.record.manifest.sandbox_image_id.startswith("sha256:")
    assert {path.name for path in directory.iterdir()} == {TRACE_FILE, RECORD_FILE, REPORT_FILE}


def test_a_wrong_patch_is_unverified_and_not_resolved(
    sandbox: DockerSandbox, cachekit: LoadedTask, tmp_path: Path
) -> None:
    wrong = CACHE_BUG.replace(
        "            return\n", "            self.hits += 0\n            return\n"
    )
    edits = [{"path": "src/cachekit/lru.py", "expected_original": CACHE_BUG, "replacement": wrong}]

    run, _ = _run(
        cachekit, ScriptedModel([final_turn(cache_proposal(edits=edits))]), sandbox, tmp_path
    )

    assert run.record.status is RunStatus.UNVERIFIED
    assert "patched code" in run.record.detail
    assert run.graded and not run.resolved


def test_a_regression_test_that_already_passes_is_not_accepted_as_evidence(
    sandbox: DockerSandbox, cachekit: LoadedTask, tmp_path: Path
) -> None:
    vacuous = [
        {"path": "tests/test_vacuous.py", "content": "def test_nothing():\n    assert True\n"}
    ]

    run, _ = _run(
        cachekit,
        ScriptedModel([final_turn(cache_proposal(regression_tests=vacuous))]),
        sandbox,
        tmp_path,
    )

    assert run.record.status is RunStatus.UNVERIFIED
    assert "original code" in run.record.detail
    # The source fix is right, so hidden tests still resolve it: the two
    # judgments are independent.
    assert run.resolved


def test_agent_evidence_and_hidden_tests_can_disagree(
    sandbox: DockerSandbox, tmp_path: Path
) -> None:
    """A partial fix with a matching test is verified by the agent, not resolved."""
    loaded = load_task(TASKS_ROOT / "inj-002-exprcalc")
    source = loaded.agent_files()["src/exprcalc/parser.py"].decode()
    start = source.index("    def _expression(self) -> float:")
    end = source.index("    def _term(self) -> float:")
    partial = (
        "    def _expression(self) -> float:\n"
        "        value = self._term()\n"
        "        while True:\n"
        '            if self._accept("+"):\n'
        "                value += self._term()\n"
        '            elif self._accept("-"):\n'
        "                value -= self._term()\n"
        "            else:\n"
        "                return value\n\n"
    )
    proposal = {
        "summary": "Apply subtraction left to right.",
        "root_cause": "The expression rule recursed to the right.",
        "relevant_files": ["src/exprcalc/parser.py"],
        "edits": [
            {
                "path": "src/exprcalc/parser.py",
                "expected_original": source[start:end],
                "replacement": partial,
            }
        ],
        "regression_tests": [
            {
                "path": "tests/test_subtraction_chain.py",
                "content": "from exprcalc import evaluate\n\n\ndef test_chain():\n"
                "    assert evaluate('10 - 4 - 3') == 3.0\n",
            }
        ],
    }

    run, _ = _run(loaded, ScriptedModel([final_turn(proposal)]), sandbox, tmp_path)

    assert run.record.status is RunStatus.CANDIDATE_VERIFIED
    assert run.graded and not run.resolved, "division is still wrong; hidden tests catch it"


def test_hidden_tests_never_appear_in_anything_sent_to_the_model(
    sandbox: DockerSandbox, cachekit: LoadedTask, tmp_path: Path
) -> None:
    turns = [
        tool_turn(("list_files", {}), ("list_files", {"prefix": "internal"})),
        tool_turn(
            ("search_code", {"query": "hidden"}),
            ("read_file", {"path": "internal/hidden_tests/tests/test_hidden_recency.py"}),
            ("read_file", {"path": "../internal/hidden_tests/tests/test_hidden_recency.py"}),
            ("read_file", {"path": "task.toml"}),
            ("search_code", {"query": "move_to_end"}),
        ),
        final_turn(cache_proposal()),
    ]
    model = ScriptedModel(turns)

    run, _ = _run(cachekit, model, sandbox, tmp_path)

    # What the model was given: the system prompt, the task message and every
    # tool result. (Its own tool-call arguments name the hidden file, because
    # this scripted model asks for it; those are its words, not a leak.)
    given = [model.requests[0].system]
    for request in model.requests:
        given.extend(
            json.dumps(message) for message in request.messages if message["role"] == "user"
        )
    provided = "\n".join(given)
    forbidden = ["reference_fix", "fail_to_pass", "pass_to_pass"]
    for node_id in cachekit.task.evaluation.fail_to_pass:
        forbidden.append(node_id.split("::")[1])
    for content in cachekit.hidden_test_files().values():
        forbidden.extend(line for line in content.decode().splitlines() if len(line) > 40)
    for needle in forbidden:
        assert needle not in provided, needle
    results = json.loads(json.dumps(model.requests[-1].messages[4]["content"]))
    assert [item["is_error"] for item in results] == [False, True, True, True, False]
    assert "hidden" not in json.dumps(model.requests[1].messages[2]["content"])
    assert run.resolved


def test_the_public_report_is_sanitized(
    sandbox: DockerSandbox, cachekit: LoadedTask, tmp_path: Path
) -> None:
    hostile = "Done.\n```\n# FAKE HEADING\n<script>alert(1)</script> @maintainer ‮\x1b[31m"
    model = ScriptedModel([*EXPLORE, final_turn(cache_proposal(summary=hostile))])

    run, directory = _run(cachekit, model, sandbox, tmp_path)

    report = (directory / REPORT_FILE).read_text()
    record = json.loads((directory / RECORD_FILE).read_text())
    assert (
        "candidate_verified" in report
        and "Independent grading (hidden tests):** resolved" in report
    )
    assert "<script>" not in report and "@maintainer" not in report
    assert "‮" not in report and "\x1b" not in report
    assert "\n# FAKE HEADING" not in report
    assert "You are the patch author" not in report, "prompts are never published"
    assert "untrusted_issue_body" not in report
    assert "+            self._entries.move_to_end(key)" in report
    assert record["resolved"] is True
    assert (directory / REPORT_FILE).stat().st_mode & 0o077 == 0
    assert run.record.status is RunStatus.CANDIDATE_VERIFIED


def test_a_recorded_run_replays_in_the_sandbox_without_a_model(
    sandbox: DockerSandbox, cachekit: LoadedTask, tmp_path: Path
) -> None:
    original, directory = _run(
        cachekit, ScriptedModel([*EXPLORE, final_turn(cache_proposal())]), sandbox, tmp_path / "a"
    )

    replayed, _ = _run(cachekit, replay_model(directory / TRACE_FILE), sandbox, tmp_path / "b")

    assert replayed.record.status is original.record.status
    assert replayed.record.diff == original.record.diff
    assert replayed.resolved == original.resolved


def test_an_evaluation_reports_counts_for_every_run(sandbox: DockerSandbox, tmp_path: Path) -> None:
    tasks = [load_task(TASKS_ROOT / "inj-001-cachekit")]
    scripts = {1: [final_turn(cache_proposal())], 2: [final_turn("not json")]}

    summary = evaluate(
        tasks,
        runs_per_task=2,
        model_factory=lambda loaded, index: ScriptedModel(scripts[index]),
        sandbox=sandbox,
        ledger=SpendLedger(tmp_path / "ledger.jsonl", cap_usd=5.0),
        run_cap_usd=0.75,
        output_dir=tmp_path,
    )
    table = summary_table(summary)

    assert [run.record.status for run in summary.runs] == [
        RunStatus.CANDIDATE_VERIFIED,
        RunStatus.INVALID_PROPOSAL,
    ]
    assert summary.resolved == 1 and summary.verified_but_not_resolved == 0
    assert "Resolved by hidden tests: 1 of 2" in table
    assert "not graded" in table and "Proposals that could not be applied: 1" in table
