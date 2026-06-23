#!/bin/bash
set -e

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

exec /app/llama-server \
    --model "$MODEL_PATH" \
    --host 0.0.0.0 \
    --port 8080 \
    --ctx-size "${CTX_SIZE:-8192}" \
    --n-gpu-layers "${N_GPU_LAYERS:-99}" \
    --threads "${THREADS:-4}" \
    --parallel "${PARALLEL:-1}" \
    --cont-batching \
    --flash-attn auto
