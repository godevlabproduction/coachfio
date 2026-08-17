# Coachfio — Stitch design prompt (full set)

Paste this into Google Stitch. Covers the hub (coachfio.com) and the FC 26
game site (fifa.coachfio.com) in one pass, sharing one visual system.

---

Design the full screen set for "Coachfio," an AI gameplay-coaching product.
It has two parts: a neutral **hub** at coachfio.com where users sign in and
pick a game, and a **game-specific site** per game — this pass covers
**FC 26**, served at fifa.coachfio.com, entered after picking. Both parts
share one visual system and differ only in accent color and content — they
must read as one product family, not two apps.

**I'm attaching reference images.** Use them ONLY for general layout/
composition inspiration (hero card + side list + card row below, etc.) — do
NOT copy their color scheme, icon style, stock photography, sidebar nav, or
any invented feature they depict. Every visual detail (colors, typography,
imagery, content) is fully specified below and overrides anything in a
reference image that conflicts with it.

## Universal guardrails — avoid the generic AI-generated SaaS look

No purple-to-blue gradient blobs, no glassmorphism/frosted-glass cards, no
oversized rounded pill buttons, no identical 3-column icon+title+sentence
feature grids, no blob-people illustrations, no stock photography of people,
no emoji used as icons, no decorative background shapes or mesh gradients,
no gradient text on headlines, no vanity metrics (view counts, follower-
style social proof), no invented social/chat/friends-list features.

Instead: asymmetric layouts with intentionally varying content density, real
specific copy grounded in what this product actually does, precise data as
a design element (exact timestamps/percentages in JetBrains Mono), and
restraint — most of the screen stays quiet, with the accent color doing
real signaling work. Quality bar: Linear.app, Stripe's docs, Claude.ai — not
a template.

## Shared design system, both sites

- Light theme. Background `#F7F7FB`, surfaces `#FFFFFF` / `#F1F1F6`,
  borders as black at 8–16% opacity, not solid grays.
- Text: primary ink `#14121F`, secondary `#4A4660`, tertiary `#86829C`.
- Typography: "Inter" for UI text, "JetBrains Mono" for timestamps/
  technical/mono labels.
- Radius `10px` default, `16px` for larger containers. Spacing scale:
  8/16/24/40/72px.
- Depth via tonal layering and thin subtle borders only; soft ambient
  shadow reserved for overlays/modals. No gradients, no glow, no hover-lift
  (press feedback is a slight scale-down instead).
- One accent color per site, used ONLY on primary buttons, active nav/
  links, focus states, and semantic status (win/loss/draw) — never
  decorative:
  - **Hub accent:** violet `#7C5CFF` (deep `#5B3FD6` for text/hover). A
    dark-inverse band (`#14121F` bg, lighter violet `#9B84FF` accent) is
    allowed for exactly one contrasting section on the landing page only.
    A soft, very low-opacity violet blur/glow shape is also allowed as a
    background treatment strictly behind hero art on the game-picker
    screen — nowhere else.
  - **FC 26 accent:** green `#48C674`.
- Shared header: Coachfio logo (mark + wordmark) on the left. Hub header
  shows sign-in/get-started when signed out, account icon when signed in.
  FC 26 header adds a small game icon/crest next to the logo, plus primary
  nav (Upload, Report, Moments, Statistics), a primary "New analysis"
  button, and Account — plus a mobile bottom tab bar mirroring the same
  destinations. No persistent sidebar on either site.
- Shared footer on every signed-in page (both sites): slim footer reading
  "Built by **GoDevLab Agency**" with a "Support" link to
  **godevlabagency@gmail.com**. The hub landing page gets a fuller
  marketing footer instead of the slim version.

## Hub screens (coachfio.com)

1. **Landing page** — eyebrow tag ("Two games live," mono font), headline
   on AI-powered match analysis turning gameplay into specific timestamped
   improvements, primary "Get started" + secondary "View demo report"
   ghost button, 3 compact inline value points, a demo section with real
   report screenshots per game in browser-chrome mockups, one
   violet-tinted "how it works" band.
2. **Sign in / create account** — single centered card, email field only
   (no password), swap link between sign-in/create-account, subtitle
   "You pick your game after signing in."
3. **Game picker (post-login hub home)** — top app bar only (logo, account
   menu, notifications, a search bar for "Search your games..."), no
   sidebar. Hero card featuring the most recently played or a featured
   game, using real key art (`frontend/art/fc27.jpg` / `frontend/art/
   cs2.png`), real description, primary "Start analysis" CTA, a soft
   low-opacity violet blur behind the hero art only. "Your games" list
   (FC 26, CS2) as real library entries linking into each game's site. A
   "more games coming soon" row as calm dashed-outline placeholder cards —
   no unrelated stock game titles. Do not include: sidebar nav, friends/
   online-players list, player/view counts, stock character artwork.

## FC 26 screens (fifa.coachfio.com)

4. **Home/dashboard** — lead card for the most recent match analysis (real
   result data, short finding summary, "View report" CTA), a "Recent
   matches" list with win/loss/draw badges using the semantic color
   system, and a "Drills & tactical knowledge" section grounded in the
   real knowledge base (formations, defending, set pieces, player roles)
   as clean icon/text cards — no stock photography, no people.
5. **Upload a match** — two-step page: Step 1 drag-and-drop video dropzone
   (MP4/MKV, 720p+) with browse fallback and progress bar; Step 2 which
   side played (Home/Away segmented control), skill level, control scheme.
6. **Analyzing** — calm staged progress (uploading → reading match →
   generating report), not a bare spinner.
7. **Match report** — "one priority to fix" callout up top, goal-by-goal
   breakdown with clickable timestamps, coaching points grouped by
   category with small category icons.
8. **Moments** — gallery of auto-clipped highlights, each with clip,
   caption, timestamp (JetBrains Mono).
9. **Statistics** — 3 summary metric cards (matches analyzed, win rate,
   most common mistake) + match history list.
10. **Account** — control scheme and coaching-level defaults; "which side"
    is NOT here, asked fresh every upload.
11. **My locker** — player dashboard: recent trend, active practice plan/
    drills, quick links into reports.
12. **Office** *(flag: confirm scope — implies a coach/multi-client role
    not yet scoped elsewhere)* — coach-facing client list with progress
    summaries.
13. **Coach** *(flag: purpose unconfirmed, currently unbuilt)* — best
    guess: a coach chat/Q&A interface.

## Shared utility screens (used by both sites, design once)

14. **404 / not found** — same visual system, calm, not illustration-heavy.
15. **Toast/notification component sheet** — success, error, in-progress
    states.
