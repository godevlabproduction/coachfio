"""The OpenAI-compatible vision engine: request shape, JSON parse, cost from rates.

No network - we monkeypatch the stdlib POST helper with a canned response.
"""
import core.ai.vision as vision
from core.ai.vision import OpenAICompatVisionModel, build_vision, get_vision_model
from core.config import Settings


def _fake_response(content: str, prompt_tokens=1000, completion_tokens=200):
    return {
        "model": "gemini-2.5-flash",
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


def test_openai_engine_builds_request_and_prices_from_rates(monkeypatch):
    captured = {}

    def fake_post(url, payload, timeout, headers=None):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        return _fake_response('{"label": "counter_attack", "confidence": 0.8}')

    monkeypatch.setattr(vision, "_http_post_json", fake_post)

    model = OpenAICompatVisionModel(
        base_url="https://example.com/v1", api_key="k-123",
        in_usd_per_mtok=0.30, out_usd_per_mtok=2.50,
    )
    schema = {"type": "object", "properties": {"label": {"type": "string"}}}
    res = model.generate(model="gemini-2.5-flash", prompt="classify", images_jpeg=[b"\xff\xd8jpeg"], schema=schema)

    # endpoint + auth
    assert captured["url"] == "https://example.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer k-123"

    # message content: the prompt text + an image_url data URI
    content = captured["payload"]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "classify"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    # The real schema is passed so the model uses our exact keys.
    rf = captured["payload"]["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["schema"] == schema

    # parsed data + cost priced from the configured rates
    assert res.data == {"label": "counter_attack", "confidence": 0.8}
    assert res.input_tokens == 1000 and res.output_tokens == 200
    expected = (1000 * 0.30 + 200 * 2.50) / 1_000_000.0
    assert abs(res.cost_usd - expected) < 1e-12


def test_openai_engine_falls_back_from_json_schema(monkeypatch):
    # Ladder: json_schema -> json_object -> plain. A provider that rejects
    # json_schema must still succeed via json_object.
    seen = []

    def fake_post(url, payload, timeout, headers=None):
        rf = payload.get("response_format", {}).get("type")
        seen.append(rf)
        if rf == "json_schema":
            raise RuntimeError("provider rejects json_schema")
        return _fake_response('{"ok": true}')

    monkeypatch.setattr(vision, "_http_post_json", fake_post)
    model = OpenAICompatVisionModel("https://x/v1", "k")
    res = model.generate(model="m", prompt="p", images_jpeg=[b"j"],
                         schema={"type": "object", "properties": {"ok": {}}})
    assert seen == ["json_schema", "json_object"] and res.data == {"ok": True}


def test_build_vision_selects_openai_from_settings():
    s = Settings(
        vision_engine="openai", openai_base_url="https://api.example/v1",
        openai_api_key="key", openai_input_usd_per_mtok=0.4, openai_output_usd_per_mtok=1.6,
    )
    model = build_vision(s)
    assert isinstance(model, OpenAICompatVisionModel)
    assert model.input_usd_per_mtok == 0.4 and model.output_usd_per_mtok == 1.6
    assert model.free is False


def test_get_vision_model_unknown_engine_raises():
    try:
        get_vision_model("does-not-exist")
    except ValueError as e:
        assert "unknown vision engine" in str(e)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")
