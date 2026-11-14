from pager import page, page_count


def test_a_partial_last_page_is_counted() -> None:
    assert page_count(11, 5) == 3


def test_the_partial_last_page_can_be_read() -> None:
    assert page(list(range(11)), 3, 5) == [10]


def test_exact_multiples_are_unchanged() -> None:
    assert page_count(10, 5) == 2
