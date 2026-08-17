# TODO / Roadmap notes

Snapshot as of 2026-08-16. Local Docker stack is up and running at
http://localhost:8000 (api, worker, postgres, redis, minio all healthy).

## Current priority: polish, not new features

**No new features for now.** Focus is upgrading the professionalism/feel of
the pages that already exist in `frontend/`, using the "Aetheric Utility"
design direction (see below) as the visual reference — not adding scope.

Existing pages to work through:
- `frontend/index.html` — entry point
- `frontend/signin/` — sign in
- `frontend/account/` — account
- `frontend/upload/` — upload flow
- `frontend/analyzing/` — analyzing state
- `frontend/report/` — coaching report
- `frontend/moment/` — highlight moment view
- `frontend/statistics/` — stats/trends
- `frontend/locker/` — locker
- `frontend/office/` — office
- `frontend/coach/` — coach
- shared: `frontend/app.css`, `frontend/coach.js`

New feature ideas below are parked, not scheduled.

## Architecture decision: hub + per-game subdomains

Decided direction: one main hub product (coachfio.com) + one subdomain per
game (fifa.coachfio.com, cs2.coachfio.com, ...), because settings/config
differ a lot per game (this maps onto the existing `/adapters` split in the
codebase — each subdomain is effectively "the adapter, as a site").

**What lives in the hub (coachfio.com):**
- Auth — signup/login kept to the bare minimum (email/password or single
  OAuth button). No game-related choices during signup.
