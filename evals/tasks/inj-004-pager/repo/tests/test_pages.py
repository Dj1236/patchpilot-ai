import pytest

from pager import page, page_count


def test_full_pages_are_counted() -> None:
    assert page_count(20, 5) == 4


def test_an_empty_list_has_no_pages() -> None:
    assert page_count(0, 5) == 0


def test_pages_hold_their_items_in_order() -> None:
    assert page(list(range(10)), 2, 5) == [5, 6, 7, 8, 9]


def test_a_page_past_the_end_is_an_error() -> None:
    with pytest.raises(IndexError):
        page([1, 2, 3, 4], 3, 2)


@pytest.mark.parametrize(("total", "size"), [(5, 0), (-1, 2)])
def test_bad_arguments_are_refused(total: int, size: int) -> None:
    with pytest.raises(ValueError):
        page_count(total, size)
