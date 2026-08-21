"""Debug: run ONE real segment call and ONE synthesis call through the configured
engine and print the RAW text + parsed data, so we can see why Gemini returns
empty/mis-shaped JSON.

    docker compose run --rm api python -m tools._gemini_seg_probe /app/reports/frames/frame_00270.jpg
"""
import json
import sys

import cv2

from core.ai.vision import build_vision
from core.config import get_settings
from core.pipeline.stages import _WINDOW_NOTES_SCHEMA, _scoreboard_crop

s = get_settings()
img = cv2.imread(sys.argv[1])
crop = _scoreboard_crop(img)
ok, full = cv2.imencode(".jpg", cv2.resize(img, (768, int(768 * img.shape[0] / img.shape[1]))))
vm = build_vision(s)

print(f"=== SEGMENT call on {s.stage2_model} ===")
seg_prompt = (
    "IMAGE 1 is a ZOOMED crop of the top-left scoreboard; IMAGE 2 is a play frame.\n"
    "1) From IMAGE 1 read the scoreboard: TOP number = home score, number BELOW = away score, "
    "and the clock. Read digits literally (a thin '1' is not an '8').\n"
    "2) From IMAGE 2 note 1-2 concrete things about the home player.\n"
    "Respond ONLY with JSON."
)
r = vm.generate(model=s.stage2_model, prompt=seg_prompt, images_jpeg=[crop, full.tobytes()],
                schema=_WINDOW_NOTES_SCHEMA, max_tokens=2000)
print("RAW :", repr(r.text)[:800])
print("DATA:", json.dumps(r.data))
print(f"tok  : in={r.input_tokens} out={r.output_tokens} cost=${r.cost_usd:.6f}")

print(f"\n=== SYNTHESIS call on {s.stage3_model} ===")
coach_schema = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "recurring_mistakes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary"],
}
r2 = vm.generate(
    model=s.stage3_model,
    prompt="Write a one-line football coaching summary and 2 recurring mistakes. Respond ONLY with JSON.",
    images_jpeg=[], schema=coach_schema, max_tokens=800,
)
print("RAW :", repr(r2.text)[:1500])
print("DATA:", json.dumps(r2.data))
print(f"tok  : in={r2.input_tokens} out={r2.output_tokens} cost=${r2.cost_usd:.6f}")
