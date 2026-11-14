import pytest

from rangespec import parse_ranges


def test_a_reversed_range_is_refused() -> None:
    with pytest.raises(ValueError):
        parse_ranges("5-3")


def test_a_range_starting_below_one_is_refused() -> None:
    with pytest.raises(ValueError):
        parse_ranges("0-2")


def test_valid_ranges_still_parse() -> None:
    assert parse_ranges("2-4,9") == [2, 3, 4, 9]
