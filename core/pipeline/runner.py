"""Stage orchestration. Runs enabled stages in cost order, enforces the budget,
and always records final cost + status on the match."""
from __future__ import annotations

import logging

from core.models.enums import MatchStatus
from core.pipeline.context import PipelineContext
from core.pipeline.cost import BudgetExceeded
from core.pipeline.stages import DEFAULT_STAGES, Stage

log = logging.getLogger("coachio.pipeline")


def run_pipeline(ctx: PipelineContext, stages: list[Stage] | None = None) -> None:
    stages = stages or DEFAULT_STAGES
    ctx.match.status = MatchStatus.PROCESSING
    ctx.emit("pipeline", "start", f"{len(ctx.frames)} frames, cap=${ctx.cost.cap_usd:.4f}")

    try:
        for stage in stages:
            if not stage.enabled(ctx):
                ctx.emit(stage.name, "skipped", "disabled for this phase")
                continue
            stage.run(ctx)

        # Only mark complete if no stage flagged a hard failure (e.g. the video
        # analysis couldn't produce a report). Otherwise leave the failure status.
        if ctx.match.status == MatchStatus.PROCESSING:
            ctx.match.status = MatchStatus.COMPLETE
            ctx.emit("pipeline", "complete", f"cost=${ctx.cost.total:.4f}")
        else:
            ctx.emit("pipeline", ctx.match.status.value, "analysis did not produce a report")

    except BudgetExceeded as exc:
        # Fail loudly and visibly - do not produce partial paid output silently.
        ctx.match.status = MatchStatus.OVER_BUDGET
        ctx.match.warnings.append(str(exc))
        log.error("BUDGET EXCEEDED on match %s: %s", ctx.match.id, exc)
        ctx.emit("pipeline", "over_budget", str(exc))

    except Exception as exc:  # noqa: BLE001
        ctx.match.status = MatchStatus.FAILED
        ctx.match.warnings.append(f"pipeline error: {exc}")
        log.exception("pipeline failed for match %s", ctx.match.id)
        ctx.emit("pipeline", "failed", str(exc))
        raise

    finally:
        ctx.match.cost_usd = ctx.cost.total
        ctx.match.outcome["cost_breakdown"] = ctx.cost.breakdown()
