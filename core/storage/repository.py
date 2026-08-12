"""Repository: translate between the domain `Match` (Pydantic) and rows.

The core/API work with the domain model; persistence details stay here."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from core.models.domain import Event, Insight, Match, Metric
from core.models.enums import (
    EventCategory,
    MatchStatus,
    MetricSource,
    SourceType,
)
from core.storage.models import EventRow, MatchRow, MetricRow


class MatchRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    # --- write --------------------------------------------------------------
    def save(self, match: Match) -> None:
        row = self.s.get(MatchRow, match.id)
        if row is None:
            row = MatchRow(id=match.id)
            self.s.add(row)

        row.game_id = match.game_id
        row.game_edition = match.game_edition
        row.adapter_version = match.adapter_version
        row.source_type = match.source_type.value
        row.status = match.status.value
        row.parse_confidence = match.parse_confidence
        row.cost_usd = match.cost_usd
        row.capture = match.capture
        row.outcome = match.outcome
        row.warnings = match.warnings
        row.insights = [i.model_dump(mode="json") for i in match.insights]

        # Replace child rows wholesale (simple + correct for a re-run).
        row.metrics.clear()
        for m in match.metrics:
            row.metrics.append(
                MetricRow(
                    match_id=match.id,
                    key=m.key,
                    label=m.label,
                    value=m.value,
                    unit=m.unit,
                    higher_is_better=m.higher_is_better,
                    source=m.source.value,
                    confidence=m.confidence,
                    extra=m.extra,
                )
            )
        row.events.clear()
        for e in match.events:
            row.events.append(
                EventRow(
                    id=e.id,
                    match_id=match.id,
                    timestamp_ms=e.timestamp_ms,
                    category=e.category.value,
                    game_event_type=e.game_event_type,
                    confidence=e.confidence,
                    frame_refs=e.frame_refs,
                    payload=e.payload,
                )
            )
        self.s.flush()

    def set_status(self, match_id: str, status: MatchStatus) -> None:
        row = self.s.get(MatchRow, match_id)
        if row:
            row.status = status.value
            self.s.flush()

    # --- read ---------------------------------------------------------------
    def get(self, match_id: str) -> Match | None:
        row = self.s.execute(
            select(MatchRow)
            .where(MatchRow.id == match_id)
            .options(selectinload(MatchRow.metrics), selectinload(MatchRow.events))
        ).scalar_one_or_none()
        return self._to_domain(row) if row else None

    def list(self, game_id: str | None = None, edition: str | None = None, limit: int = 100) -> list[Match]:
        stmt = select(MatchRow).order_by(MatchRow.created_at.desc()).limit(limit)
        if game_id:
            stmt = stmt.where(MatchRow.game_id == game_id)
        if edition:
            stmt = stmt.where(MatchRow.game_edition == edition)
        stmt = stmt.options(selectinload(MatchRow.metrics), selectinload(MatchRow.events))
        return [self._to_domain(r) for r in self.s.execute(stmt).scalars().all()]

    def recurring_issues(self, identity: str, limit: int = 8) -> dict:
        """Aggregate this player's weakness tags + recent advice across their last
        `limit` COMPLETE matches — the memory that makes coaching 'learn you'."""
        if not identity:
            return {}
        stmt = (
            select(MatchRow)
            .where(MatchRow.status == MatchStatus.COMPLETE.value)
            .where(MatchRow.capture["identity"].astext == identity)
            .order_by(MatchRow.created_at.desc())
            .limit(limit)
        )
        rows = self.s.execute(stmt).scalars().all()
        from collections import Counter
        counts: dict[str, dict] = {}
        recent_advice: list[str] = []
        squad: Counter = Counter()
        formations: Counter = Counter()
        n = 0
        for i, row in enumerate(rows):
            reps = [x for x in (row.insights or []) if x.get("kind") == "coaching_report"]
            if not reps:
                continue
            n += 1
            p = reps[0].get("payload", {}) or {}
            for tag in p.get("weakness_tags", []) or []:
                e = counts.setdefault(str(tag), {"tag": str(tag), "count": 0, "last_match_ago": i})
                e["count"] += 1
            for name in p.get("roster", []) or []:      # aggregate the regular squad
                squad[str(name)] += 1
            f = str(p.get("formation") or "").strip()
            if f:
                formations[f] += 1
            if i < 2:  # advice from the 1-2 most recent matches, to check if it stuck
                recent_advice += [str(x) for x in (p.get("recurring_mistakes") or [])[:3]]
        issues = sorted(counts.values(), key=lambda e: (-e["count"], e["last_match_ago"]))
        return {
            "matches": n,
            "issues": issues[:6],
            "recent_advice": recent_advice[:6],
            # Names seen in >=2 matches = the player's real squad (also filters one-off
            # OCR misreads that never recur).
            "squad": [nm for nm, c in squad.most_common() if c >= 2][:14] or [nm for nm, _ in squad.most_common(11)],
            "formation": (formations.most_common(1)[0][0] if formations else ""),
        }

    @staticmethod
    def _to_domain(row: MatchRow) -> Match:
        return Match(
            id=row.id,
            game_id=row.game_id,
            game_edition=row.game_edition,
            adapter_version=row.adapter_version,
            source_type=SourceType(row.source_type),
            status=MatchStatus(row.status),
            capture=row.capture or {},
            outcome=row.outcome or {},
            parse_confidence=row.parse_confidence,
            cost_usd=row.cost_usd,
            warnings=row.warnings or [],
            insights=[Insight(**i) for i in (row.insights or [])],
            created_at=row.created_at,
            updated_at=row.updated_at,
            metrics=[
                Metric(
                    key=m.key,
                    label=m.label,
                    value=m.value,
                    unit=m.unit,
                    higher_is_better=m.higher_is_better,
                    source=MetricSource(m.source),
                    confidence=m.confidence,
                    extra=m.extra or {},
                )
                for m in row.metrics
            ],
            events=[
                Event(
                    id=e.id,
                    timestamp_ms=e.timestamp_ms,
                    category=EventCategory(e.category),
                    game_event_type=e.game_event_type,
                    confidence=e.confidence,
                    frame_refs=e.frame_refs or [],
                    payload=e.payload or {},
                )
                for e in row.events
            ],
        )
