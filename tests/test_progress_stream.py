"""Which progress events end the analysing screen.

Regression cover for a false failure: the SSE stream closed on ANY event whose
`status` was complete/failed/over_budget. Stages emit "failed" for recoverable
problems (a scoreboard read that didn't land, a roster OCR miss) and the run
carries on - so the stream closed mid-run, the client re-read a match still in
"processing", and reported a failure for an analysis that went on to succeed.

Only the pipeline speaks for the match.
"""
from __future__ import annotations

from api.routes.matches import is_terminal_event


class TestNonTerminalStageEvents:
    """A stage failing is not the match failing."""

    def test_stage_failure_does_not_end_the_stream(self):
        assert not is_terminal_event({"stage": "gemini_video_coaching", "status": "failed"})

    def test_score_read_failure_does_not_end_the_stream(self):
        assert not is_terminal_event({"stage": "score_timeline", "status": "failed"})

    def test_stage_completion_does_not_end_the_stream(self):
        """A stage finishing its own step is not the run finishing."""
        assert not is_terminal_event({"stage": "roster", "status": "complete"})
        assert not is_terminal_event({"stage": "gemini_video_coaching", "status": "done"})

    def test_ordinary_progress_is_not_terminal(self):
        for status in ("running", "uploading", "processing", "compressing",
                       "scoring", "consensus", "synthesising", "retry", "skipped"):
            assert not is_terminal_event({"stage": "gemini_video_coaching", "status": status})

    def test_empty_event_is_not_terminal(self):
        assert not is_terminal_event({})


class TestTerminalPipelineEvents:
    def test_pipeline_completion_ends_the_stream(self):
        assert is_terminal_event({"stage": "pipeline", "status": "complete"})

    def test_pipeline_failure_ends_the_stream(self):
        assert is_terminal_event({"stage": "pipeline", "status": "failed"})

    def test_pipeline_over_budget_ends_the_stream(self):
        assert is_terminal_event({"stage": "pipeline", "status": "over_budget"})

    def test_workers_final_event_ends_the_stream(self):
        """workers/tasks.py emits stage=pipeline status=final at the very end."""
        assert is_terminal_event({"stage": "pipeline", "status": "final"})

    def test_explicit_match_status_ends_the_stream_from_any_stage(self):
        """The worker's final event carries match_status; trust it wherever it
        comes from, since it is the match's own verdict."""
        assert is_terminal_event({"stage": "pipeline", "status": "final",
                                  "match_status": "complete"})
        assert is_terminal_event({"stage": "anything", "match_status": "failed"})
