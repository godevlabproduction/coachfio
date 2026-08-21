"""Derive a game's UI theme from its own key art.

Why this exists: every game site needs a palette that belongs to that game, and
picking hexes by hand does not scale past two titles. The art is the one asset
every game already ships, so the palette is READ from it.

Game-agnostic by construction: the extractor knows nothing about any title. It
takes an image, produces a theme, and writes it into that game's ADAPTER
config (`adapters/<pkg>/config/theme.yaml`) - the plugin owns its presentation,
the core never learns a game id. `--all` then regenerates the two frontend
artifacts (`palettes.css`, `palettes.json`) from every adapter that has one, so
adding game #10 is: drop the art in, run this, done.

    python -m tools.extract_palette --game ea-fc --edition 26 \
        --art frontend/art/fc27-card.jpg --pkg adapters/ea_fc_26
    python -m tools.extract_palette --all      # regenerate frontend artifacts
"""
from __future__ import annotations

import argparse
import colorsys
import json
from dataclasses import dataclass
from pathlib import Path

import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ADAPTERS = ROOT / "adapters"
FRONTEND = ROOT / "frontend"

# Sampling: small enough to be fast, big enough that a crest or a kit colour
# survives. 24 bins because a poster usually has 3-6 real hues plus gradients.
SAMPLE_W = 320
# 64, not 24: a photographic poster (an aerial shot, a map) averages into greys
# at low bin counts and the one saturated thing in frame - a kit, a pitch, a
# crest - disappears. More bins keeps small vivid regions as their own colour.
BINS = 64


@dataclass(frozen=True)
class Swatch:
    r: int
    g: int
    b: int
    weight: float          # share of pixels, 0-1

    @property
    def hex(self) -> str:
        return f"#{self.r:02X}{self.g:02X}{self.b:02X}"

    @property
    def hls(self) -> tuple[float, float, float]:
        return colorsys.rgb_to_hls(self.r / 255, self.g / 255, self.b / 255)


def _hex(r: float, g: float, b: float) -> str:
    to = lambda v: max(0, min(255, round(v * 255)))
    return f"#{to(r):02X}{to(g):02X}{to(b):02X}"


def _shift(sw: Swatch, *, light: float | None = None, sat: float | None = None,
           dl: float = 0.0, ds: float = 0.0) -> str:
    """Recolour a swatch in HLS: absolute targets win, deltas apply after."""
    h, l, s = sw.hls
    if light is not None:
        l = light
    if sat is not None:
        s = sat
    l = max(0.0, min(1.0, l + dl))
    s = max(0.0, min(1.0, s + ds))
    return _hex(*colorsys.hls_to_rgb(h, l, s))


def swatches(path: Path) -> list[Swatch]:
    """Dominant colours, most-used first.

    Median-cut on a downsampled copy. Fully transparent pixels are dropped -
    a PNG with a cut-out subject would otherwise report its background as the
    game's main colour.
    """
    img = Image.open(path)
    img = img.convert("RGBA")
    w, h = img.size
    img = img.resize((SAMPLE_W, max(1, round(h * SAMPLE_W / w))))
    rgb = Image.new("RGB", img.size, (0, 0, 0))
    rgb.paste(img, mask=img.split()[3])
    alpha = img.split()[3]
    opaque = sum(c for v, c in zip(range(256), alpha.histogram()) if v > 8) or 1

    q = rgb.quantize(colors=BINS, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette() or []
    out: list[Swatch] = []
    for count, idx in sorted(q.getcolors() or [], reverse=True):
        r, g, b = pal[idx * 3: idx * 3 + 3]
        out.append(Swatch(r, g, b, count / opaque))
    return out


def _chroma(sw: Swatch) -> float:
    """How much COLOUR a swatch carries. Near-blacks and greys score ~0, so a
    poster that is mostly night sky still yields its kit colour as the accent."""
    _, l, s = sw.hls
    return s * (1 - abs(l - 0.5) * 1.3)


def build_theme(art: Path) -> dict:
    sw = swatches(art)
    if not sw:
        raise SystemExit(f"no colours found in {art}")

    # Chroma dominates the ranking: a game's identity is its saturated colour,
    # even when most of the frame is sky, grass-grey or night. Frequency only
    # breaks ties between similarly vivid candidates.
    coloured = sorted(sw, key=lambda x: (_chroma(x) ** 2) * (0.08 + x.weight), reverse=True)
    accent = next((c for c in coloured if _chroma(c) > 0.12), coloured[0])
    # Second hue that is genuinely different from the accent (>40 deg apart),
    # so a two-tone palette does not collapse into one colour.
    def far(a: Swatch, b: Swatch) -> bool:
        d = abs(a.hls[0] - b.hls[0])
        return min(d, 1 - d) > 0.11
    second = next((c for c in coloured if far(c, accent)), accent)
    darkest = min(sw, key=lambda x: x.hls[1])
    brightest = max(sw, key=lambda x: x.hls[1])

    def palette(name: str, slug: str, s1, s2, s3, s4, acc) -> dict:
        return {"name": name, "slug": slug,
                "sky": [s1, s2, s3, s4], "accent": acc}

    # Every variant is derived from THIS art - no invented hues. They differ in
    # how the same colours are pitched: as shot, pushed, dimmed, or reduced.
    palettes = [
        palette("Key art", "key-art",
                _shift(accent, light=0.26, ds=-0.05),
                _shift(accent, light=0.52),
                _shift(second, light=0.60),
                _shift(darkest, light=0.10),
                _shift(accent, light=0.55, sat=max(0.45, accent.hls[2]))),
        palette("Vivid", "vivid",
                _shift(accent, light=0.34, ds=0.25),
                _shift(accent, light=0.58, ds=0.35),
                _shift(brightest, light=0.66, ds=0.25),
                _shift(second, light=0.16, ds=0.15),
                _shift(accent, light=0.60, ds=0.35)),
        palette("Deep", "deep",
                _shift(accent, light=0.16, ds=-0.05),
                _shift(accent, light=0.34),
                _shift(second, light=0.28),
                _shift(darkest, light=0.07),
                _shift(accent, light=0.48, ds=0.1)),
        palette("Duotone", "duotone",
                _shift(accent, light=0.30),
                _shift(second, light=0.46, ds=0.1),
                _shift(accent, light=0.58, ds=0.1),
                _shift(second, light=0.14),
                _shift(second, light=0.56, ds=0.2)),
        palette("Muted", "muted",
                _shift(accent, light=0.28, sat=0.16),
                _shift(second, light=0.44, sat=0.14),
                _shift(brightest, light=0.58, sat=0.12),
                _shift(darkest, light=0.10, sat=0.12),
                _shift(accent, light=0.56, sat=0.42)),
    ]
    return {
        "source_art": str(art.relative_to(ROOT)).replace("\\", "/"),
        "read_colours": [{"hex": c.hex, "weight": round(c.weight, 4)} for c in sw[:8]],
        "palettes": palettes,
    }


def write_theme(pkg: Path, game_id: str, edition: str, art: Path) -> Path:
    theme = build_theme(art)
    theme = {"game_id": game_id, "edition": edition, **theme}
    out = pkg / "config" / "theme.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "# GENERATED by tools/extract_palette.py - the game's UI theme, read\n"
        "# from its own key art. Re-run the tool after changing the art; edit\n"
        "# by hand only to correct a colour the extractor got wrong.\n"
        + yaml.safe_dump(theme, sort_keys=False),
        encoding="utf-8")
    return out


