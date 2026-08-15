"""Adapter registry. The core resolves a `(game_id, edition)` to an adapter
instance here - this is the ONLY place that knows which games exist, and it's a
lookup table, not a branch. No `if game == "fc26"` anywhere else."""
from __future__ import annotations

from adapters.base.interface import GameAdapter


class UnknownGameError(KeyError):
    pass


class AdapterRegistry:
    def __init__(self) -> None:
        self._by_key: dict[str, GameAdapter] = {}

    def register(self, adapter: GameAdapter) -> GameAdapter:
        ident = adapter.identity()
        # Versioned per EDITION, not per franchise: "ea-fc@26" != "ea-fc@27".
        self._by_key[f"{ident.game_id}@{ident.edition}"] = adapter
        return adapter

    def get(self, game_id: str, edition: str) -> GameAdapter:
        key = f"{game_id}@{edition}"
        if key not in self._by_key:
            raise UnknownGameError(key)
        return self._by_key[key]

    def all(self) -> list[GameAdapter]:
        return list(self._by_key.values())

    def keys(self) -> list[str]:
        return list(self._by_key)


registry = AdapterRegistry()


def load_builtin_adapters() -> None:
    """Import-and-register shipped adapters. Called once at startup. Adding a
    game means adding one line here (or, later, entry-point discovery)."""
    from adapters.cs2.adapter import Cs2Adapter
    from adapters.ea_fc_26.adapter import EaFc26Adapter

    if "ea-fc@26" not in registry.keys():
        registry.register(EaFc26Adapter())
    if "cs2@2" not in registry.keys():
        registry.register(Cs2Adapter())
