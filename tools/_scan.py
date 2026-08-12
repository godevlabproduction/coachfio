"""Throwaway diagnostic: per-frame OCR of the away-score + clock regions, to see
exactly which frames produce a spurious score. Not part of the product."""
import sys
import tempfile

from adapters.base.registry import load_builtin_adapters, registry
from core.extraction.ffmpeg_fallback import extract_frames
from core.extraction.frames import load_frame_image
from core.extraction.ocr import get_ocr_engine

video = sys.argv[1]
load_builtin_adapters()
ad = registry.get("ea-fc", "26")
sch = ad.hud_schema({})
ocr = get_ocr_engine("paddle")
sa = sch.region("score_away")
sh = sch.region("score_home")
ck = sch.region("clock")

with tempfile.TemporaryDirectory() as tmp:
    paths = extract_frames(video, tmp, fps=1)
    for i, p in enumerate(paths):
        img = load_frame_image(p.read_bytes())
        h, w = img.shape[:2]

        def crop(r):
            x0, y0, x1, y1 = r.pixel_box(w, h)
            return img[y0:y1, x0:x1]

        ct, cc = ocr.read_text(crop(ck), ck.whitelist)
        ht, hc = ocr.read_text(crop(sh), sh.whitelist)
        at, ac = ocr.read_text(crop(sa), sa.whitelist)
        # Only print frames where away read a digit, to spot the spurious ones.
        if at.strip():
            print(f"f{i:03d} clk={ct!r}({cc:.2f}) home={ht!r}({hc:.2f}) away={at!r}({ac:.2f})")
