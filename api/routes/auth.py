"""Sign up / sign in.

WHAT THIS IS NOT: it does not verify that you are who you say you are. There are
no passwords and no sessions - the project brief says to use a hosted auth
provider (Clerk / Auth0 / Supabase) rather than roll our own, and half-built
credential handling is worse than none.

WHAT THIS IS: real account records. Signing up creates a row keyed by email and
returns an opaque `user_id`; the client then presents that id at the
`current_user` seam and every match, report and usage counter is scoped to it.

Connecting a provider means one change: verify its token in `api/deps.current_user`
and return the subject claim instead of trusting the header. These endpoints then
either disappear or become thin wrappers around the provider's SDK - nothing that
stores or reads data has to change.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fastapi import Request

from api.deps import current_user, db_session
from core.auth import (
    clear_session_cookie,
    make_handoff,
    read_handoff,
    set_session_cookie,
)
from core.auth.supabase import (
    EmailRateLimited,
    SupabaseError,
    auth_settings,
    oauth_url,
    send_magic_link,
    verify_access_token,
)
from core.config import Settings
from core.storage.users import (
    create_user,
    link_or_create_from_provider,
    find_by_email,
    normalise_email,
    update_user,
    user_profile,
    valid_email,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_settings = Settings()


def _rate_limited(bucket: str, limit: int, window_s: int = 3600):
    """Route dependency: at most `limit` hits per IP per window on this bucket.

    Keyed on the direct client IP. Behind the production proxy this must become
    the client's real address - set `proxy_set_header X-Forwarded-For` AND run
    uvicorn with --proxy-headers, or every visitor shares the proxy's IP and one
    abuser rate-limits everyone (the current behaviour is still safer than no
    limit, which lets one abuser burn the sign-in email quota for everyone).
    """
    def dep(request: Request) -> None:
        ip = request.client.host if request.client else "unknown"
        from core.auth.ratelimit import get_rate_limiter
        if not get_rate_limiter(_settings).allow(f"rl:{bucket}:{ip}", limit, window_s):
            raise HTTPException(
                429, "too many attempts from this address - wait a while and try again")
    return dep


def _refuse_unverified_signin() -> None:
    """The dev email endpoints prove nothing about who is asking - anyone who
    knows an email can be its account. That was the least-bad option while no
    provider was connected; the moment Supabase IS connected they are not a
    fallback, they are a bypass. 410 rather than 403: the resource is gone on
    purpose, and the message says where sign-in lives now."""
    if _settings.supabase_enabled:
        raise HTTPException(
            410,
            "Email-only sign-in has been replaced. Use the sign-in page on the "
            "main site - an emailed sign-in link or Discord.",
        )


class SignUpRequest(BaseModel):
    email: str
    display_name: str | None = None
    skill_level: str | None = None
    # Adapter-defined answers (FC: Division Rivals tier, Champs record) kept so the
    # suggestion can be recomputed later and shown on the account page. Stored
    # under "<game_id>@<edition>" so a second game's answers cannot collide.
    skill_survey: dict | None = None
    game_id: str = "ea-fc"
    edition: str = "26"
    # "player" (default) or "coach" - decides which home the app gives them.
    role: str | None = None


class SignInRequest(BaseModel):
    email: str


@router.post("/signup", dependencies=[Depends(_rate_limited("dev-auth", 30))])
def sign_up(body: SignUpRequest, response: Response,
            session: Session = Depends(db_session)) -> dict:
    _refuse_unverified_signin()
    email = normalise_email(body.email)
    if not valid_email(email):
        raise HTTPException(422, "Enter a valid email address.")
    if find_by_email(session, email) is not None:
        raise HTTPException(409, "An account with that email already exists - sign in instead.")
    row = create_user(session, email, body.display_name, body.skill_level)
    if body.skill_survey:
        update_user(session, row.user_id, skill_survey=body.skill_survey,
                    skill_survey_key=f"{body.game_id}@{body.edition}")
    if body.role:
        update_user(session, row.user_id, role=body.role)
        row = find_by_email(session, email) or row
    set_session_cookie(response, row.user_id, _settings)
    # user_id is still returned so existing clients keep working while they move
    # over to the cookie. It stops being needed once nothing reads it.
    return {"user_id": row.user_id,
            "profile": user_profile(row, survey_key=f"{body.game_id}@{body.edition}")}


@router.post("/signin", dependencies=[Depends(_rate_limited("dev-auth", 30))])
def sign_in(body: SignInRequest, response: Response,
            session: Session = Depends(db_session)) -> dict:
    _refuse_unverified_signin()
    email = normalise_email(body.email)
    if not valid_email(email):
        raise HTTPException(422, "Enter a valid email address.")
    row = find_by_email(session, email)
    if row is None:
        raise HTTPException(404, "No account found for that email.")
    set_session_cookie(response, row.user_id, _settings)
    return {"user_id": row.user_id, "profile": user_profile(row)}


# ---- Supabase ---------------------------------------------------------------
# The provider proves WHO someone is. We still issue our own session cookie, so
# every request after sign-in is answered without calling Supabase, and the rest
# of the app is unchanged - `current_user` reads the same cookie either way.


class MagicLinkRequest(BaseModel):
    email: str
    # Where Supabase should send them back to. Checked against our own hosts
    # below: an open redirect here would let someone bounce a real sign-in link
    # off our domain to a site they control.
    redirect_to: str | None = None


class ProviderSessionRequest(BaseModel):
    access_token: str


def _safe_redirect(request: Request, wanted: str | None) -> str:
    """A callback URL on the SAME origin as the request, whatever was asked for."""
    base = str(request.base_url).rstrip("/")
    if wanted:
        from urllib.parse import urlparse
        w, b = urlparse(wanted), urlparse(base)
        if (w.scheme, w.netloc) == (b.scheme, b.netloc):
            return wanted
    return f"{base}/auth/callback"


# The settings rarely change and every visitor to a sign-in page would ask, so
# one fetch answers everyone for five minutes.
_METHODS_TTL_S = 300.0
_methods_cache: tuple[float, dict] | None = None


@router.get("/methods")
def sign_in_methods() -> dict:
    """Which sign-in methods are actually live, so the frontend can render only
    buttons that will work instead of finding out on click.

    `oauth` lists only providers switched ON in Supabase AND supported by our
    /oauth/{provider} endpoint. If Supabase cannot be reached the answer is the
    optimistic one - a button that fails on click (the pages already handle
    that) beats a sign-in page with no buttons because of a blip."""
    global _methods_cache
    if not _settings.supabase_enabled:
        return {"dev_email": True, "magic_link": False, "oauth": []}

    import time
    if _methods_cache and time.monotonic() - _methods_cache[0] < _METHODS_TTL_S:
        return _methods_cache[1]
    try:
        external = auth_settings(_settings).get("external") or {}
        out = {
            "dev_email": False,
            "magic_link": bool(external.get("email")),
            "oauth": [p for p in ("google", "discord") if external.get(p)],
        }
    except SupabaseError:
        return {"dev_email": False, "magic_link": True, "oauth": ["google", "discord"]}
    _methods_cache = (time.monotonic(), out)
    return out


# Tight: every call can send a real email, and the project-wide sender quota is
# a handful per hour. 5/hour covers "typo, retry, still nothing, one more try".
@router.post("/magic-link", dependencies=[Depends(_rate_limited("magic-link", 5))])
def magic_link(body: MagicLinkRequest, request: Request) -> dict:
    """Email a sign-in link. Answers the same way whether or not the address has
    an account, so it cannot be used to find out who is registered."""
    if not _settings.supabase_enabled:
        raise HTTPException(503, "Email sign-in is not configured yet.")
    email = normalise_email(body.email)
    if not valid_email(email):
        raise HTTPException(422, "Enter a valid email address.")
    try:
        send_magic_link(email, _safe_redirect(request, body.redirect_to), _settings)
    except EmailRateLimited as exc:
        # A provider quota, not a mistake the person made - so say what to do
        # rather than showing them the raw 429 body.
        raise HTTPException(
            429,
            "Too many sign-in emails have gone out in the last hour. "
            "Wait a few minutes and try again.",
        ) from exc
    except SupabaseError as exc:
        raise HTTPException(
            502, f"Could not send the sign-in email. {exc}") from exc
    return {"sent": True}


@router.get("/oauth/{provider}")
def oauth_redirect(provider: str, request: Request) -> dict:
    """Where the Google/Discord buttons should send the browser."""
    if provider not in ("google", "discord"):
        raise HTTPException(404, "unknown provider")
    if not _settings.supabase_enabled:
        raise HTTPException(503, f"{provider.title()} sign-in is not configured yet.")
    return {"url": oauth_url(provider, _safe_redirect(request, None), _settings)}


# Looser: no email behind it, but it is the token-exchange door - a cap turns
# token guessing from free into pointless.
@router.post("/provider-session",
             dependencies=[Depends(_rate_limited("provider-session", 30))])
def provider_session(body: ProviderSessionRequest, response: Response,
                     session: Session = Depends(db_session)) -> dict:
    """Exchange a verified Supabase token for OUR session cookie.

    The token is checked with Supabase rather than trusted, and the account it
    resolves to is linked through `auth_subject` - so the provider's id is never
    our primary key and a match's owner does not depend on the vendor.
    """
    if not _settings.supabase_enabled:
        raise HTTPException(503, "Sign-in is not configured yet.")
    try:
        user = verify_access_token(body.access_token, _settings)
    except SupabaseError as exc:
        raise HTTPException(502, f"Could not reach the sign-in service: {exc}") from exc
    if not user:
        raise HTTPException(401, "That sign-in link has expired. Please request a new one.")

    meta = user.get("user_metadata") or {}
    row = link_or_create_from_provider(
        session,
        subject=str(user["id"]),
        email=user.get("email") or "",
        # Supabase only sets this once the address is proven - by clicking the
        # emailed link, or by the OAuth provider asserting it.
        email_verified=bool(user.get("email_confirmed_at")),
        display_name=(meta.get("full_name") or meta.get("name")
                      or meta.get("user_name") or None),
    )
    set_session_cookie(response, row.user_id, _settings)
    return {"user_id": row.user_id, "profile": user_profile(row)}


@router.post("/signout")
def sign_out(response: Response) -> dict:
    """Drop the session cookie. Server-side because the cookie is HttpOnly and
    page scripts cannot delete it themselves - that is the point of HttpOnly."""
    clear_session_cookie(response, _settings)
    return {"ok": True}


class AdoptRequest(BaseModel):
    token: str


@router.post("/handoff")
def mint_handoff(user: str = Depends(current_user)) -> dict:
    """A 60-second token the hub can hand to a game subdomain.

    Needed only while one cookie cannot cover both hosts - locally, where the
    hosts are localhost and fifa.localhost. In production the cookie is scoped
    to .coachfio.com and nothing calls this.
    """
    if user == "anonymous":
        raise HTTPException(401, "not signed in")
    return {"token": make_handoff(user, _settings)}


@router.post("/handoff/adopt")
def adopt_handoff(body: AdoptRequest, response: Response,
                  session: Session = Depends(db_session)) -> dict:
    """Exchange a hand-off token for a session cookie on THIS origin.

    The token is verified here rather than trusted: it is signed, single-purpose
    (its own salt, so a session cookie cannot be presented as one, or vice
    versa) and expires in a minute.
    """
    user_id = read_handoff(body.token, _settings)
    if not user_id:
        raise HTTPException(401, "that sign-in link has expired - open the game again")
    from core.storage.users import get_or_create_user
    row = get_or_create_user(session, user_id)
    set_session_cookie(response, row.user_id, _settings)
    return {"ok": True, "profile": user_profile(row)}


@router.get("/session")
def whoami(user: str = Depends(current_user),
           session: Session = Depends(db_session)) -> dict:
    """Who the current cookie resolves to. The frontend calls this on load
    instead of reading an identity out of localStorage."""
    if user == "anonymous":
        return {"signed_in": False}
    from core.storage.users import get_or_create_user
    return {"signed_in": True, "profile": user_profile(get_or_create_user(session, user))}
