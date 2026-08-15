"""Guards the test-isolation fixture in conftest.py.

Without it, `Settings()` inherits the developer's environment and the project
`.env`, so the suite's result depends on who runs it. That already happened:
a middle-mode `.env` (GEMINI_VIDEO_TWO_PASS=false) made two stage tests fail on a
clean checkout with no code defect involved.

If this file starts failing, the suite has become environment-dependent again.
"""
from __future__ import annotations

from core.config import Settings


def test_settings_use_declared_defaults_not_the_ambient_env():
    s = Settings()
    # These are the fields whose ambient values differ from the declared defaults
    # in this project's .env - the exact leak that broke the suite.
    assert s.gemini_video_two_pass is True
    assert s.vision_engine == "stub"
    assert s.match_budget_usd == 0.25


def test_explicit_values_still_win():
    """Isolation must not stop a test configuring what it is actually testing."""
    s = Settings(gemini_video_two_pass=False, match_budget_usd=1.5)
    assert s.gemini_video_two_pass is False
    assert s.match_budget_usd == 1.5
