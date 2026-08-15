# Coach.io - Frontend

Static multi-page site, served by the API itself at **http://localhost:8000** (same
origin, so no CORS). Edits are live on refresh - but browsers cache `coach.js` and
`app.css`, so hard-refresh after changing them.

No build step, no framework, no CDN. Two files carry the whole thing:

| File       | Role                                                          |
|------------|---------------------------------------------------------------|
| `app.css`  | The design system - tokens + semantic components               |
| `coach.js` | All behaviour; detects the page from the URL and wires the API |

## Pages

| Route         | Screen              | Rendered by                          |
|---------------|---------------------|--------------------------------------|
| `/`           | Home / dashboard    | static + `loadRecent()`              |
| `/upload/`    | Upload a match      | static + `initUpload()`              |
| `/analyzing/` | Live progress (SSE) | static + `initAnalyzing()`           |
| `/report/`    | Match report        | `initReport()` renders into `<main>` |
| `/moment/`    | Moment viewer       | `initMoment()` renders into `<main>` |
| `/trends/`    | Trends & history    | `initTrends()`                       |

## The markup contract (read before restyling)

`coach.js` finds elements through **`data-cx-*` attributes** plus a few stable
hooks (`[data-side]`, `.time-chip`, `#cx-controls`, `#cx-skill`). It never matches
on styling classes.

This matters: the previous version keyed off `.lg\:col-span-4`, `.border-dashed`
and a button whose text read "Browse files", so any restyle silently broke the
upload flow. **Keep the `data-cx-*` attributes when you edit HTML** and the pages
stay decoupled from the CSS.

Current hooks: `data-cx-recent`, `data-cx-dropzone`, `data-cx-dropzone-title`,
`data-cx-dropzone-hint`, `data-cx-browse`, `data-cx-upload-progress`,
`data-cx-error`, `data-cx-pct`, `data-cx-bar`, `data-cx-step`, `data-cx-heading`,
`data-step="upload|watch|score|write"`, `data-cx-note`, `data-cx-count`,
`data-cx-prev`, `data-cx-next`, `data-cx-metrics`, `data-cx-history`, `data-nav`.

## Design system

Tokens live at the top of `app.css` as CSS custom properties - change a colour
once there and it moves everywhere. The look is minimal, dark and flat, and
committed to dark; there is no light theme.

- **Ground**: softened dark `--bg` (`#16181c`, not pure black - pure black makes
  hairline borders shout), three surface steps, hairline borders, no shadows.
- **One accent** (`--accent`) on the primary button and the active nav item, and
  nowhere else.
- **Semantic colour only where it means something**: the category icon on a
  coaching card (`.tile--accent/danger/warn/info`) and win/loss/draw state.
  Never decoration.
- **Type**: Inter for everything, JetBrains Mono for anything numeric - scores,
  timestamps, costs, deltas - so digits align in tables. No display face.
- **Radii**: 5 / 7 / 10px. **Motion**: one pulse on the active pipeline step,
  disabled under `prefers-reduced-motion`.

**Deliberately absent** - all of these were tried and removed as too fancy, so
please don't reintroduce them: glows and coloured box-shadows, gradients (text,
hairlines, progress bar), background textures, condensed uppercase display type,
hover translations, and coloured accent stripes on every card.

Components: `.btn`, `.card`, `.badge`, `.result`, `.chip`, `.table`, `.stat`,
`.field`/`.input`/`.select`, `.segmented`, `.dropzone`, `.steps`/`.step`,
`.progress`, `.alert`, `.empty`, `.skeleton`, `.disclosure`, `.match-row`,
`.moment-item`, `.goal`, `.log`.

## Run standalone (optional)

The API already serves this at :8000. To serve it alone on :3000 - `coach.js`
detects port 3000 and points at `http://localhost:8000` for the API:

```bash
npm install && npm run dev
```
