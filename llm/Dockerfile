FROM ghcr.io/ggml-org/llama.cpp:server

ENV MODEL_REPO=unsloth/gemma-4-E4B-it-GGUF
ENV MODEL_FILE=gemma-4-E4B-it-Q4_K_M.gguf
ENV MODEL_DIR=/models

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip curl && \
    pip3 install --break-system-packages huggingface-hub && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
