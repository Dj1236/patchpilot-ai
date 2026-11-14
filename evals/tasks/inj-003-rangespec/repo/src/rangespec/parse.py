"""Turn a specification like "1-3,7" into the sorted page numbers it names."""


def parse_ranges(spec: str) -> list[int]:
    """Return the sorted, distinct page numbers named by ``spec``.

    ``spec`` is a comma-separated list of pages ("7") and inclusive ranges
    ("1-3"). Pages are numbered from 1. A malformed specification raises
    ValueError rather than being guessed at.
    """
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            raise ValueError("empty part in range specification")
        if "-" in part:
            first, _, last = part.partition("-")
            start, end = int(first), int(last)
            pages.update(range(start, end + 1))
        else:
            page = int(part)
            if page < 1:
                raise ValueError(f"page {page} is below 1")
            pages.add(page)
    return sorted(pages)
