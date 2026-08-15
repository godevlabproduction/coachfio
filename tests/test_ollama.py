"""OllamaVisionModel - payload shape + response parsing, with the HTTP call
mocked so no server is needed."""
import core.ai.vision as vision
from core.ai.vision import OllamaVisionModel, get_vision_model


def test_ollama_builds_payload_and_parses(monkeypatch):
    captured = {}

    def fake_post(url, payload, timeout):
        captured["url"] = url
        captured["payload"] = payload
        return {"message": {"content": '{"label": "goal", "confidence": 0.8}'}}

    monkeypatch.setattr(vision, "_ollama_post", fake_post)

    model = OllamaVisionModel(base_url="http://host.docker.internal:11434")
    schema = {"type": "object", "properties": {"label": {"type": "string"}}}
    res = model.generate(model="qwen2.5vl:3b", prompt="classify", images_jpeg=[b"jpegbytes"], schema=schema, max_tokens=64)

    assert captured["url"].endswith("/api/chat")
    msg = captured["payload"]["messages"][0]
    assert msg["images"] and isinstance(msg["images"][0], str)   # base64 string
    assert captured["payload"]["format"] == schema               # structured output
    assert res.data == {"label": "goal", "confidence": 0.8}
    assert res.input_tokens == 0 and res.output_tokens == 0      # local = free


def test_ollama_is_free_engine():
    assert OllamaVisionModel().free is True
    assert get_vision_model("ollama").free is True


def test_factory_rejects_unknown_engine():
    import pytest
    with pytest.raises(ValueError):
        get_vision_model("gpt5-turbo-9000")
