"""Measure what a NATIVE Gemini video-analysis call actually costs.

Sends a short clip inline to Gemini's generateContent endpoint, asks for a
coaching report, and prints the REAL token usage + cost — then extrapolates to a
full 15-min match. This is an experiment separate from the frame pipeline.

    docker compose run --rm -v "/c/path/to/clips:/clips:ro" api \
        python -m tools.gemini_video_cost /clips/short.mp4 [model] [low|default]

Notes:
- Inline video request is capped ~20MB, so use a SHORT clip (10-30s, 720p or
  lower) just to measure tokens/second. Longer video needs the File API.
- Uses OPENAI_API_KEY from .env as the Gemini key (Bearer).
"""
from __future__ import annotations

import base64
import json
import sys
import urllib.request
from pathlib import Path

from core.config import get_settings

# Gemini list price per 1M tokens (approx; adjust to your plan). in/out.
PRICING = {
    "gemini-flash-latest": (0.30, 2.50),
    "gemini-flash-lite-latest": (0.10, 0.40),
    "gemini-pro-latest": (1.25, 10.0),
}

PROMPT = (
    "You are an elite EA FC coach. Watch this gameplay clip and give a short "
    "coaching note: 2 strengths and 2 things to improve for the player. JSON with "
    "keys strengths[] and improve[]."
)


def main() -> None:
    s = get_settings()
    key = s.openai_api_key
    if not key:
        print("No OPENAI_API_KEY (Gemini key) set in .env")
        sys.exit(1)

    path = Path(sys.argv[1])
    model = sys.argv[2] if len(sys.argv) > 2 else "gemini-flash-latest"
    res_mode = sys.argv[3] if len(sys.argv) > 3 else "default"
    raw = path.read_bytes()
    mb = len(raw) / 1e6
    print(f"clip   : {path.name}  ({mb:.1f} MB)   model: {model}   media_res: {res_mode}")
    if mb > 18:
        print("WARNING: >18MB inline may be rejected — use a shorter clip or the File API.")

    b64 = base64.standard_b64encode(raw).decode("ascii")
    gen_cfg: dict = {"maxOutputTokens": 800, "temperature": 0}
    if res_mode == "low":
        gen_cfg["mediaResolution"] = "MEDIA_RESOLUTION_LOW"
    payload = {
        "contents": [{"parts": [
            {"inline_data": {"mime_type": "video/mp4", "data": b64}},
            {"text": PROMPT},
        ]}],
        "generationConfig": gen_cfg,
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()[:600]}")
        sys.exit(1)

    um = data.get("usageMetadata", {})
    pin = um.get("promptTokenCount", 0)
    pout = um.get("candidatesTokenCount", 0)
    total = um.get("totalTokenCount", pin + pout)
    cin, cout = PRICING.get(model, (0.30, 2.50))
    cost = (pin * cin + pout * cout) / 1e6
    print("\n--- REAL usage ---")
    print(f"  prompt (incl. video) tokens : {pin:,}")
    print(f"  output tokens               : {pout:,}")
    print(f"  total tokens                : {total:,}")
    print(f"  cost this call              : ${cost:.5f}")

    # Extrapolate to a full match, assuming most of the input is the video.
    # Estimate tokens/second from this clip if we can infer duration is unknown;
    # instead scale by video bytes is unreliable, so report per-call and a
    # 15-min projection using the observed prompt-token rate if a duration is set.
    try:
        import subprocess
        dur = float(subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nk=1:nw=1", str(path)]).decode().strip())
        tps = pin / dur if dur else 0
        for mins in (15, 18):
            secs = mins * 60
            proj_in = tps * secs
            proj = (proj_in * cin + 1500 * cout) / 1e6
            print(f"  projected {mins}-min match  : ~{proj_in:,.0f} in-tokens -> ${proj:.4f}")
        print(f"  (measured {tps:.0f} video tokens/sec on this clip)")
    except Exception as e:  # noqa: BLE001
        print(f"  (install ffprobe or pass duration to project a full match: {e})")

    # Show the model's answer so we can judge quality too.
    try:
        txt = data["candidates"][0]["content"]["parts"][0]["text"]
        print("\n--- model answer ---\n" + txt[:800])
    except Exception:
        print("\n(no text answer parsed)")


if __name__ == "__main__":
    main()
