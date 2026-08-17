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
    # How this metric was obtained ("ocr" / "model" / "derived"). Carried so the
    # UI can say which numbers were MEASURED and which the coach estimated while
    # watching - they are rendered identically otherwise, and they should not be.
    source: str | None = None
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

    @property
    def baseline(self) -> float | None:
        """Your normal for this metric: the MEDIAN across every match.

        Two deliberate differences from `average`.

        It is a MEDIAN, because the mean is dragged by one freak match. A real
        run of shots was [5, 5, 1, 2, 4, 16, 6]: the single 16-shot match moved
        the mean from 3.8 to 5.6 and made five of the seven look below par.

        And it is computed over the WHOLE history, never the selected window.
        The chart fills green above this line and red below it, so a baseline
        drawn from the visible points alone put roughly half of them on each
        side no matter what - a player who improved in every one of their last
        five matches still saw the earliest ones in red, because they sat below
        the mean of that same set. Comparing five matches to themselves cannot
        show progress; comparing them to your normal can.
        """
        vals = sorted(p.value for p in self.points)
        if not vals:
            return None
        mid = len(vals) // 2
        med = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2
        return round(med, 3)


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
                    source=getattr(m.source, "value", m.source),
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
