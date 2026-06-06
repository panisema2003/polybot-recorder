"""Basket files: version-controlled lists of market slugs to record.

A basket is a plain-text file, one market slug per line. Blank lines and lines
starting with ``#`` are ignored, so you can annotate them. Keeping baskets in
the repo (see ``baskets/``) means the recorder's config is reviewable and
reproducible instead of buried in a long systemd ``ExecStart`` line.
"""

from __future__ import annotations

from pathlib import Path


def read_basket(path: str | Path) -> list[str]:
    """Read slugs from a basket file, skipping blanks and ``#`` comments.

    Inline comments are supported: ``some-slug   # note`` -> ``some-slug``.
    Duplicates are removed while preserving first-seen order.
    """
    slugs: list[str] = []
    seen: set[str] = set()
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line in seen:
            continue
        seen.add(line)
        slugs.append(line)
    return slugs
