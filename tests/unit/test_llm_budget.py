import json
import multiprocessing
from pathlib import Path

import pytest

from patchpilot.llm.base import Usage
from patchpilot.llm.budget import (
    PRICES,
    BudgetExceededError,
    RunBudget,
    SpendLedger,
    UnknownModelPriceError,
    actual_cost,
    prices_for,
    to_micro,
    worst_case_cost,
)

MODEL = "claude-sonnet-5-5"
SONNET = PRICES[MODEL]


def test_cost_counts_input_output_cache_writes_and_cache_reads() -> None:
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_creation_input_tokens=1_000_000,
        cache_read_input_tokens=1_000_000,
    )

    assert actual_cost(usage, SONNET) == to_micro(2 + 10 + 2.5 + 0.10)


def test_thinking_is_covered_because_it_is_billed_as_output() -> None:
    # A response that spent its whole output limit on thinking still costs
    # no more than the reserved worst case.
    reserved = worst_case_cost(10_000, 16_000, SONNET)

    assert actual_cost(Usage(input_tokens=10_000, output_tokens=16_000), SONNET) <= reserved
    assert actual_cost(Usage(cache_creation_input_tokens=10_000, output_tokens=16_000), SONNET) <= (
        reserved
    )


def test_fractional_costs_round_up_never_down() -> None:
    assert actual_cost(Usage(cache_read_input_tokens=1), SONNET) == 1
    assert actual_cost(Usage(cache_creation_input_tokens=1), SONNET) == 3


def test_a_model_without_a_price_cannot_be_called() -> None:
    with pytest.raises(UnknownModelPriceError):
        prices_for("some-future-model")


def test_reserve_then_settle_records_the_actual_cost(tmp_path: Path) -> None:
    ledger = SpendLedger(tmp_path / "ledger.jsonl", cap_usd=1.0)
    budget = RunBudget(ledger, "run-1", run_cap_usd=0.75)

    reservation = budget.reserve(MODEL, input_tokens=10_000, max_output_tokens=16_000)
    assert ledger.committed() == worst_case_cost(10_000, 16_000, SONNET)
    cost = budget.settle(reservation, MODEL, Usage(input_tokens=8_000, output_tokens=500))

    assert cost == 8_000 * 2 + 500 * 10
    assert budget.spent == cost and ledger.committed() == cost


def test_a_failed_request_is_charged_its_full_reservation(tmp_path: Path) -> None:
    ledger = SpendLedger(tmp_path / "ledger.jsonl", cap_usd=1.0)
    budget = RunBudget(ledger, "run-1", run_cap_usd=0.75)

    reservation = budget.reserve(MODEL, 10_000, 16_000)
    cost = budget.settle_failed(reservation, "timeout")

    assert cost == worst_case_cost(10_000, 16_000, SONNET) > 0
    assert ledger.committed() == cost


def test_a_request_that_could_exceed_the_run_cap_is_not_reserved(tmp_path: Path) -> None:
    ledger = SpendLedger(tmp_path / "ledger.jsonl", cap_usd=5.0)
    budget = RunBudget(ledger, "run-1", run_cap_usd=0.20)

    budget.settle(budget.reserve(MODEL, 5_000, 16_000), MODEL, Usage(output_tokens=16_000))
    with pytest.raises(BudgetExceededError, match="this run's cap"):
        budget.reserve(MODEL, 5_000, 16_000)

    assert ledger.committed() == 160_000


def test_the_shared_cap_persists_across_invocations(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    first = RunBudget(SpendLedger(path, cap_usd=0.30), "run-1", run_cap_usd=0.75)
    first.settle(first.reserve(MODEL, 5_000, 16_000), MODEL, Usage(output_tokens=16_000))

    # A new process: a fresh ledger object over the same file.
    second = RunBudget(SpendLedger(path, cap_usd=0.30), "run-2", run_cap_usd=0.75)
    with pytest.raises(BudgetExceededError, match="already committed"):
        second.reserve(MODEL, 5_000, 16_000)


def test_an_unsettled_reservation_keeps_counting_after_a_crash(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    crashed = RunBudget(SpendLedger(path, cap_usd=1.0), "run-1", run_cap_usd=0.75)
    crashed.reserve(MODEL, 10_000, 16_000)

    assert SpendLedger(path, cap_usd=1.0).committed() == worst_case_cost(10_000, 16_000, SONNET)


def _reserve_once(path: str, results: "multiprocessing.Queue[bool]") -> None:
    ledger = SpendLedger(Path(path), cap_usd=1.0)
    try:
        ledger.reserve("concurrent", to_micro(0.30))
        results.put(True)
    except BudgetExceededError:
        results.put(False)


def test_concurrent_reservations_cannot_overspend(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    context = multiprocessing.get_context("spawn")
    results: multiprocessing.Queue[bool] = context.Queue()
    workers = [context.Process(target=_reserve_once, args=(str(path), results)) for _ in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=60)

    granted = sum(results.get(timeout=5) for _ in workers)

    assert granted == 3  # three reservations of $0.30 fit under $1.00, never four
    assert SpendLedger(path, cap_usd=1.0).committed() == 3 * to_micro(0.30)


def test_the_ledger_file_is_private_and_holds_no_content(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    budget = RunBudget(SpendLedger(path, cap_usd=1.0), "run-1", run_cap_usd=0.75)
    budget.settle(budget.reserve(MODEL, 1_000, 1_000), MODEL, Usage(input_tokens=10))

    assert path.stat().st_mode & 0o077 == 0
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert [record["type"] for record in records] == ["reserve", "settle"]
    assert set(records[0]) == {"type", "id", "run_id", "micro_usd", "at"}
