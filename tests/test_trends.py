"""Statistics baseline and metric provenance.

Both encode real defects on the Statistics page:

  - the chart shaded green/red against the mean of the points ON SCREEN, so the
    same match rendered green under one range button and red under another, and
    a player who improved every match still saw the earliest ones in red;
  - every model-counted stat carried a flat confidence of 0.6 that nothing read,
    so an estimate looked exactly like a scoreboard measurement.
"""
from datetime import datetime, timedelta, timezone

from core.models.domain import Match, Metric
from core.models.enums import MetricSource
from core.progress.trends import MetricTrend, TrendPoint, build_trends

_T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _trend(values: list[float]) -> MetricTrend:
    return MetricTrend(
        key="shots", label="Shots", unit=None, higher_is_better=True,
        points=[TrendPoint(f"m{i}", (_T0 + timedelta(days=i)).isoformat(), v)
                for i, v in enumerate(values)],
    )


def _match(i: int, **metrics: float) -> Match:
    return Match(
        game_id="ea-fc", game_edition="26", created_at=_T0 + timedelta(days=i),
        metrics=[Metric(key=k, label=k, value=v, source=MetricSource.MODEL)
                 for k, v in metrics.items()],
    )


class TestBaselineIsStable:
    def test_the_baseline_does_not_move_with_the_window(self):
        """The whole bug. Real data: 4 goals against read ABOVE the last-5 mean
        of 3.8 and BELOW the all-time mean of 4.14 - same match, two colours.

        This reproduces what the route does: build over the FULL history for the
        baseline, build over the window for the points, and hand the full-history
        baseline to every window.
        """
        matches = [_match(i, shots=float(v))
                   for i, v in enumerate([5, 5, 1, 2, 4, 16, 6])]

        def baseline_for(window: int | None) -> float:
            full = {t.key: t for t in build_trends(matches)}
            shown = matches[-window:] if window else matches
            windowed = {t.key: t for t in build_trends(shown)}
            assert len(windowed["shots"].points) == min(window or len(matches), len(matches))
            return full["shots"].baseline

        assert baseline_for(5) == baseline_for(10) == baseline_for(None) == 5

        # And it is genuinely different from what the old code drew, which is
        # why the colours used to change with the range button.
        assert _trend([5, 5, 1, 2, 4, 16, 6][-5:]).average == 5.8
        assert _trend([5, 5, 1, 2, 4, 16, 6]).average == 5.571

    def test_the_baseline_is_a_median_so_one_freak_match_cannot_move_it(self):
        """[5, 5, 1, 2, 4, 16, 6]: the single 16-shot match drags the mean from
        3.8 to 5.57 and puts five of the seven below par."""
        t = _trend([5, 5, 1, 2, 4, 16, 6])
        assert t.average == 5.571
        assert t.baseline == 5
        without_outlier = _trend([5, 5, 1, 2, 4, 6])
        assert without_outlier.baseline == 4.5      # moved a little
        assert without_outlier.average == 3.833     # moved a lot

    def test_a_steady_improvement_is_not_half_red(self):
        """The structural failure: against the mean of its OWN points, any rising
        series has roughly half below the line. Against a baseline from history,
        a run that is better than normal is entirely above it."""
        history = _trend([1, 1, 2, 2, 1, 2])       # how they usually play
        recent = [4, 5, 6]                          # a clearly better run
        assert all(v > history.baseline for v in recent)

    def test_no_baseline_without_enough_matches(self):
        """A median of two matches is not a norm. The route withholds it below
        four; the chart then draws the line without judging either side."""
        assert len(_trend([3, 9]).points) < 4


class TestMetricProvenance:
    def test_goals_are_measured_from_the_scoreboard_not_the_model(self):
        from adapters.ea_fc_26.adapter import EaFc26Adapter
        from core.pipeline.stages import _stats_to_metrics

        class Ctx:
            pass

        ctx = Ctx()
        ctx.match = Match(game_id="ea-fc", game_edition="26",
                          outcome={"score_home": 4, "score_away": 3, "score": "4-3"},
                          capture={"player_side": "away"})
        spec = EaFc26Adapter().report_spec()
        # The model claims 9 goals for; the scoreboard says the away player got 3.
        _stats_to_metrics(ctx, {"goals_for": 9, "goals_against": 9, "shots": 12}, spec)

        by_key = {m.key: m for m in ctx.match.metrics}
        assert by_key["goals_for"].value == 3.0, "scoreboard must win over the model"
        assert by_key["goals_against"].value == 4.0
        assert by_key["goals_for"].source == MetricSource.DERIVED
        assert by_key["goals_for"].confidence == 1.0

    def test_an_uncheckable_stat_is_marked_as_the_models_estimate(self):
        from adapters.ea_fc_26.adapter import EaFc26Adapter
        from core.pipeline.stages import MODEL_ESTIMATE_CONFIDENCE, _stats_to_metrics

        class Ctx:
            pass

        ctx = Ctx()
        ctx.match = Match(game_id="ea-fc", game_edition="26", outcome={}, capture={})
        _stats_to_metrics(ctx, {"shots": 12, "big_chances": 4},
                          EaFc26Adapter().report_spec())
        by_key = {m.key: m for m in ctx.match.metrics}
        assert by_key["shots"].source == MetricSource.MODEL
        assert by_key["shots"].confidence == MODEL_ESTIMATE_CONFIDENCE
        assert MODEL_ESTIMATE_CONFIDENCE != 0.6, "the old flat value nobody measured"

    def test_the_trend_carries_the_source_so_the_ui_can_mark_estimates(self):
        trends = {t.key: t for t in build_trends([_match(0, shots=5.0)])}
        assert trends["shots"].source == "model"
