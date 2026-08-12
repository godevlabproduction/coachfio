"""Vision-model access for Stages 2 & 3. Game-agnostic: the pipeline calls a
VisionModel with an adapter-supplied prompt + frames and gets structured JSON
back. Cost is computed from token usage and charged against the match budget."""
from core.ai.pricing import actual_cost_usd, estimate_cost_usd, image_tokens
from core.ai.vision import VisionModel, VisionResult, get_vision_model

__all__ = [
    "VisionModel",
    "VisionResult",
    "get_vision_model",
    "actual_cost_usd",
    "estimate_cost_usd",
    "image_tokens",
]
