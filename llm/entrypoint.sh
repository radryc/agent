#!/bin/bash
set -e

# ---------------------------------------------------------------------------
# Model selection
#
# MODEL_PRESET picks a curated preset (repo + file). MODEL_REPO/MODEL_FILE
# still win when you need something custom. Presets are mirrored in
# backend/app/llm/presets.py so the Settings UI can show the same list.
# ---------------------------------------------------------------------------
case "${MODEL_PRESET:-}" in
    gemma4-e4b|gemma4)
        MODEL_REPO="unsloth/gemma-4-E4B-it-GGUF"
        MODEL_FILE="gemma-4-E4B-it-Q4_K_M.gguf"
        ;;
    qwen3.5-9b|qwen35-9b)
        MODEL_REPO="unsloth/Qwen3.5-9B-GGUF"
        MODEL_FILE="Qwen3.5-9B-Q4_K_M.gguf"
        ;;
    qwen3.5-4b|qwen35-4b)
        MODEL_REPO="unsloth/Qwen3.5-4B-GGUF"
        MODEL_FILE="Qwen3.5-4B-Q4_K_M.gguf"
        ;;
    ""|custom)
        ;;
    *)
        echo "==> Unknown MODEL_PRESET '${MODEL_PRESET}' (use gemma4-e4b, qwen3.5-9b, qwen3.5-4b, or custom)" >&2
        exit 1
        ;;
esac

MODEL_REPO="${MODEL_REPO:-unsloth/gemma-4-E4B-it-GGUF}"
MODEL_FILE="${MODEL_FILE:-gemma-4-E4B-it-Q4_K_M.gguf}"
MODEL_DIR="${MODEL_DIR:-/models}"
MODEL_PATH="${MODEL_DIR}/${MODEL_FILE}"

if [ ! -f "$MODEL_PATH" ]; then
    echo "==> Downloading model ${MODEL_REPO} / ${MODEL_FILE} ..."
    python3 -c "
from huggingface_hub import hf_hub_download
hf_hub_download(
    repo_id='${MODEL_REPO}',
    filename='${MODEL_FILE}',
    local_dir='${MODEL_DIR}',
    local_dir_use_symlinks=False,
)
"
    echo "==> Model downloaded to ${MODEL_PATH}"
fi

echo "==> Starting llama-cpp server with ${MODEL_FILE} ..."

# Ensure runtime can locate llama shared libraries regardless of loader defaults.
export LD_LIBRARY_PATH="/app:/usr/local/lib:${LD_LIBRARY_PATH:-}"

# --jinja: use the model's own chat template (required for Qwen3.5 tool tokens).
# --reasoning-format deepseek: keep thinking out of `content` so the agent loop
# only sees the answer / tool calls. Set REASONING_FORMAT= empty to disable.
REASONING_ARGS=()
if [ -n "${REASONING_FORMAT-deepseek}" ]; then
    REASONING_ARGS=(--reasoning-format "${REASONING_FORMAT:-deepseek}")
fi

# Optional extra llama-server flags, e.g. EXTRA_ARGS="--no-mmap --cache-type-k q8_0"
EXTRA_ARGS_ARRAY=()
if [ -n "${EXTRA_ARGS:-}" ]; then
    read -r -a EXTRA_ARGS_ARRAY <<< "${EXTRA_ARGS}"
fi

exec /app/llama-server \
    --model "$MODEL_PATH" \
    --host 0.0.0.0 \
    --port 8080 \
    --ctx-size "${CTX_SIZE:-32768}" \
    --n-gpu-layers "${N_GPU_LAYERS:-99}" \
    --threads "${THREADS:-4}" \
    --parallel "${PARALLEL:-1}" \
    --jinja \
    "${REASONING_ARGS[@]}" \
    --cont-batching \
    --flash-attn auto \
    "${EXTRA_ARGS_ARRAY[@]}"
