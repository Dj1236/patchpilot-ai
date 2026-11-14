"""Split a list into pages of a fixed size, numbered from 1."""

from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


def page_count(total: int, size: int) -> int:
    """Return how many pages ``total`` items fill, at ``size`` items per page."""
    if size < 1:
        raise ValueError("page size must be at least 1")
    if total < 0:
        raise ValueError("total cannot be negative")
    return total // size


def page(items: Sequence[T], number: int, size: int) -> list[T]:
    """Return page ``number`` (from 1) of ``items``. A page past the end is an error."""
    if number < 1 or number > page_count(len(items), size):
        raise IndexError(f"there is no page {number}")
    start = (number - 1) * size
    return list(items[start : start + size])
