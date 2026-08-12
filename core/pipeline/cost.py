"""Cost accounting + hard budget enforcement.

The brief's line in the sand: total API cost per 15-minute match must stay under
$0.25. We scale the cap linearly with match length, log every charge, and FAIL
LOUDLY the instant a charge would breach the cap — never silently overspend.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class BudgetExceeded(RuntimeError):
    def __init__(self, cap: float, attempted: float, label: str) -> None:
        super().__init__(
            f"Match budget ${cap:.4f} would be exceeded by '{label}' "
            f"(attempted total ${attempted:.4f}). Halting."
        )
        self.cap = cap
        self.attempted = attempted
        self.label = label


@dataclass
class Charge:
    label: str
    usd: float


@dataclass
class CostAccountant:
    cap_usd: float
    charges: list[Charge] = field(default_factory=list)

    @classmethod
    def for_match(cls, base_cap_usd: float, duration_ms: int | None) -> "CostAccountant":
        """Scale the $0.25/15-min cap to the match's actual length."""
        minutes = (duration_ms or 0) / 60000.0
        factor = max(1.0, minutes / 15.0) if minutes else 1.0
        return cls(cap_usd=round(base_cap_usd * factor, 4))

    @property
    def total(self) -> float:
        return round(sum(c.usd for c in self.charges), 6)

    @property
    def remaining(self) -> float:
        return round(self.cap_usd - self.total, 6)

    def charge(self, label: str, usd: float) -> None:
        """Record a charge, or raise BudgetExceeded before it lands."""
        prospective = self.total + usd
        if prospective > self.cap_usd + 1e-9:
            raise BudgetExceeded(self.cap_usd, prospective, label)
        self.charges.append(Charge(label=label, usd=usd))

    def breakdown(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for c in self.charges:
            out[c.label] = round(out.get(c.label, 0.0) + c.usd, 6)
        return out
