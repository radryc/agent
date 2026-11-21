FROM ghcr.io/ggml-org/llama.cpp:server

ENV MODEL_REPO=unsloth/gemma-4-E4B-it-GGUF
ENV MODEL_FILE=gemma-4-E4B-it-Q4_K_M.gguf
ENV MODEL_DIR=/models

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip curl && \
    pip3 install --break-system-packages huggingface-hub && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Run the inference server as an unprivileged user. The named /models volume is
# initialised with this ownership; bind mounts must be chowned by the operator.
RUN groupadd --gid 10001 llama \
    && useradd --uid 10001 --gid llama --create-home --shell /bin/bash llama \
    && mkdir -p /models \
    && chown -R llama:llama /models /home/llama

COPY --chown=llama:llama entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER llama

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
