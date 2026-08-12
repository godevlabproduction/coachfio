"""Preview the scoreboard crop that Stage 3 now sends to the model."""
import sys
from pathlib import Path

import cv2

from core.pipeline.stages import _scoreboard_crop

src = Path(sys.argv[1])
img = cv2.imread(str(src))
jpeg = _scoreboard_crop(img)
out = src.parent / (src.stem + "_scorecrop.jpg")
out.write_bytes(jpeg)
print(f"WROTE {out}  ({len(jpeg)} bytes, from {img.shape[1]}x{img.shape[0]})")
