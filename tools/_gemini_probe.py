"""Throwaway: verify the configured 'openai' engine (Gemini) actually works -
auth, model name, image handling - with ONE tiny synthetic frame. No upload.

    docker compose run --rm api python -m tools._gemini_probe
"""
import json

import cv2
import numpy as np

from core.ai.vision import build_vision
from core.config import get_settings

s = get_settings()
print(f"engine   : {s.vision_engine}")
print(f"base_url : {s.openai_base_url}")
print(f"model    : {s.stage3_model}")
print(f"key set  : {'yes' if s.openai_api_key else 'NO'} (len={len(s.openai_api_key)})")

# A trivial 320x180 frame with some text so the model has something to read.
img = np.full((180, 320, 3), 30, np.uint8)
cv2.putText(img, "2 - 1", (110, 100), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)
ok, buf = cv2.imencode(".jpg", img)
jpeg = buf.tobytes()

vm = build_vision(s)
schema = {
    "type": "object",
    "properties": {"home": {"type": "integer"}, "away": {"type": "integer"}},
    "required": ["home", "away"],
}
try:
    res = vm.generate(
        model=s.stage3_model,
        prompt="Read the two numbers on the scoreboard as home and away integers.",
        images_jpeg=[jpeg],
        schema=schema,
        max_tokens=64,
    )
    print("\nSUCCESS")
    print(f"  raw   : {res.text!r}")
    print(f"  data  : {json.dumps(res.data)}")
    print(f"  tokens: in={res.input_tokens} out={res.output_tokens}")
    print(f"  cost  : ${res.cost_usd:.6f}")
except Exception as e:  # noqa: BLE001
    print("\nFAILED")
    print(f"  {type(e).__name__}: {e}")
