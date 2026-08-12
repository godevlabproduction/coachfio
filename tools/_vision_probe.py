"""Throwaway: send one real frame to the local Ollama VLM through the Stage 2
path and print the label it returns. Proves the fully-local vision path works.

    docker compose run --rm -v "<frames>:/frames:ro" api \
        python -m tools._vision_probe /frames/x.jpg gemma3:4b
"""
import json
import sys
from pathlib import Path

from adapters.base.registry import load_builtin_adapters, registry
from core.ai.vision import OllamaVisionModel
from core.config import get_settings

img = Path(sys.argv[1])
model = sys.argv[2] if len(sys.argv) > 2 else "gemma3:4b"

load_builtin_adapters()
s = get_settings()
ad = registry.get("ea-fc", "26")
vm = OllamaVisionModel(base_url=s.ollama_base_url, num_gpu=s.ollama_num_gpu)
res = vm.generate(
    model=model,
    prompt=ad.stage_prompt(2),
    images_jpeg=[img.read_bytes()],
    schema=ad.stage2_label_schema(),
    max_tokens=64,
)
print(f"[{img.name}] RAW: {res.text!r}")
print(f"[{img.name}] DATA: {json.dumps(res.data)}")
