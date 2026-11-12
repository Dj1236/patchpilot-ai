"""Hidden regression tests. Never copied into an agent workspace."""

from cachekit import LRUCache


def test_rewriting_an_entry_protects_it_from_eviction():
    cache = LRUCache(capacity=2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("a", 10)
    cache.put("c", 3)

    assert "a" in cache
    assert cache.get("a") == 10
    assert "b" not in cache


def test_rewritten_entry_becomes_the_most_recently_used():
    cache = LRUCache(capacity=3)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("c", 3)
    cache.put("a", 10)

    assert cache.keys() == ["b", "c", "a"]


def test_repeated_rewrites_keep_a_hot_key_alive():
    cache = LRUCache(capacity=2)
    cache.put("hot", 0)
    for number in range(1, 6):
        cache.put(f"cold{number}", number)
        assert "hot" in cache
        cache.put("hot", number)

    assert cache.get("hot") == 5
    assert len(cache) == 2


def test_rewriting_the_newest_entry_changes_nothing_else():
    cache = LRUCache(capacity=2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("b", 20)
    cache.put("c", 3)

    assert cache.keys() == ["b", "c"]
    assert cache.get("b") == 20
