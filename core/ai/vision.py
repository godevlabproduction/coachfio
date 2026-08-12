"""Vision model abstraction for Stages 2 & 3.

The pipeline never imports the Anthropic SDK directly — it calls a `VisionModel`
with an adapter-supplied prompt, a few JPEG frames, and an optional JSON schema,
and gets back parsed data + token usage. Swap `anthropic` for `stub` (or a local
model later) without touching the pipeline.
"""
from __future__ import annotations

import base64
import json
import sys
from dataclasses import dataclass
from typing import Any, Protocol

# Some model responses embed very long integer-like tokens; Python 3.11 caps
# int(str) at 4300 digits and raises otherwise. Lift it so JSON parsing can't
# crash a match. Process-wide + idempotent.
try:
    sys.set_int_max_str_digits(1_000_000)
except Exception:  # noqa: BLE001 - older/newer runtimes without the knob
    pass


@dataclass
class VisionResult:
    data: dict[str, Any]       # parsed JSON (empty dict if parsing failed)
    text: str                  # raw model text
    input_tokens: int
    output_tokens: int
    model: str
    # Engine-computed actual cost. 0.0 means "let the pipeline price it from the
    # model table" (used by the Anthropic engine); cheap providers set it here.
    cost_usd: float = 0.0


def _b64(jpeg: bytes) -> str:
    return base64.standard_b64encode(jpeg).decode("ascii")


def _parse_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Tolerate a fenced/wrapped blob: grab the outermost {...}.
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return {}
        return {}


class VisionModel(Protocol):
    # True when calls are free (local/stub) — the pipeline then skips the budget
    # estimate so a free engine can't be halted by the $-cap.
    free: bool

    def generate(
        self,
        model: str,
        prompt: str,
        images_jpeg: list[bytes],
        schema: dict[str, Any] | None = None,
        max_tokens: int = 512,
    ) -> VisionResult: ...


class StubVisionModel:
    """No-network engine. Lets the full Stage 2/3 path run (and be tested)
    without an API key or spend. Returns a schema-shaped, honest placeholder and
    reports zero tokens so it costs $0 — it never fabricates events."""

    free = True

    def generate(self, model, prompt, images_jpeg, schema=None, max_tokens=512) -> VisionResult:
        props = (schema or {}).get("properties", {})
        if "label" in props:
            data = {"label": "in_play", "confidence": 0.0}
        elif "summary" in props or "kind" in props:
            data = {"kind": "note", "summary": "(stub vision engine — no analysis)",
                    "factors": [], "coaching_point": ""}
        else:
            data = {}
        return VisionResult(data=data, text=json.dumps(data), input_tokens=0, output_tokens=0, model=model)


class AnthropicVisionModel:
    """Real vision via the Anthropic Messages API. Lazily constructed so importing
    this module never requires the SDK or a key."""

    free = False

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or None
        self._client = None

    def _client_or_init(self):
        if self._client is None:
            import anthropic  # lazy

            self._client = anthropic.Anthropic(api_key=self._api_key) if self._api_key else anthropic.Anthropic()
        return self._client

    @staticmethod
    def _thinking_for(model: str) -> dict | None:
        # Sonnet 5 / Opus 5 / Opus 4.8 run adaptive thinking by default; for cheap
        # structured extraction we don't need it and it eats the token budget.
        if any(m in model for m in ("sonnet-5", "opus-5", "opus-4-8")):
            return {"type": "disabled"}
        return None  # Haiku 4.5 etc. — leave default (no thinking)

    def generate(self, model, prompt, images_jpeg, schema=None, max_tokens=512) -> VisionResult:
        client = self._client_or_init()
        content: list[dict] = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _b64(j)}}
            for j in images_jpeg
        ]
        content.append({"type": "text", "text": prompt})

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": content}],
        }
        thinking = self._thinking_for(model)
        if thinking:
            kwargs["thinking"] = thinking
        if schema:
            kwargs["output_config"] = {"format": {"type": "json_schema", "schema": schema}}

        try:
            resp = client.messages.create(**kwargs)
        except Exception:
            # Structured-output or thinking kwarg rejected by this model — retry bare.
            kwargs.pop("output_config", None)
            kwargs.pop("thinking", None)
            resp = client.messages.create(**kwargs)

        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
        usage = resp.usage
        return VisionResult(
            data=_parse_json(text),
            text=text,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            model=getattr(resp, "model", model),
        )


