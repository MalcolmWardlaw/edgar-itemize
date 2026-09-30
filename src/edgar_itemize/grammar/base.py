"""Grammar protocol: what labels exist, how they order, and how they nest."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import regex


@dataclass(frozen=True, slots=True)
class LevelSpec:
    kind: str  # e.g. "part", "item", "article", "section", "clause_alpha"
    depth: int  # nominal depth under the document node (document = 0)
    pattern: regex.Pattern  # matched against block text (after normalization); must anchor at start
    canonical_only: bool = False  # only accept labels the grammar knows (10-K items)
    inline_ok: bool = False  # heading may share its paragraph with body text (contract sections/clauses)


class Grammar(Protocol):
    name: str
    levels: tuple[LevelSpec, ...]

    def canonicalize(self, kind: str, m: regex.Match) -> str | None:
        """Return canonical label (e.g. 'ITEM 7A') or None if not acceptable."""

    def order_key(self, kind: str, canon: str) -> int:
        """Ordinal for monotonicity checks within the level."""

    def parent_kind(self, kind: str) -> str | None:
        """Kind of the level that must contain this one (None = document)."""
