"""Base class that makes an adapter ~90% declarative.

A concrete adapter points at a config directory:

    config/
      game.yaml       identity, event vocabulary, metric definitions, validation
      hud.yaml        the HUD schema (regions, formats) — pure data
      prompts/
        stage2.txt    (Phase 1)
        stage3.txt    (Phase 1)

…and only writes `interpret()` (the small parser) plus any custom validation.
Everything else is loaded from YAML here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from core.models.enums import EventCategory, SourceType
from adapters.base.hud_schema import HudSchema
from adapters.base.interface import (
    EventTypeDef,
    GameAdapter,
    GameIdentity,
    MetricDefinition,
    ParsedHud,
)


class ConfigDrivenAdapter(GameAdapter):
    #: Subclass sets this to its config directory (a Path).
    config_dir: Path

    def __init__(self) -> None:
        if not getattr(self, "config_dir", None):
            raise ValueError(f"{type(self).__name__} must set config_dir")
        self._game_cfg = self._load_yaml(self.config_dir / "game.yaml")
        self._hud_cfg = self._load_yaml(self.config_dir / "hud.yaml")

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        # hud.yaml is optional — replay/API games (no video) don't have one.
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    # --- identity -----------------------------------------------------------
    def identity(self) -> GameIdentity:
        ident = self._game_cfg["identity"]
        return GameIdentity(
            game_id=ident["game_id"],
            display_name=ident["display_name"],
            franchise=ident.get("franchise", ident["game_id"]),
            edition=str(ident["edition"]),
            platforms=ident.get("platforms", []),
            supported_sources=[SourceType(s) for s in ident.get("supported_sources", ["video"])],
        )

    # --- HUD schema (data) --------------------------------------------------
    def hud_schema(self, capture: dict[str, Any] | None = None) -> HudSchema:
        # No hud.yaml (replay/API game) -> an empty schema; such adapters use
        # ingest() instead of the OCR path.
        if not self._hud_cfg:
            ident = self.identity()
            return HudSchema(game_id=ident.game_id, edition=ident.edition, regions=[])
        # A game may declare platform/resolution variants; pick one from capture.
        cfg = dict(self._hud_cfg)
        variants = cfg.pop("variants", {}) or {}
        variant_key = None
        if capture:
            variant_key = capture.get("platform") or capture.get("variant")
        if variant_key and variant_key in variants:
            # Shallow-merge the variant's overrides (e.g. a different region set).
            cfg = {**cfg, **variants[variant_key]}
        return HudSchema(**cfg)

    # --- event vocabulary ---------------------------------------------------
    def event_vocabulary(self) -> list[EventTypeDef]:
        out: list[EventTypeDef] = []
        for e in self._game_cfg.get("events", []):
            out.append(
                EventTypeDef(
                    game_type=e["game_type"],
                    category=EventCategory(e["category"]),
                    description=e.get("description", ""),
                )
            )
        return out

    # --- metric definitions -------------------------------------------------
    def metric_definitions(self) -> list[MetricDefinition]:
        return [MetricDefinition(**m) for m in self._game_cfg.get("metrics", [])]

    # --- prompts (Phase 1) --------------------------------------------------
    def stage_prompt(self, stage: int) -> str:
        path = self.config_dir / "prompts" / f"stage{stage}.txt"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    # --- interpret ----------------------------------------------------------
    def interpret(self, readings):  # noqa: ANN001 - subclass implements
        raise NotImplementedError

    # --- generic declarative validation ------------------------------------
    def validate(self, parsed: ParsedHud) -> list[str]:
        """Apply the declarative rules in game.yaml `validation:`.

        Supported (game-agnostic) rule kinds:
          - {metric: <key>, min: x, max: y}     value must fall in range
          - {metric: <key>, non_negative: true}
        Game-specific temporal rules (e.g. score monotonicity across snapshots)
        live in the subclass.
        """
        warnings: list[str] = []
        by_key = {m.key: m for m in parsed.metrics}
        for rule in self._game_cfg.get("validation", []):
            key = rule.get("metric")
            m = by_key.get(key) if key else None
            if m is None:
                continue
            if "min" in rule and m.value < rule["min"]:
                warnings.append(f"{key}={m.value} below min {rule['min']}")
            if "max" in rule and m.value > rule["max"]:
                warnings.append(f"{key}={m.value} above max {rule['max']}")
            if rule.get("non_negative") and m.value < 0:
                warnings.append(f"{key}={m.value} is negative")
        return warnings
