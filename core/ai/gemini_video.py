"""Native Gemini video analysis: upload a whole clip via the File API, then ask
for a structured coaching report in ONE call. This is a distinct source path from
the frame pipeline — Gemini-specific, kept out of core dispatch (the pipeline
selects it via SourceType.VIDEO_NATIVE).

Stdlib-only HTTP (urllib) so no extra dependency. The native generativelanguage
endpoints authenticate with the `x-goog-api-key` header (NOT Bearer, which the
OpenAI-compat endpoint uses) — same Gemini key as the `openai` engine.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from dataclasses import dataclass
from typing import Any

# A model can occasionally emit an absurd integer literal (thousands of digits);
# Python 3.11 caps int<->str conversion and json.loads would raise. Lift the cap
# so parsing never crashes the whole analysis over one junk number.
try:
    sys.set_int_max_str_digits(1_000_000)
except Exception:  # noqa: BLE001 - older/newer runtimes without the knob
    pass

from core.ai.vision import _parse_json

_BASE = "https://generativelanguage.googleapis.com"


def _urlopen_retry(req, timeout: float, attempts: int = 5) -> bytes:
    """Read a request body, retrying on transient 429/5xx (Gemini returns 503 a lot
    on heavy video calls) and network errors with exponential backoff."""
    import urllib.error

    last = None
    for i in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (408, 409, 429, 500, 502, 503, 504) and i < attempts - 1:
                time.sleep(min(2 ** i, 8))
                continue
            raise
        except urllib.error.URLError as e:
            last = e
            if i < attempts - 1:
                time.sleep(min(2 ** i, 8))
                continue
            raise
    if last:
        raise last
    raise RuntimeError("request failed")


def _urlopen_retry_full(req, timeout: float, attempts: int = 5):
    """Like _urlopen_retry but returns (body_bytes, headers) — needed for the File
    API upload steps that read a response HEADER (the resumable upload URL)."""
    import urllib.error

    last = None
    for i in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (408, 409, 429, 500, 502, 503, 504) and i < attempts - 1:
                time.sleep(min(2 ** i, 8))
                continue
            raise
        except urllib.error.URLError as e:
            last = e
            if i < attempts - 1:
                time.sleep(min(2 ** i, 8))
                continue
            raise
    if last:
        raise last
    raise RuntimeError("request failed")

# Higher = the model sees the video in more detail (more tokens/sec, more $).
_MEDIA_RES = {
    "low": "MEDIA_RESOLUTION_LOW",       # ~66 tok/s  — cheap, coarse
    "medium": "MEDIA_RESOLUTION_MEDIUM",
    "high": "MEDIA_RESOLUTION_HIGH",     # most detail
    # "default" -> omit (model default)
}


@dataclass
class VideoResult:
    data: dict[str, Any]
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    cost_usd: float


class GeminiVideoModel:
    def __init__(self, api_key: str, in_usd_per_mtok: float = 0.30,
                 out_usd_per_mtok: float = 2.50, timeout: float = 600.0) -> None:
        self._key = api_key
        self.input_usd_per_mtok = in_usd_per_mtok
        self.output_usd_per_mtok = out_usd_per_mtok
        self._timeout = timeout

    # --- File API upload (resumable protocol) --------------------------------
    def _upload(self, data: bytes, mime: str, display_name: str) -> str:
        start = urllib.request.Request(
            f"{_BASE}/upload/v1beta/files",
            data=json.dumps({"file": {"display_name": display_name}}).encode(),
            headers={
                "x-goog-api-key": self._key,
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(len(data)),
                "X-Goog-Upload-Header-Content-Type": mime,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        _, headers = _urlopen_retry_full(start, self._timeout)
        upload_url = headers.get("X-Goog-Upload-URL") or headers.get("x-goog-upload-url")
        if not upload_url:
            raise RuntimeError("Gemini File API did not return an upload URL")

        put = urllib.request.Request(
            upload_url, data=data,
            headers={
                "x-goog-api-key": self._key,
                "Content-Length": str(len(data)),
                "X-Goog-Upload-Offset": "0",
                "X-Goog-Upload-Command": "upload, finalize",
            },
            method="POST",
        )
        body, _h = _urlopen_retry_full(put, self._timeout)
        info = json.loads(body.decode())
        return info["file"]["name"]  # e.g. "files/abc123"

    def _wait_active(self, name: str, tries: int = 60) -> str:
        """Files are PROCESSING right after upload; generateContent needs ACTIVE."""
        for _ in range(tries):
            req = urllib.request.Request(
                f"{_BASE}/v1beta/{name}",
                headers={"x-goog-api-key": self._key},
            )
            f = json.loads(_urlopen_retry(req, self._timeout).decode())
            state = f.get("state")
            if state == "ACTIVE":
                return f["uri"]
            if state == "FAILED":
                raise RuntimeError("Gemini failed to process the uploaded video")
            time.sleep(3)
        raise TimeoutError("Gemini video stayed in PROCESSING too long")

    # --- Upload once, read many ----------------------------------------------
    def prepare(self, video: bytes, mime: str = "video/mp4", on_step=None) -> str:
        """Upload the video ONCE and return its file_uri (reusable for N reads)."""
        step = on_step or (lambda *_: None)
        step("uploading", f"uploading {len(video) / 1e6:.0f} MB to Gemini")
        name = self._upload(video, mime, "coachio-match")
        step("processing", "Google is processing the video")
        return self._wait_active(name)

    def analyze_uri(self, uri: str, model: str, prompt: str, schema: dict | None = None,
                    media_resolution: str = "default", max_tokens: int = 3000,
                    mime: str = "video/mp4") -> VideoResult:
        """Run generateContent against an already-uploaded file (no re-upload)."""
        parts = [{"file_data": {"mime_type": mime, "file_uri": uri}}, {"text": prompt}]
        return self._generate(model, parts, schema, media_resolution, max_tokens)

    def analyze(self, model: str, prompt: str, video: bytes, mime: str = "video/mp4",
                schema: dict | None = None, media_resolution: str = "default",
                max_tokens: int = 3000, on_step=None) -> VideoResult:
        uri = self.prepare(video, mime, on_step)
        (on_step or (lambda *_: None))("generating", f"generating on {model}")
        return self.analyze_uri(uri, model, prompt, schema, media_resolution, max_tokens, mime)

    def generate_text(self, model: str, prompt: str, schema: dict | None = None,
                      max_tokens: int = 3000) -> VideoResult:
        """Text-only call (no video) — the 'reduce'/synthesis step on a stronger model."""
        return self._generate(model, [{"text": prompt}], schema, "default", max_tokens)

    def research(self, model: str, question: str, max_tokens: int = 1800) -> dict:
        """Answer a question WITH live Google Search grounding (self-learning).
        Returns {answer, sources[], cost_usd}."""
        payload = {
            "contents": [{"parts": [{"text": question}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0},
        }
        req = urllib.request.Request(
            f"{_BASE}/v1beta/models/{model}:generateContent",
            data=json.dumps(payload).encode(),
            headers={"x-goog-api-key": self._key, "Content-Type": "application/json"},
            method="POST",
        )
        out = json.loads(_urlopen_retry(req, self._timeout).decode())
        cand = (out.get("candidates") or [{}])[0]
        text = ""
        try:
            text = " ".join(p.get("text", "") for p in cand["content"]["parts"]).strip()
        except Exception:
            pass
        sources: list[str] = []
        for ch in (cand.get("groundingMetadata", {}) or {}).get("groundingChunks", []) or []:
            uri = (ch.get("web", {}) or {}).get("uri")
            if uri:
                sources.append(uri)
        um = out.get("usageMetadata", {})
        cost = (int(um.get("promptTokenCount", 0) or 0) * self.input_usd_per_mtok
                + int(um.get("candidatesTokenCount", 0) or 0) * self.output_usd_per_mtok) / 1_000_000.0
        return {"answer": text, "sources": sources[:4], "cost_usd": cost}

    def _generate(self, model: str, parts: list, schema: dict | None,
                  media_resolution: str, max_tokens: int) -> VideoResult:
        gen_cfg: dict[str, Any] = {"maxOutputTokens": max_tokens, "temperature": 0}
        if schema:
            gen_cfg["responseMimeType"] = "application/json"
            gen_cfg["responseSchema"] = _to_gemini_schema(schema)
        mr = _MEDIA_RES.get(media_resolution)
        if mr:
            gen_cfg["mediaResolution"] = mr

        payload = {"contents": [{"parts": parts}], "generationConfig": gen_cfg}
        req = urllib.request.Request(
            f"{_BASE}/v1beta/models/{model}:generateContent",
            data=json.dumps(payload).encode(),
            headers={"x-goog-api-key": self._key, "Content-Type": "application/json"},
            method="POST",
        )
        out = json.loads(_urlopen_retry(req, self._timeout).decode())

        text = ""
        try:
            text = out["candidates"][0]["content"]["parts"][0]["text"]
        except Exception:
            pass
        um = out.get("usageMetadata", {})
        pin = int(um.get("promptTokenCount", 0) or 0)
        pout = int(um.get("candidatesTokenCount", 0) or 0)
        cost = (pin * self.input_usd_per_mtok + pout * self.output_usd_per_mtok) / 1_000_000.0
        return VideoResult(
            data=_parse_json(text), text=text, input_tokens=pin, output_tokens=pout,
            model=out.get("modelVersion", model), cost_usd=cost,
        )


def _to_gemini_schema(schema: dict) -> dict:
    """Gemini's responseSchema uppercases types and drops JSON-Schema extras like
    additionalProperties. Convert our simple object/array/string/integer schema."""
    t = schema.get("type")
    if t == "object":
        props = {k: _to_gemini_schema(v) for k, v in schema.get("properties", {}).items()}
        out: dict[str, Any] = {"type": "OBJECT", "properties": props}
        if schema.get("required"):
            out["required"] = schema["required"]
        return out
    if t == "array":
        return {"type": "ARRAY", "items": _to_gemini_schema(schema.get("items", {"type": "string"}))}
    return {"type": str(t or "string").upper()}