- Billing & usage (matches-analysed limits, plan).
- Game picker / dashboard — cards for each game (FC 26, CS2, "more coming
  soon"), this is what a user lands on right after signup/login.

**What does NOT live in the hub:**
- Anything game-specific: upload flow, reports, trends, drills, per-game
  settings. That all stays inside each game's own subdomain. Game-specific
  settings (e.g. FC 26's Home/Away pick) live in an in-game account-settings
  picker, not in onboarding.

**Auth / cross-subdomain session sharing:**
- Session/JWT issued on hub login, stored as a cookie scoped to the parent
  domain (`Domain=.coachfio.com`, `Secure`, `HttpOnly`, `SameSite=Lax`) so a
  user doesn't have to log in again when moving from the hub into a game
  subdomain.
- Each subdomain's API validates that token against a shared auth service —
  this is the natural home for the `current_user` seam already referenced in
  `api/deps.current_user`.

**Next concrete step on this:** design the shared auth/session service first
— both the hub and every future game subdomain depend on it, so it needs to
exist before wiring up even one subdomain. Everything else (game-picker UI,
per-game settings, billing) can be built in parallel once that's decided.

## Hub visual design

Direction: simple, professional, "quiet SaaS tool" look — like Claude,
ChatGPT, Linear. Not a gaming brand, no generic template feel, no stock
gaming imagery/gradients/heavy icons.

Prompt drafted for Google Stitch (3 screens: landing/marketing, sign up/
login, hub home/game picker) — see chat history or re-request if needed for
iteration.

**First Stitch export received and checked into the repo** at
`design/stitch-hub/` (not wired into the app yet — reference/design only):
- `coach.io_landing_page/` — marketing landing page (`code.html` + `screen.png`)
- `coach.io_sign_in/` — bare-minimum sign in screen
- `coach.io_game_hub/` — post-login game picker/dashboard
- `aetheric_utility/DESIGN.md` — the design system behind it: "Aetheric
  Utility" — Linear/Claude-influenced, warm off-white / near-black dark mode,
  single indigo accent (`#5E6AD2`) used sparingly, Inter for UI text,
  JetBrains Mono for metadata/timestamps, tonal layering + subtle 1px
  borders instead of shadows, 4px soft-square radius, 8px spacing scale.

- [ ] Review the 3 exported screens against the hub scope decided above
      (bare-minimum signup, no game choices at signup, game picker as
      landing after login) and confirm they match before building anything
      from them.
- [ ] Decide how much of the exported `code.html` gets reused vs.
      rebuilt against the actual frontend stack.
- [ ] Entering a game from the hub (e.g. hub → FC 26) needs to be a good,
      well-considered entry point/transition — not an afterthought.

**Decision: one shared light design system, accent color per game.**
Previously the hub (`hub.css`, "Aetheric Utility") and the FC 26 game app
(`app.css`, dark near-black + green) were two visually distinct systems.
Decided: unify on ONE light theme (structure, typography, spacing, component
shapes shared across hub + every game), with each game differentiated only
by its own accent color (and optionally a game icon/crest, hero imagery on
its Home page, and game-specific terminology already in the adapter's vocab
config). Nav structure, cards, buttons, spacing must stay identical across
games so the whole product reads as one family.
- [ ] `app.css` (FC 26 game pages) needs to move from dark/green to the
      shared light system + its own accent color — this is a real rework,
      not just a copy/paste from `hub.css`.
- [ ] Decide FC 26's specific accent color (distinct from the hub's
      indigo `#5E6AD2`).

## Polish checklist — professionalism pass on existing pages

Applies across all pages listed above (`index`, `signin`, `account`,
`upload`, `analyzing`, `report`, `moment`, `statistics`, `locker`, `office`,
`coach`) unless a specific page is called out. No new features — just
raising the craft level of what's there, consistent with `app.css`'s own
stated rules (no shadows/gradients/hover-translate, restrained accent use).

**Consistency & system discipline**
1. [ ] Audit all pages against `app.css`'s own stated rules — check for
       drift (shadows/gradients/hover-translate creeping back in anywhere).
2. [ ] Standardize spacing rhythm across pages — confirm `upload`, `report`,
       `moment`, `statistics` use the same section/gap scale as `index.html`.
3. [ ] Unify empty states — `report`, `statistics`, `moment` with zero data.
4. [ ] Unify loading states — match `analyzing/`'s pattern on `statistics`/
       `report` while they're fetching.
5. [ ] Unify error/failure states (upload fails, analysis fails, 402 over
       usage limit) into one consistent visual pattern.

**Typography & hierarchy**
6. [ ] Check heading scale consistency (`t-display`, `h1`, `h2`, `h3`) across
       pages for accidental skipped levels.
7. [ ] Line-length control — confirm long report/coaching text respects a
       `ch`-based max-width like the homepage lede does.
8. [ ] JetBrains Mono usage — confirm it's applied consistently to *all*
       timestamps/technical data, not just some pages.

**Navigation & structure**
9. [ ] Active nav state — confirm `data-nav` highlighting is correct on
       every page, not just the ones already tested.
10. [ ] Breadcrumb or back-context on deep pages (`moment/`, `report/`) so
        it's clear how you got there.
11. [ ] Add a minimal footer (see #21 below for required content).

**Forms & interaction**
12. [ ] Sign-in form polish — validation states, error messaging tone
        matching the calm/restrained voice used elsewhere.
13. [ ] Upload flow — drag-and-drop states (idle/hover/active/error) fully
        styled, not default browser file input look.
14. [ ] Button hierarchy audit — one primary CTA per view, consistent
        primary/secondary/tertiary usage.

**Content & data density**
15. [ ] `statistics/` — table/list readability at scale (10 matches vs 100).
16. [ ] `report/` — reinforce "one priority per match" framing visually,
        matching the homepage copy's promise.
17. [ ] `moment/` — clip/frame presentation polish (borders, captions,
        timestamp formatting).

**Craft details**
18. [ ] Add a favicon (currently likely default/missing).
19. [ ] Focus states for keyboard navigation across all interactive
        elements.
20. [ ] Meta tags / page titles — confirm every route has a distinct,
        correct `<title>`, not a copy-paste leftover from another page.
21. [ ] **Footer branding** — every page's footer (and anywhere the app
        says "powered by") should credit **GoDevLab Agency**, with contact
        **godevlabagency@gmail.com**.

## Gotchas to remember while working on this

- Celery worker does **not** auto-reload — after editing `core/`,
  `adapters/`, or `workers/`, run `docker compose restart worker api`.
- Changing `.env` needs `--force-recreate`, not `restart`.
- Never commit `.env`, API keys, or match videos (already gitignored).
- The one rule that overrides everything: **no game ids in `/core`**
  (`if game == "fc26"` is a design failure — push it into the adapter).
