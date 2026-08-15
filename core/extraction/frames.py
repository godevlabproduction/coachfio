"""Frame model. In Phase 0 frames are extracted IN THE BROWSER and uploaded as
JPEGs; the backend just loads them. (A server-side ffmpeg fallback lives in
core/extraction/ffmpeg_fallback.py for formats the browser can't seek.)"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class Frame:
    index: int
    timestamp_ms: int
    # Object-store key for the uploaded JPEG.
    key: str
    # Lazily loaded BGR image (numpy HxWx3). None until decoded.
    image: np.ndarray | None = field(default=None, repr=False)

    @property
    def size(self) -> tuple[int, int]:
        if self.image is None:
            raise ValueError("frame image not loaded")
        h, w = self.image.shape[:2]
        return w, h


def load_frame_image(jpeg_bytes: bytes) -> np.ndarray:
    """Decode JPEG bytes to a BGR image."""
    arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("failed to decode frame JPEG")
    return img


def encode_jpeg(image_bgr: np.ndarray, max_width: int = 640, quality: int = 70) -> bytes:
    """Downscale to <= max_width and JPEG-encode - used to send frames to the
    vision models. Downscaling hard is the whole cost strategy: 640x360 is enough
    to classify a scene (Stage 2); Stage 3 uses a larger width on a few frames."""
    h, w = image_bgr.shape[:2]
    if w > max_width:
        scale = max_width / w
        image_bgr = cv2.resize(image_bgr, (max_width, int(round(h * scale))), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", image_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise ValueError("failed to JPEG-encode frame")
    return buf.tobytes()
