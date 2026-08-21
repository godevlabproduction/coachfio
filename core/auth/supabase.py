"""Supabase as the identity provider. Identity only - no application data.

WHY THE USER ENDPOINT RATHER THAN LOCAL JWT VERIFICATION. Supabase signs access
tokens with ES256 and publishes a JWKS, so we could verify them offline. We do
not, for three reasons:

  1. It happens ONCE. A Supabase token is exchanged for our own session cookie at
     sign-in and never presented again, so the "avoid a network round trip per
     request" argument for local verification does not apply here.
  2. Asking Supabase who the token belongs to also tells us whether the account
     still exists and is not banned. A locally-verified JWT stays valid until it
     expires, however deleted its owner is.
  3. It needs no JWT library and no key material, so there is nothing to rotate
     and nothing to get subtly wrong about algorithm confusion.

If tokens ever need checking on every request, switch to the JWKS at
`{SUPABASE_URL}/auth/v1/.well-known/jwks.json` - the keys are already published.
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request

import certifi

# One SSL context for every Supabase call, backed by certifi's CA bundle.
# The stdlib default trusts the SYSTEM store, which python.org macOS builds
# ship empty - every HTTPS request then dies with CERTIFICATE_VERIFY_FAILED.
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())


def _urlopen(req: urllib.request.Request, timeout: float):
    return urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX)


class SupabaseError(RuntimeError):
    """Supabase refused or could not be reached. Distinct from 'the token is
    invalid', which is a None return - one is our problem, the other is theirs."""


class EmailRateLimited(SupabaseError):
    """Too many sign-in emails. Supabase's built-in sender allows only a couple
    per hour for the WHOLE project, so in practice this is hit while testing, or
    the moment two people sign in at once. Configuring custom SMTP replaces that
    limit with the sender's own, which is why it is a launch blocker rather than
    a nuisance."""


# A token check is Supabase talking to itself. Sending an email makes it open an
# SMTP connection to somebody else, and a wrong host or a blocked port HANGS
# rather than refusing - so the two need different budgets.
_TIMEOUT_S = 15.0
_EMAIL_TIMEOUT_S = 45.0


def _post(settings, path: str, body: dict, token: str | None = None,
          timeout: float = _TIMEOUT_S) -> dict:
    req = urllib.request.Request(
        f"{settings.supabase_url.rstrip('/')}{path}",
        data=json.dumps(body).encode(),
        headers={
            "apikey": settings.supabase_anon_key,
            "Authorization": f"Bearer {token or settings.supabase_anon_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with _urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001 - already handling a failure
            pass
        if e.code == 429 or "rate limit" in detail.lower():
            raise EmailRateLimited(detail) from e
        raise SupabaseError(f"HTTP {e.code} {e.reason} {detail}".strip()) from e
    except urllib.error.URLError as e:
        raise SupabaseError(f"could not reach Supabase: {e}") from e
    except TimeoutError as e:
        # NOT a URLError: a read that times out after the connection was made
        # raises this directly, so it escaped both handlers above and surfaced as
        # an unhandled 500 instead of a message anyone could act on.
        raise SupabaseError(
            "timed out waiting for Supabase - usually its SMTP host or port is "
            "wrong, so the mail server never answers"
        ) from e


def verify_access_token(token: str, settings) -> dict | None:
    """The Supabase user behind an access token, or None if it is not valid.

    Returns their raw user object: `id` (the subject we map to our user_id),
    `email`, `email_confirmed_at`, and `user_metadata` (where an OAuth provider
    puts the display name and avatar).
    """
    if not token or not settings.supabase_enabled:
        return None
    req = urllib.request.Request(
        f"{settings.supabase_url.rstrip('/')}/auth/v1/user",
        headers={"apikey": settings.supabase_anon_key,
                 "Authorization": f"Bearer {token}"},
    )
    try:
        with _urlopen(req, timeout=15) as r:
            user = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return None          # a bad or expired token is not an error
        raise SupabaseError(f"HTTP {e.code} {e.reason}") from e
    except urllib.error.URLError as e:
        raise SupabaseError(f"could not reach Supabase: {e}") from e
    except TimeoutError as e:
        raise SupabaseError("timed out waiting for Supabase") from e
    return user if user.get("id") else None


def auth_settings(settings) -> dict:
    """The project's public auth settings - which providers are switched on.

    This is what the anon key is FOR (the same endpoint every Supabase client
    hits at boot), so exposing it onward is safe. Lets the frontend show only
    sign-in buttons that will actually work, instead of finding out on click.
    """
    if not settings.supabase_enabled:
        return {}
    req = urllib.request.Request(
        f"{settings.supabase_url.rstrip('/')}/auth/v1/settings",
        headers={"apikey": settings.supabase_anon_key},
    )
    try:
        with _urlopen(req, timeout=_TIMEOUT_S) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise SupabaseError(f"could not read auth settings: {e}") from e


def send_magic_link(email: str, redirect_to: str, settings) -> None:
    """Ask Supabase to email a sign-in link. Creates the account on first use.

    Supabase deliberately answers the same way whether or not the address has an
    account, so this cannot be used to discover who is registered.
    """
    _post(settings, "/auth/v1/otp",
          {"email": email, "create_user": True,
           "options": {"email_redirect_to": redirect_to}},
          timeout=_EMAIL_TIMEOUT_S)


def oauth_url(provider: str, redirect_to: str, settings) -> str:
    """Where to send someone to sign in with Google/Discord. Supabase handles the
    provider round trip and comes back to `redirect_to`."""
    from urllib.parse import quote
    return (f"{settings.supabase_url.rstrip('/')}/auth/v1/authorize"
            f"?provider={quote(provider)}&redirect_to={quote(redirect_to, safe='')}")