def _http_post_json(url: str, payload: dict[str, Any], timeout: float, headers: dict | None = None) -> dict[str, Any]:
    """POST JSON with stdlib only (no extra dependency). Isolated for tests.
    Retries on 429/5xx with exponential backoff so a burst of small calls (e.g.
    the scoreboard reads) rides out provider rate-limiting instead of failing."""
    import time
    import urllib.error
    import urllib.request

    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    body = json.dumps(payload).encode("utf-8")
    last: Exception | None = None
    for attempt in range(5):
        req = urllib.request.Request(url, data=body, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in (408, 409, 429, 500, 502, 503, 504) or attempt == 4:
                raise
        except urllib.error.URLError as exc:
            last = exc
            if attempt == 4:
                raise
        time.sleep(min(2 ** attempt, 12))
    raise last  # unreachable, but keeps type-checkers happy


def _ollama_post(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    return _http_post_json(url, payload, timeout)


class OpenAICompatVisionModel:
    """Vision via any OpenAI-compatible chat/completions endpoint — Google Gemini
    (…/v1beta/openai), Alibaba Qwen-VL (DashScope compatible-mode), Zhipu GLM,
    Moonshot, OpenRouter, local vLLM. Set base_url + api_key + model in config.
    Computes its own cost from configured $/Mtok rates (cheap providers)."""

    free = False

    def __init__(self, base_url: str, api_key: str, in_usd_per_mtok: float = 0.3,
                 out_usd_per_mtok: float = 1.2, timeout: float = 300.0) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._key = api_key
        self.input_usd_per_mtok = in_usd_per_mtok
        self.output_usd_per_mtok = out_usd_per_mtok
        self._timeout = timeout

    def _post(self, payload):
        return _http_post_json(
            self._url, payload, self._timeout,
            headers={"Authorization": f"Bearer {self._key}"},
        )

    def generate(self, model, prompt, images_jpeg, schema=None, max_tokens=1500) -> VisionResult:
        content: list[dict] = [{"type": "text", "text": prompt}]
        for j in images_jpeg:
            content.append({"type": "image_url",
                            "image_url": {"url": "data:image/jpeg;base64," + _b64(j)}})
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        # Pass the real JSON schema so the model uses our exact keys (Gemini/OpenAI
        # otherwise invent names like "coaching_summary" or nest under "scoreboard").
        if schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": schema},
            }
        try:
            data = self._post(payload)
        except Exception:
            # Some providers reject json_schema -> try json_object -> then plain.
            try:
                payload["response_format"] = {"type": "json_object"}
                data = self._post(payload)
            except Exception:
                payload.pop("response_format", None)
                data = self._post(payload)

        msg = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "") or ""
        usage = data.get("usage") or {}
        intok = int(usage.get("prompt_tokens", 0) or 0)
        outtok = int(usage.get("completion_tokens", 0) or 0)
        cost = (intok * self.input_usd_per_mtok + outtok * self.output_usd_per_mtok) / 1_000_000.0
        return VisionResult(
            data=_parse_json(msg), text=msg, input_tokens=intok, output_tokens=outtok,
            model=data.get("model", model), cost_usd=cost,
        )


class OllamaVisionModel:
    """Local vision via Ollama (http://<host>:11434). $0, fully offline. Suits a
    small VLM like `qwen2.5vl:3b` on a modest GPU. From inside a container the
    host runs at host.docker.internal; Ollama must listen on 0.0.0.0
    (OLLAMA_HOST=0.0.0.0) for the container to reach it."""

    free = True

    def __init__(
        self,
        base_url: str = "http://host.docker.internal:11434",
        timeout: float = 300.0,
        num_gpu: int = -1,
    ) -> None:
        self._url = base_url.rstrip("/") + "/api/chat"
        self._timeout = timeout
        # -1 = let Ollama decide; 0 = force CPU (needed when the GPU driver is too
        # old for the CUDA build — the PTX-toolchain crash).
        self._num_gpu = num_gpu

    def generate(self, model, prompt, images_jpeg, schema=None, max_tokens=512) -> VisionResult:
        options: dict[str, Any] = {"temperature": 0, "num_predict": max_tokens}
        if self._num_gpu >= 0:
            options["num_gpu"] = self._num_gpu
        payload: dict[str, Any] = {
            "model": model,
            "stream": False,
            "messages": [{"role": "user", "content": prompt, "images": [_b64(j) for j in images_jpeg]}],
            "options": options,
        }
        if schema:
            payload["format"] = schema  # Ollama structured output (JSON schema)
        try:
            data = _ollama_post(self._url, payload, self._timeout)
        except Exception:
            # Older Ollama may reject a schema `format`; retry asking for plain JSON.
            payload["format"] = "json"
            data = _ollama_post(self._url, payload, self._timeout)

        text = (data.get("message") or {}).get("content", "") or ""
        # Local calls are free — report zero tokens so cost accounting stays $0.
        return VisionResult(data=_parse_json(text), text=text, input_tokens=0, output_tokens=0, model=model)


def get_vision_model(
    engine: str,
    api_key: str | None = None,
    ollama_base_url: str = "http://host.docker.internal:11434",
    ollama_num_gpu: int = -1,
    openai_base_url: str = "",
    openai_api_key: str = "",
    openai_in_rate: float = 0.3,
    openai_out_rate: float = 1.2,
) -> VisionModel:
    engine = (engine or "stub").lower()
    if engine == "stub":
        return StubVisionModel()
    if engine == "anthropic":
        return AnthropicVisionModel(api_key=api_key)
    if engine == "ollama":
        return OllamaVisionModel(base_url=ollama_base_url, num_gpu=ollama_num_gpu)
    if engine == "openai":  # any OpenAI-compatible provider (Gemini/Qwen/GLM/…)
        return OpenAICompatVisionModel(openai_base_url, openai_api_key, openai_in_rate, openai_out_rate)
    raise ValueError(f"unknown vision engine: {engine}")


def build_vision(settings) -> VisionModel:
    """Construct the configured engine from a Settings object (used by the
    worker/CLI)."""
    return get_vision_model(
        settings.vision_engine,
        settings.anthropic_api_key or None,
        settings.ollama_base_url,
        settings.ollama_num_gpu,
        settings.openai_base_url,
        settings.openai_api_key,
        settings.openai_input_usd_per_mtok,
        settings.openai_output_usd_per_mtok,
    )
