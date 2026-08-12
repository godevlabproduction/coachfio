"""Phase 0 CLI — run the extraction pipeline against a real clip WITHOUT the web
stack or a database. This is the fastest loop for the only question that matters
right now: does OCR read real footage reliably?

    # Analyse a clip and print the extracted stats as JSON
    python -m tools.cli analyze match.mp4 --fps 1

    # Draw the HUD region boxes on a frame so you can calibrate coordinates
    python -m tools.cli overlay match.mp4 --at 60 --scene stat_screen --out overlay.png

Run inside the backend container (has ffmpeg + OCR):
    docker compose run --rm -v "$PWD:/data" api python -m tools.cli analyze /data/match.mp4
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2

from adapters.base.hud_schema import SceneKind
from adapters.base.registry import load_builtin_adapters, registry
from core.extraction.frames import Frame, load_frame_image
from core.extraction.ffmpeg_fallback import extract_frames
from core.extraction.hud import HudReader
from core.extraction.ocr import get_ocr_engine
from core.extraction.scene import SceneDetector


def _load_frames_from_dir(paths: list[Path], fps: float) -> list[Frame]:
    frames: list[Frame] = []
    step_ms = int(1000 / fps)
    for i, p in enumerate(sorted(paths)):
        frames.append(
            Frame(index=i, timestamp_ms=i * step_ms, key=str(p), image=load_frame_image(p.read_bytes()))
        )
    return frames


def cmd_analyze(args: argparse.Namespace) -> int:
    load_builtin_adapters()
    adapter = registry.get(args.game, args.edition)
    ocr = get_ocr_engine(args.ocr)

    with tempfile.TemporaryDirectory() as tmp:
        paths = extract_frames(args.video, tmp, fps=args.fps)
        if not paths:
            print("no frames extracted", file=sys.stderr)
            return 2
        frames = _load_frames_from_dir(paths, args.fps)

        if args.full:
            out = _run_full(adapter, ocr, frames, args)
        else:
            scene = SceneDetector().analyze(frames)
            schema = adapter.hud_schema({"platform": args.platform, "resolution": args.resolution})
            readings, stat_frames = HudReader(ocr).read(frames, schema, scene)
            parsed = adapter.interpret(readings)
            out = {
                "video": str(args.video),
                "frames": len(frames),
                "scene_changes": len(scene.scene_changes),
                "stat_screen_frames": stat_frames,
                "outcome": parsed.outcome,
                "parse_confidence": parsed.parse_confidence,
                "warnings": parsed.warnings,
                "metrics": [m.model_dump() for m in parsed.metrics],
                "events": [e.model_dump() for e in parsed.events],
            }
    print(json.dumps(out, indent=2, default=str))
    return 0


def _run_full(adapter, ocr, frames, args) -> dict:
    """Run the ENTIRE pipeline (Stages 1-3) with the configured vision engine —
    the CLI equivalent of a worker run, minus the DB/upload. Uses .env settings
    (VISION_ENGINE, ENABLE_STAGE_2/3, models, budget)."""
    from core.ai.vision import build_vision
    from core.config import get_settings
    from core.models.domain import Match
    from core.pipeline.context import PipelineContext
    from core.pipeline.cost import CostAccountant
    from core.pipeline.runner import run_pipeline
    from core.storage.objectstore import get_object_store

    s = get_settings()
    try:
        store = get_object_store()
    except Exception:
        store = None
    ident = adapter.identity()
    duration_ms = max((f.timestamp_ms for f in frames), default=0)
    match = Match(
        game_id=ident.game_id, game_edition=ident.edition,
        capture={"platform": args.platform, "resolution": args.resolution},
    )
    ctx = PipelineContext(
        match=match, adapter=adapter, frames=frames, ocr=ocr, settings=s,
        cost=CostAccountant.for_match(s.match_budget_usd, duration_ms),
        vision=build_vision(s),
        object_store=store,
    )
    run_pipeline(ctx)
    return {
        "video": str(args.video),
        "frames": len(frames),
        "status": match.status.value,
        "vision_engine": s.vision_engine,
        "stage2": s.enable_stage_2,
        "stage3": s.enable_stage_3,
        "outcome": match.outcome,
        "parse_confidence": match.parse_confidence,
        "cost_usd": match.cost_usd,
        "warnings": match.warnings,
        "metrics": [m.model_dump() for m in match.metrics],
        "events": [e.model_dump() for e in match.events],
        "insights": [i.model_dump() for i in match.insights],
    }


def _grab_frame(video: str, at_sec: float, out_path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(at_sec),
         "-i", video, "-frames:v", "1", "-q:v", "2", "-y", str(out_path)],
        check=True,
    )


def cmd_overlay(args: argparse.Namespace) -> int:
    """Draw the schema's region boxes on one frame — the calibration workflow.
    Nudge the rects in hud.yaml until the boxes sit on the numbers."""
    load_builtin_adapters()
    adapter = registry.get(args.game, args.edition)
    schema = adapter.hud_schema({"platform": args.platform, "resolution": args.resolution})
    scene = SceneKind(args.scene)

    with tempfile.TemporaryDirectory() as tmp:
        frame_path = Path(tmp) / "frame.jpg"
        _grab_frame(str(args.video), args.at, frame_path)
        img = load_frame_image(frame_path.read_bytes())

    h, w = img.shape[:2]
    ocr = get_ocr_engine(args.ocr)
    for region in schema.regions_for(scene):
        x0, y0, x1, y1 = region.pixel_box(w, h)
        cv2.rectangle(img, (x0, y0), (x1, y1), (0, 0, 255), 2)
        text, conf = ocr.read_text(img[y0:y1, x0:x1], region.whitelist)
        label = f"{region.name}: '{text}' ({conf:.2f})"
        cv2.putText(img, label, (x0, max(12, y0 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
        print(label)

    cv2.imwrite(str(args.out), img)
    print(f"wrote {args.out}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="coachio", description="Phase 0 extraction CLI")
    p.add_argument("--game", default="ea-fc")
    p.add_argument("--edition", default="26")
    p.add_argument("--platform", default="ps5")
    p.add_argument("--resolution", default="1920x1080")
    p.add_argument("--ocr", default="paddle", choices=["paddle", "stub"])
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="extract stats from a clip -> JSON")
    a.add_argument("video")
    a.add_argument("--fps", type=float, default=1.0)
    a.add_argument("--full", action="store_true",
                   help="run the whole pipeline (Stages 1-3) with the configured vision engine")
    a.set_defaults(func=cmd_analyze)

    o = sub.add_parser("overlay", help="draw HUD region boxes on a frame for calibration")
    o.add_argument("video")
    o.add_argument("--at", type=float, default=60.0, help="seconds into the clip")
    o.add_argument("--scene", default="stat_screen", choices=["in_match", "stat_screen", "any"])
    o.add_argument("--out", type=Path, default=Path("overlay.png"))
    o.set_defaults(func=cmd_overlay)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
