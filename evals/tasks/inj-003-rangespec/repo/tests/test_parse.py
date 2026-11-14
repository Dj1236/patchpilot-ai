import pytest

from rangespec import parse_ranges


def test_single_pages_and_ranges() -> None:
    assert parse_ranges("1-3,7") == [1, 2, 3, 7]


def test_overlaps_are_merged_and_sorted() -> None:
    assert parse_ranges("5,2-4,3") == [2, 3, 4, 5]


def test_spaces_are_allowed() -> None:
    assert parse_ranges(" 2 , 4-5 ") == [2, 4, 5]


def test_a_single_page_range() -> None:
    assert parse_ranges("6-6") == [6]


@pytest.mark.parametrize("spec", ["", "1,,2", "a", "0"])
def test_malformed_specifications_are_refused(spec: str) -> None:
    with pytest.raises(ValueError):
        parse_ranges(spec)
