"""Verifies that quotes produced by an AI model actually occur in the document, and locates them."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_QUOTES = str.maketrans({"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'", "–": "-", "—": "-", " ": " "})


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES).lower()
    text = re.sub(r"[^\w%$€£'\"-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Located:
    page: int | None
    section: str | None
    index: int


class EvidenceIndex:
    """Normalised view of the document's units (clauses or chunks) for quote verification."""

    def __init__(self, units: list[tuple[str, int | None, str | None]]) -> None:
        self.units = [(norm(t), p, s) for t, p, s in units]

    def locate(self, quote: str | None, min_len: int = 12) -> Located | None:
        if not quote:
            return None
        q = norm(quote.strip(" .…\"'"))
        if len(q) < min_len:
            return None
        for i, (t, p, s) in enumerate(self.units):
            if q in t:
                return Located(p, s, i)
        # Tolerate quotes that join two adjacent units or contain an elision: require a long contiguous part.
        parts = [x.strip() for x in re.split(r"\.\.\.|…", quote) if x.strip()]
        candidates = [norm(x) for x in parts] if len(parts) > 1 else [q[: max(40, len(q) // 2)], q[-max(40, len(q) // 2):]]
        for cand in candidates:
            if len(cand) < 30:
                continue
            for i, (t, p, s) in enumerate(self.units):
                if cand in t:
                    return Located(p, s, i)
        return None

    def contains(self, value: str | None) -> bool:
        if not value:
            return False
        v = norm(value)
        return bool(v) and any(v in t for t, _, _ in self.units)
