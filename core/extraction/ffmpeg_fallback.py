"""Server-side frame extraction FALLBACK.

The primary path extracts frames in the browser (video never uploaded whole).
This exists only for browsers/formats where client-side seeking fails and the
raw video does get uploaded. Requires ffmpeg in the image.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def extract_frames(video_path: str | Path, out_dir: str | Path, fps: float = 1.0) -> list[Path]:
    """Pull frames at `fps` into out_dir as jpg. Returns sorted frame paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(out_dir / "frame_%06d.jpg")
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", str(video_path),
        "-vf", f"fps={fps}",
        "-q:v", "3",
        pattern,
    ]
    subprocess.run(cmd, check=True)
    return sorted(out_dir.glob("frame_*.jpg"))
