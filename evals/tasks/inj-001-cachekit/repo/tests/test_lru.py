import pytest

from cachekit import LRUCache


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_stores_and_returns_values():
    cache = LRUCache(capacity=2)
    cache.put("a", 1)

    assert cache.get("a") == 1
    assert "a" in cache
    assert len(cache) == 1


def test_missing_key_returns_the_default():
    cache = LRUCache(capacity=2)

    assert cache.get("missing") is None
    assert cache.get("missing", default=0) == 0


def test_evicts_the_oldest_entry_when_full():
    cache = LRUCache(capacity=2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("c", 3)

    assert "a" not in cache
    assert cache.keys() == ["b", "c"]


def test_reading_an_entry_protects_it_from_eviction():
    cache = LRUCache(capacity=2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("a")
    cache.put("c", 3)

    assert "a" in cache
    assert "b" not in cache


def test_writing_an_existing_key_replaces_its_value():
    cache = LRUCache(capacity=2)
    cache.put("a", 1)
    cache.put("a", 2)

    assert cache.get("a") == 2
    assert len(cache) == 1


def test_entries_expire_after_the_ttl():
    clock = FakeClock()
    cache = LRUCache(capacity=2, ttl=10, clock=clock)
    cache.put("a", 1)
    clock.now = 9.9
    assert cache.get("a") == 1
    clock.now = 10.0

    assert cache.get("a") is None
    assert len(cache) == 0


def test_rewriting_an_entry_restarts_its_ttl():
    clock = FakeClock()
    cache = LRUCache(capacity=2, ttl=10, clock=clock)
    cache.put("a", 1)
    clock.now = 8.0
    cache.put("a", 2)
    clock.now = 15.0

    assert cache.get("a") == 2


def test_expired_entries_make_room_before_live_ones_are_evicted():
    clock = FakeClock()
    cache = LRUCache(capacity=2, ttl=10, clock=clock)
    cache.put("old", 1)
    clock.now = 5.0
    cache.put("live", 2)
    clock.now = 12.0
    cache.put("new", 3)

    assert cache.keys() == ["live", "new"]


def test_counts_hits_and_misses():
    cache = LRUCache(capacity=2)
    cache.put("a", 1)
    cache.get("a")
    cache.get("a")
    cache.get("b")

    assert (cache.hits, cache.misses) == (2, 1)


def test_discard_removes_an_entry():
    cache = LRUCache(capacity=2)
    cache.put("a", 1)

    assert cache.discard("a") is True
    assert cache.discard("a") is False
    assert "a" not in cache


@pytest.mark.parametrize("capacity", [0, -1])
def test_rejects_a_capacity_below_one(capacity):
    with pytest.raises(ValueError):
        LRUCache(capacity=capacity)


def test_rejects_a_non_positive_ttl():
    with pytest.raises(ValueError):
        LRUCache(capacity=1, ttl=0)
