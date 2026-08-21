"""The dev sign-in endpoints must close the moment a real provider is connected.

`POST /api/auth/signin` sets a session cookie for any email, no password - the
least-bad option while nothing could verify identity, and a bypass the moment
Supabase can. These tests pin the switch itself; the live flow is exercised
against the running stack.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from api.routes import auth as auth_routes


def _supabase(monkeypatch, on: bool) -> None:
    monkeypatch.setattr(auth_routes._settings, "supabase_url",
                        "https://example.supabase.co" if on else "")
    monkeypatch.setattr(auth_routes._settings, "supabase_anon_key",
                        "anon-key" if on else "")


class TestSigninGate:
    def test_refused_once_supabase_is_connected(self, monkeypatch):
        _supabase(monkeypatch, on=True)
        with pytest.raises(HTTPException) as exc:
            auth_routes._refuse_unverified_signin()
        assert exc.value.status_code == 410
        # The message must say where sign-in lives now, not just refuse.
        assert "sign-in" in str(exc.value.detail).lower()

    def test_allowed_while_no_provider_exists(self, monkeypatch):
        """Without a provider the dev endpoints are the only sign-in there is -
        refusing would lock everyone out of local development."""
        _supabase(monkeypatch, on=False)
        auth_routes._refuse_unverified_signin()  # must not raise


class TestSignInMethods:
    def test_dev_email_is_the_only_method_without_supabase(self, monkeypatch):
        _supabase(monkeypatch, on=False)
        assert auth_routes.sign_in_methods() == {
            "dev_email": True, "magic_link": False, "oauth": []}

    def test_reflects_what_supabase_has_enabled(self, monkeypatch):
        _supabase(monkeypatch, on=True)
        monkeypatch.setattr(auth_routes, "_methods_cache", None)
        monkeypatch.setattr(
            auth_routes, "auth_settings",
            lambda s: {"external": {"email": True, "discord": True, "google": False,
                                    "github": True}})  # github: on but unsupported
        out = auth_routes.sign_in_methods()
        assert out["dev_email"] is False
        assert out["magic_link"] is True
        assert out["oauth"] == ["discord"]   # only providers we have buttons for

    def test_supabase_outage_answers_optimistically(self, monkeypatch):
        """A blip must degrade to buttons-that-may-fail, never to a sign-in page
        with no way in."""
        from core.auth.supabase import SupabaseError

        _supabase(monkeypatch, on=True)
        monkeypatch.setattr(auth_routes, "_methods_cache", None)

        def boom(_):
            raise SupabaseError("down")

        monkeypatch.setattr(auth_routes, "auth_settings", boom)
        out = auth_routes.sign_in_methods()
        assert out["magic_link"] is True
        assert out["oauth"] == ["google", "discord"]
