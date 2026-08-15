"""Test isolation for configuration.

`Settings` is a pydantic-settings `BaseSettings` with `env_file=".env"`, so any
field a test does not pass explicitly is silently filled from the developer's
environment AND from the project `.env` file. Tests run in the container with
/app as the working directory, so `.env` sits right next to them.

That made the documented command in SETUP.md fail on a clean checkout: a
middle-mode `.env` sets GEMINI_VIDEO_TWO_PASS=false, which sent
`GeminiVideoCoaching.run()` down the single-call branch while two tests asserted
two-pass behaviour. Two red tests, no code defect, no hint as to why - and the
result flips depending on who runs it.

This fixture pins every test to the DECLARED defaults so results are the same on
every machine. A test that wants non-default configuration passes it explicitly
to `Settings(...)`, which still works.
"""
from __future__ import annotations

import pytest

from core import config as _config
from core.config import Settings

# pydantic-settings matches env vars case-insensitively, so clear both spellings.
_ENV_NAMES = [n for f in Settings.model_fields for n in (f.upper(), f.lower())]


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    # Stop BaseSettings reading the project .env file as well.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    # get_settings() memoises into a module global, so a settings object built
    # from the real environment must not leak across tests.
    monkeypatch.setattr(_config, "_settings", None, raising=False)
    yield
