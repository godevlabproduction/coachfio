from __future__ import annotations

from fastapi import APIRouter

from adapters.base.registry import registry
from api.schemas import GameInfo

router = APIRouter(prefix="/api/games", tags=["games"])


@router.get("", response_model=list[GameInfo])
def list_games() -> list[GameInfo]:
    out: list[GameInfo] = []
    for adapter in registry.all():
        ident = adapter.identity()
        out.append(
            GameInfo(
                game_id=ident.game_id,
                edition=ident.edition,
                display_name=ident.display_name,
                franchise=ident.franchise,
                platforms=ident.platforms,
                supported_sources=[s.value for s in ident.supported_sources],
                metric_keys=[m.key for m in adapter.metric_definitions()],
            )
        )
    return out
