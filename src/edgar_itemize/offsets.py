"""Map offsets in normalized text back to byte offsets in the raw submission."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field


@dataclass(slots=True)
class OffsetMap:
    """Piecewise map: segment i covers norm [ns, ns+len) -> raw [rs, rs+len)."""

    norm_starts: list[int] = field(default_factory=list)
    raw_starts: list[int] = field(default_factory=list)
    lengths: list[int] = field(default_factory=list)

    def add(self, norm_start: int, raw_start: int, length: int) -> None:
        if length <= 0:
            return
        # merge with previous if contiguous on both sides
        if self.norm_starts:
            pn = self.norm_starts[-1] + self.lengths[-1]
            pr = self.raw_starts[-1] + self.lengths[-1]
            if pn == norm_start and pr == raw_start:
                self.lengths[-1] += length
                return
        self.norm_starts.append(norm_start)
        self.raw_starts.append(raw_start)
        self.lengths.append(length)

    def norm_to_raw(self, n: int) -> int:
        if not self.norm_starts:
            return n
        i = bisect_right(self.norm_starts, n) - 1
        if i < 0:
            return self.raw_starts[0]
        ns, rs, ln = self.norm_starts[i], self.raw_starts[i], self.lengths[i]
        if n < ns + ln:
            return rs + (n - ns)
        # in a gap (collapsed whitespace / decoded entity): snap to next segment start
        if i + 1 < len(self.norm_starts):
            return self.raw_starts[i + 1]
        return rs + ln
