"""Curated local model presets for the llama.cpp server.

The model *weights* are selected by the LLM container (MODEL_PRESET /
MODEL_REPO / MODEL_FILE), not by the backend: the backend only chooses which
model id to send in the OpenAI-compatible request. The presets below keep both
halves in sync and are surfaced in the Settings UI.

Notes:
- Qwen3.5 9B is the recommended upgrade for agent/tool loops on a single 12 GB
  GPU: native tool tokens, long context, Apache-2.0.
- Gemma 4 E4B stays the default for multimodal/edge use and Apache-2.0.
- LFM3-1.7B is intentionally absent: weaker agentic task pass rate and the
  LFM Open License v1.0 caps commercial use at $10M annual revenue.
"""

from __future__ import annotations

LLM_MODEL_PRESETS: tuple[dict, ...] = (
    {
        "id": "gemma4-e4b",
        "label": "Gemma 4 E4B — default, multimodal, Apache-2.0",
        "model": "gemma-4-E4B-it",
        "repo": "unsloth/gemma-4-E4B-it-GGUF",
        "file": "gemma-4-E4B-it-Q4_K_M.gguf",
        "license": "Apache-2.0",
    },
    {
        "id": "qwen3.5-9b",
        "label": "Qwen3.5 9B — recommended for agent/tool loops, Apache-2.0",
        "model": "qwen3.5-9b",
        "repo": "unsloth/Qwen3.5-9B-GGUF",
        "file": "Qwen3.5-9B-Q4_K_M.gguf",
        "license": "Apache-2.0",
    },
    {
        "id": "qwen3.5-4b",
        "label": "Qwen3.5 4B — lower latency, Apache-2.0",
        "model": "qwen3.5-4b",
        "repo": "unsloth/Qwen3.5-4B-GGUF",
        "file": "Qwen3.5-4B-Q4_K_M.gguf",
        "license": "Apache-2.0",
    },
)


def get_preset(preset_id: str) -> dict | None:
    wanted = str(preset_id or "").strip().lower()
    if not wanted:
        return None
    for preset in LLM_MODEL_PRESETS:
        if preset["id"] == wanted:
            return dict(preset)
    return None


def preset_for_model(model: str) -> dict | None:
    wanted = str(model or "").strip().lower()
    if not wanted:
        return None
    for preset in LLM_MODEL_PRESETS:
        if preset["model"].lower() == wanted:
            return dict(preset)
    return None