def collect() -> list[dict]:
    themes = []
    for f in sorted(ADAPTERS.glob("*/config/theme.yaml")):
        themes.append(yaml.safe_load(f.read_text(encoding="utf-8")))
    return themes


def regenerate(themes: list[dict]) -> tuple[Path, Path]:
    """Write the two frontend artifacts: one stylesheet of palette variables,
    one manifest the picker builds itself from."""
    css = ["/* GENERATED by tools/extract_palette.py - do not edit.",
           "   One block per game palette, keyed `<game_id>:<slug>`. The page sets",
           "   data-sky to one of these and every surface follows: the sky fields",
           "   (--s1..--s4) and the accent the glass is tinted with. */", ""]
    manifest: dict[str, list[dict]] = {}
    for t in themes:
        gid = t["game_id"]
        manifest[gid] = []
        for p in t["palettes"]:
            key = f'{gid}:{p["slug"]}'
            s1, s2, s3, s4 = p["sky"]
            acc = p["accent"]
            css.append(
                f'body[data-sky="{key}"] {{\n'
                f"  --s1: {s1}; --s2: {s2}; --s3: {s3}; --s4: {s4};\n"
                f"  --h-violet: {acc};\n"
                f"  --h-violet-2: color-mix(in oklab, {acc} 72%, white);\n"
                f"  --h-violet-deep: color-mix(in oklab, {acc} 78%, black);\n"
                f"  --h-violet-soft: color-mix(in oklab, {acc} 14%, transparent);\n"
                f"  --h-violet-line: color-mix(in oklab, {acc} 45%, transparent);\n"
                f"}}")
            manifest[gid].append({"key": key, "name": p["name"],
                                  "swatch": [s1, s2, s3], "accent": acc})
        # The game's default, applied before any JS runs, so entering a game
        # never flashes another game's (or the hub's) colours first.
        first = t["palettes"][0]
        css.append(
            f'body[data-game-id="{gid}"]:not([data-sky]) {{\n'
            f'  --s1: {first["sky"][0]}; --s2: {first["sky"][1]};'
            f' --s3: {first["sky"][2]}; --s4: {first["sky"][3]};\n'
            f'  --h-violet: {first["accent"]};\n'
            f'  --h-violet-2: color-mix(in oklab, {first["accent"]} 72%, white);\n'
            f'  --h-violet-deep: color-mix(in oklab, {first["accent"]} 78%, black);\n'
            f'  --h-violet-soft: color-mix(in oklab, {first["accent"]} 14%, transparent);\n'
            f'  --h-violet-line: color-mix(in oklab, {first["accent"]} 45%, transparent);\n'
            f'}}')
    css_path = FRONTEND / "palettes.css"
    json_path = FRONTEND / "palettes.json"
    css_path.write_text("\n".join(css) + "\n", encoding="utf-8")
    json_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return css_path, json_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--game"); ap.add_argument("--edition", default="")
    ap.add_argument("--art"); ap.add_argument("--pkg")
    ap.add_argument("--all", action="store_true",
                    help="regenerate frontend artifacts from every adapter theme")
    a = ap.parse_args()

    if a.game:
        if not (a.art and a.pkg):
            raise SystemExit("--game needs --art and --pkg")
        out = write_theme(ROOT / a.pkg, a.game, a.edition, ROOT / a.art)
        print(f"wrote {out.relative_to(ROOT)}")
    css, js = regenerate(collect())
    print(f"wrote {css.relative_to(ROOT)} and {js.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
