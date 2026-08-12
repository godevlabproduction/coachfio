"""Extract still frames from a match's stored native video (to inspect the HUD /
bottom-bar player badges / kit colours).

    docker compose run --rm api python -m tools.grab_video_frame <match_id> <ts1> [ts2 ...]
    ts like 00:20 or 00:04:00
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from core.storage.frame_keys import source_key
from core.storage.objectstore import get_object_store

OUT = Path("/app/reports/frames")


def main() -> None:
    mid = sys.argv[1]
    stamps = sys.argv[2:] or ["00:20"]
    data = get_object_store().get(source_key(mid))
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        vid = Path(tmp) / "match.mp4"
        vid.write_bytes(data)
        for ts in stamps:
            safe = ts.replace(":", "-")
            out = OUT / f"{mid[:8]}_{safe}.jpg"
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-ss", ts, "-i", str(vid), "-frames:v", "1", "-q:v", "2", str(out)],
                check=True,
            )
            print(f"WROTE {out}")


if __name__ == "__main__":
    main()
