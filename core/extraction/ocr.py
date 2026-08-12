"""OCR engine abstraction.

Phase 0's whole question is "does local OCR read the HUD reliably". We keep the
engine behind a Protocol so we can swap PaddleOCR for something else without
touching the pipeline, and so the stack boots with a stub before the (heavy)
Paddle wheels are installed.
"""
from __future__ import annotations

from typing import Protocol

import cv2
import numpy as np


class OcrEngine(Protocol):
    def read_text(self, image_bgr: np.ndarray, whitelist: str | None = None) -> tuple[str, float]:
        """Return (text, confidence in 0..1) for a small pre-cropped region."""
        ...


def _preprocess(image_bgr: np.ndarray) -> np.ndarray:
    """Upscale small HUD crops and boost contrast — OCR is far better on big,
    high-contrast digits than on tiny ones."""
    h, w = image_bgr.shape[:2]
    if h == 0 or w == 0:
        return image_bgr
    scale = max(1, int(round(48 / max(1, h))))  # aim for ~48px tall
    if scale > 1:
        image_bgr = cv2.resize(image_bgr, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.convertScaleAbs(gray, alpha=1.4, beta=0)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def _apply_whitelist(text: str, whitelist: str | None) -> str:
    if not whitelist:
        return text.strip()
    return "".join(ch for ch in text if ch in whitelist).strip()


class StubOcrEngine:
    """No-op engine. Lets the full stack run end-to-end (upload -> pipeline ->
    JSON) before PaddleOCR is installed. Returns empty reads at 0 confidence, so
    parse_confidence is honestly 0 and nothing is silently faked."""

    def read_text(self, image_bgr: np.ndarray, whitelist: str | None = None) -> tuple[str, float]:
        return "", 0.0


class PaddleOcrEngine:
    """Real local OCR (PaddleOCR). Lazily constructed so importing this module
    never triggers the heavy model load."""

    def __init__(self, lang: str = "en") -> None:
        self._lang = lang
        self._ocr = None

    def _engine(self):
        if self._ocr is None:
            from paddleocr import PaddleOCR  # imported lazily on first use

            # `show_log` / `use_angle_cls` kwargs vary across PaddleOCR versions;
            # fall back to a minimal constructor if a kwarg is rejected.
            try:
                self._ocr = PaddleOCR(use_angle_cls=False, lang=self._lang, show_log=False)
            except (TypeError, ValueError):
                self._ocr = PaddleOCR(lang=self._lang)
        return self._ocr

    def read_text(self, image_bgr: np.ndarray, whitelist: str | None = None) -> tuple[str, float]:
        img = _preprocess(image_bgr)
        engine = self._engine()
        try:
            result = engine.ocr(img, cls=False)
        except TypeError:
            result = engine.ocr(img)  # newer versions dropped the cls kwarg
        if not result or not result[0]:
            return "", 0.0
        # result[0] = [ [box, (text, conf)], ... ] — join lines, average conf.
        texts, confs = [], []
        for line in result[0]:
            text, conf = line[1][0], float(line[1][1])
            texts.append(text)
            confs.append(conf)
        merged = _apply_whitelist(" ".join(texts), whitelist)
        conf = sum(confs) / len(confs) if confs else 0.0
        # If whitelisting stripped everything, the read is unusable.
        if whitelist and not merged:
            return "", 0.0
        return merged, conf


def get_ocr_engine(name: str) -> OcrEngine:
    name = (name or "paddle").lower()
    if name == "stub":
        return StubOcrEngine()
    if name == "paddle":
        return PaddleOcrEngine()
    raise ValueError(f"unknown OCR engine: {name}")
