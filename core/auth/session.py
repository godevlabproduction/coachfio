"""Session transport: a signed cookie that works across coachfio.com subdomains.

WHAT THIS IS. Session management - deciding that a request belongs to an account
that has already been authenticated, and carrying that across the hub and every
game subdomain. Every application does this.

WHAT THIS IS NOT, and must never become. Authentication. Proving that somebody
owns an email address is the hosted provider's job (CLAUDE.md: do NOT roll your
own auth), and nothing here checks a password, sends a magic link or verifies a
token. Until a provider is connected, `/api/auth/signin` still trusts whatever
email it is given - this cookie only changes how the resulting identity is
CARRIED, not how it is EARNED. It is not a security improvement on its own.

WHY A COOKIE, rather than the localStorage identity it replaces:

  1. localStorage is per-ORIGIN. coachfio.com and fifa.coachfio.com cannot see
     each other's, which is why entering a game needed the session smuggled
     through a URL fragment. One cookie on `.coachfio.com` covers every
     subdomain, and that hand-off can go.
  2. Three callers cannot set request headers - the SSE progress stream, the
     <video> element and the PDF download link - so the identity had to ride in
     `?u=` in the query string, where it lands in server logs and Referer
     headers. Cookies are sent automatically by all three.
  3. HttpOnly means page scripts cannot read it, so an XSS bug cannot exfiltrate
     the session the way it could read localStorage.

The value is `<user_id>` signed with SECRET_KEY and timestamped, so it cannot be
forged or replayed past `session_max_age_days`. The signature is not encryption:
the user id is readable by anyone holding the cookie, which is fine - it is an
opaque id, and the holder is the account owner.
"""
from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

SESSION_COOKIE = "coachfio_session"

# Namespaced so a signature minted for one purpose can never be replayed as
# another (e.g. a future one-time email link presented as a session).
_SALT = "coachfio.session.v1"


def _signer(settings) -> TimestampSigner:
    return TimestampSigner(settings.secret_key, salt=_SALT)


def make_token(user_id: str, settings) -> str:
    return _signer(settings).sign(user_id.encode()).decode()


def read_token(token: str, settings) -> str | None:
    """The user id inside a token, or None if it is missing, tampered with or
    past its age. Never raises: a bad cookie is an anonymous request, not a 500."""
    if not token:
        return None
    try:
        raw = _signer(settings).unsign(
            token, max_age=settings.session_max_age_days * 86400)
    except (BadSignature, SignatureExpired):
        return None
    return raw.decode() or None


def read_session(request, settings) -> str | None:
    return read_token(request.cookies.get(SESSION_COOKIE, ""), settings)


def set_session_cookie(response, user_id: str, settings) -> None:
    """Attach the session to a response.

    `domain` is empty in development on purpose. Browsers do not agree about
    `Domain=.localhost`, so locally the cookie stays host-only and the hub keeps
    its fragment hand-off; in production SESSION_COOKIE_DOMAIN=.coachfio.com
    makes one cookie cover every subdomain and the hand-off becomes dead code.
    """
    response.set_cookie(
        SESSION_COOKIE,
        make_token(user_id, settings),
        max_age=settings.session_max_age_days * 86400,
        # Not readable by page scripts - the main reason to move off localStorage.
        httponly=True,
        # Lax, not Strict: Strict would drop the cookie on the hub -> game
        # navigation, which is precisely the journey this exists to support.
        samesite="lax",
        # HTTPS-only in production; off locally, where there is no TLS.
        secure=settings.session_cookie_secure,
        domain=settings.session_cookie_domain or None,
        path="/",
    )


def clear_session_cookie(response, settings) -> None:
    """Sign out. The attributes must match those used to set it, or the browser
    keeps the original cookie and the user stays signed in."""
    response.delete_cookie(
        SESSION_COOKIE,
        domain=settings.session_cookie_domain or None,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
    )


# ---- cross-origin hand-off (hub -> game site) -------------------------------
# Only needed where one cookie CANNOT cover both hosts. In production it can
# (SESSION_COOKIE_DOMAIN=.coachfio.com) and this is dead code; locally the hosts
# are localhost and fifa.localhost, browsers disagree about `Domain=.localhost`,
# and without this you would be asked to sign in again one click after signing in.
#
# What crosses is a SEPARATE, short-lived, single-purpose token - never the
# session cookie and never a raw user id. Its own salt means it cannot be
# presented as a session, and 60 seconds means a token left in someone's history
# or a shared link is useless by the time it is found.
_HANDOFF_SALT = "coachfio.handoff.v1"
HANDOFF_MAX_AGE_S = 60


def make_handoff(user_id: str, settings) -> str:
    return TimestampSigner(settings.secret_key, salt=_HANDOFF_SALT).sign(
        user_id.encode()).decode()


def read_handoff(token: str, settings) -> str | None:
    if not token:
        return None
    try:
        raw = TimestampSigner(settings.secret_key, salt=_HANDOFF_SALT).unsign(
            token, max_age=HANDOFF_MAX_AGE_S)
    except (BadSignature, SignatureExpired):
        return None
    return raw.decode() or None
