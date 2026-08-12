# Coach.io — Frontend

Static multi-page frontend generated from the Google Stitch export (`coach.zip`), wired up
with real navigation between screens and served locally.

## Pages

| Route         | Screen                          |
|---------------|----------------------------------|
| `/`           | Home — AI Coach chat            |
| `/upload/`    | Upload Hub                      |
| `/analyzing/` | Analyzing Match (processing)    |
| `/report/`    | Match Report                    |
| `/trends/`    | Trends & History                |
| `/moment/`    | Moment Viewer (video breakdown) |

Design tokens (colors, type scale, spacing) come from the Stitch `DESIGN.md` and are baked
into each page's Tailwind CDN config, so every screen matches the exported design exactly.

## Run locally

```bash
npm install
npm run dev
```

Then open **http://localhost:3000**.

## Flow wired between screens

- Home → "Analyze Last Match" / bottom nav → Upload Hub
- Upload Hub → "Browse Files" / recent match cards → Analyzing → auto-advances to Match Report after ~4s
- Match Report → clicking a time chip → Moment Viewer
- Moment Viewer → close (X) → back to Match Report
- Top/bottom nav (Upload / Reports / Trends) and the "Coach.io" logo are live links on every page
