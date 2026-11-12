# cachekit

A small in-memory cache with least-recently-used eviction and an optional
time-to-live per entry.

```python
from cachekit import LRUCache

cache = LRUCache(capacity=2)
cache.put("a", 1)
cache.put("b", 2)
cache.get("a")       # 1, and "a" becomes the most recently used entry
cache.put("c", 3)    # evicts "b", the least recently used entry
```

Run the tests with `python -m pytest tests`.
