"""Trend calculation - game-agnostic.

Works purely off `Match.metrics` and `metric.higher_is_better`. It never knows
whether a metric is possession or accuracy, so this same code serves every
game. `higher_is_better` is what lets it call a direction 'improvement' without
understanding the metric.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.models.domain import Match


@dataclass
class TrendPoint:
    match_id: str
    created_at: str
    value: float


@dataclass
class MetricTrend:
    key: str
    label: str
    unit: str | None
    higher_is_better: bool | None
    points: list[TrendPoint] = field(default_factory=list)

    @property
    def latest(self) -> float | None:
        return self.points[-1].value if self.points else None

    @property
    def previous(self) -> float | None:
        return self.points[-2].value if len(self.points) >= 2 else None

    @property
    def delta(self) -> float | None:
        if self.latest is None or self.previous is None:
            return None
        return round(self.latest - self.previous, 3)

    @property
    def improving(self) -> bool | None:
        if self.delta is None or self.higher_is_better is None or self.delta == 0:
            return None
        return (self.delta > 0) == self.higher_is_better

    @property
    def average(self) -> float | None:
        if not self.points:
            return None
        return round(sum(p.value for p in self.points) / len(self.points), 3)


def build_trends(matches: list[Match]) -> list[MetricTrend]:
    """Given matches oldest→newest-agnostic, produce one trend per metric key."""
    ordered = sorted(matches, key=lambda m: m.created_at)
    trends: dict[str, MetricTrend] = {}
    for match in ordered:
        for m in match.metrics:
            t = trends.get(m.key)
            if t is None:
                t = MetricTrend(
                    key=m.key,
                    label=m.label,
                    unit=m.unit,
                    higher_is_better=m.higher_is_better,
                )
                trends[m.key] = t
            t.points.append(
                TrendPoint(
                    match_id=match.id,
                    created_at=match.created_at.isoformat(),
                    value=m.value,
                )
            )
    return list(trends.values())
