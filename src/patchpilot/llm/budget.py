"""Spend control for paid model calls.

Money is counted in micro-dollars, which makes token prices exact integers:
one token at 2 USD per million tokens costs 2 micro-dollars.

Every request goes through reserve -> send -> settle:

- Before sending, a conservative worst-case amount is reserved. If it does
  not fit under the cap, the request is not sent.
- After a response, the reservation is settled at the actual cost.
- After an error, the reservation is settled at the full reserved amount. A
  failed request may still have been billed, so it is never assumed free.

The ledger is an append-only file shared by every run. Reservations are made
under an exclusive file lock, so two processes cannot both reserve the last
of the budget. A reservation that is never settled (a crash) keeps counting
at its full amount.
"""

import fcntl
import json
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field

from patchpilot.llm.base import Usage
from patchpilot.sandbox.models import FrozenModel

MICRO = 1_000_000
PRICES_AS_OF = "2026-11-03"


class Prices(FrozenModel):
    """USD per million tokens. Thinking tokens are billed as output tokens."""

    input: int = Field(ge=0)
    output: int = Field(ge=0)
    cache_write: float = Field(ge=0)
    cache_read: float = Field(ge=0)


# Read from the provider's published prices on PRICES_AS_OF. Cache writes use
# the 5-minute rate, 1.25 times the input price.
PRICES: dict[str, Prices] = {
    "claude-sonnet-5-5": Prices(input=2, output=10, cache_write=2.5, cache_read=0.10),
    # Used on the free tier, where nothing is charged while the key's project
    # has no billing account. These are the paid list prices that apply from
    # 2027-01-01 (1.50 and 7.50 USD), rounded up, so the ledger still bounds
    # what a run could cost if billing turned out to be enabled.
    "gemini-3.8-flash": Prices(input=2, output=8, cache_write=2, cache_read=0.15),
}


class BudgetExceededError(Exception):
    """A request was not sent because it could exceed a spending cap."""


class UnknownModelPriceError(KeyError):
    """No price is known for the model, so its cost cannot be bounded."""


def usd(micro: int) -> float:
    return micro / MICRO


def to_micro(dollars: float) -> int:
    return round(dollars * MICRO)


def prices_for(model: str) -> Prices:
    try:
        return PRICES[model]
    except KeyError:
        raise UnknownModelPriceError(f"no price is configured for model {model!r}") from None


def actual_cost(usage: Usage, prices: Prices) -> int:
    """Return the cost in micro-dollars of the usage a response reported."""
    total = (
        usage.input_tokens * prices.input
        + usage.output_tokens * prices.output
        + usage.cache_creation_input_tokens * prices.cache_write
        + usage.cache_read_input_tokens * prices.cache_read
    )
    return int(-(-total // 1))  # round up to a whole micro-dollar


def worst_case_cost(input_tokens: int, max_output_tokens: int, prices: Prices) -> int:
    """Return an upper bound for one request.

    Every input token is priced at the dearest input rate (a cache write),
    and the response is assumed to use its whole output limit, which also
    bounds thinking tokens.
    """
    dearest_input = max(prices.input, prices.cache_write)
    return int(-(-(input_tokens * dearest_input + max_output_tokens * prices.output) // 1))


class SpendLedger:
    """Append-only record of reservations and settlements, safe across processes."""

    def __init__(self, path: Path, cap_usd: float) -> None:
        self._path = path
        self._cap = to_micro(cap_usd)
        path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def cap(self) -> int:
        return self._cap

    @contextmanager
    def _locked(self) -> Iterator[None]:
        lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _records(self) -> list[dict[str, object]]:
        if not self._path.exists():
            return []
        records = []
        for line in self._path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                int(str(record["micro_usd"])), str(record["id"])
            except (ValueError, KeyError, TypeError):
                # Spend that cannot be counted must not be added to.
                raise BudgetExceededError(
                    f"the spend ledger {self._path} is unreadable; refusing to spend"
                ) from None
            records.append(record)
        return records

    def _append(self, record: dict[str, object]) -> None:
        descriptor = os.open(self._path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "a") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _committed(records: list[dict[str, object]]) -> int:
        """Settled amounts plus the full amount of every open reservation."""
        amounts: dict[str, int] = {}
        for record in records:
            identifier = str(record["id"])
            amounts[identifier] = int(str(record["micro_usd"]))
        return sum(amounts.values())

    def committed(self) -> int:
        with self._locked():
            return self._committed(self._records())

    def reserve(self, run_id: str, micro_usd: int) -> str:
        """Reserve ``micro_usd`` or raise ``BudgetExceededError``. Nothing is sent before this."""
        if micro_usd < 0:
            raise ValueError("a reservation cannot be negative")
        with self._locked():
            committed = self._committed(self._records())
            if committed + micro_usd > self._cap:
                raise BudgetExceededError(
                    f"a request that could cost up to ${usd(micro_usd):.4f} does not fit: "
                    f"${usd(committed):.4f} of ${usd(self._cap):.2f} is already committed"
                )
            identifier = uuid.uuid4().hex
            self._append(
                {
                    "type": "reserve",
                    "id": identifier,
                    "run_id": run_id,
                    "micro_usd": micro_usd,
                    "at": datetime.now(UTC).isoformat(),
                }
            )
            return identifier

    def settle(self, reservation_id: str, micro_usd: int, *, note: str = "") -> None:
        """Replace a reservation with what the request actually cost."""
        with self._locked():
            self._append(
                {
                    "type": "settle",
                    "id": reservation_id,
                    "micro_usd": max(micro_usd, 0),
                    "note": note,
                    "at": datetime.now(UTC).isoformat(),
                }
            )


class RunBudget:
    """The spending guard for one run: a per-run cap on top of the shared ledger."""

    def __init__(self, ledger: SpendLedger, run_id: str, run_cap_usd: float) -> None:
        self._ledger = ledger
        self._run_id = run_id
        self._run_cap = to_micro(run_cap_usd)
        self._open: dict[str, int] = {}
        self.spent = 0
        self.requests = 0

    def reserve(self, model: str, input_tokens: int, max_output_tokens: int) -> str:
        """Reserve the worst case for one request, against both caps."""
        estimate = worst_case_cost(input_tokens, max_output_tokens, prices_for(model))
        if self.spent + sum(self._open.values()) + estimate > self._run_cap:
            raise BudgetExceededError(
                f"a request that could cost up to ${usd(estimate):.4f} would exceed "
                f"this run's cap of ${usd(self._run_cap):.2f} (spent ${usd(self.spent):.4f})"
            )
        reservation = self._ledger.reserve(self._run_id, estimate)
        self._open[reservation] = estimate
        return reservation

    def settle(self, reservation: str, model: str, usage: Usage) -> int:
        """Record what a successful request cost. Returns the cost."""
        cost = actual_cost(usage, prices_for(model))
        self._close(reservation, cost, "response")
        return cost

    def settle_failed(self, reservation: str, reason: str) -> int:
        """Charge the full reservation for a failed request: it may have been billed."""
        cost = self._open[reservation]
        self._close(reservation, cost, f"failed: {reason}")
        return cost

    def _close(self, reservation: str, cost: int, note: str) -> None:
        self._open.pop(reservation)
        self._ledger.settle(reservation, cost, note=note)
        self.spent += cost
        self.requests += 1
