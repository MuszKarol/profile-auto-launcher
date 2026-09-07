"""One matching rule, shared by everything that has a search box.

The HUD, the manager's launch page and `palaunch app` all rank the same way,
so a query that finds something in one of them finds it in the others.
"""

from __future__ import annotations

from collections.abc import Iterable

PREFIX = 100
WORD_START = 80
SUBSTRING = 60
SUBSEQUENCE = 30


def score(query: str, text: str) -> int | None:
    """Rank a match: prefix > word start > substring > subsequence > miss."""
    if not query:
        return 0
    if not text:
        return None
    query, text = query.lower(), text.lower()
    if text.startswith(query):
        return PREFIX
    if any(word.startswith(query) for word in text.replace("-", " ").replace("_", " ").split()):
        return WORD_START
    if query in text:
        return SUBSTRING
    remaining = iter(text)
    if all(char in remaining for char in query):
        return SUBSEQUENCE
    return None


def best(query: str, texts: Iterable[str]) -> int | None:
    """The strongest score across several fields — name, description, tags."""
    hits = [s for s in (score(query, text) for text in texts) if s is not None]
    return max(hits) if hits else None
