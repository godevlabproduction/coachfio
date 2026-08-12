"""Adapter registry + HUD schema: the game-agnostic seams the whole design
depends on."""
import pytest

from adapters.base.hud_schema import SceneKind
from adapters.base.registry import UnknownGameError, load_builtin_adapters, registry


def setup_module(_):
    load_builtin_adapters()


def test_fc26_is_registered_by_edition():
    ad = registry.get("ea-fc", "26")
    ident = ad.identity()
    assert ident.display_name == "EA Sports FC 26"
    assert ident.edition == "26"


def test_unknown_edition_raises():
    # Adapters are versioned per edition, not per franchise.
    with pytest.raises(UnknownGameError):
        registry.get("ea-fc", "99")


def test_normalized_regions_scale_to_pixels():
    ad = registry.get("ea-fc", "26")
    schema = ad.hud_schema({})
    region = schema.region("score_home")
    assert region is not None
    x0, y0, x1, y1 = region.pixel_box(1280, 720)
    assert 0 <= x0 < x1 <= 1280
    assert 0 <= y0 < y1 <= 720
    # Same schema, different resolution -> proportionally larger box.
    assert region.pixel_box(1920, 1080)[2] > x1


def test_variant_selection_switches_layout():
    ad = registry.get("ea-fc", "26")
    console = ad.hud_schema({})
    broadcast = ad.hud_schema({"variant": "fcpro_broadcast"})
    assert console.schema_version != broadcast.schema_version


def test_metric_definitions_present():
    ad = registry.get("ea-fc", "26")
    keys = {m.key for m in ad.metric_definitions()}
    assert {"score_home", "score_away"} <= keys


def test_in_match_regions_exist():
    ad = registry.get("ea-fc", "26")
    schema = ad.hud_schema({})
    roles = {r.meta.get("role") for r in schema.regions_for(SceneKind.IN_MATCH)}
    assert "score" in roles and "clock" in roles
