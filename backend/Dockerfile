ARG MONOFS_CLIENT_IMAGE=monofs-client:latest
FROM ${MONOFS_CLIENT_IMAGE} AS monofs-client-stage

FROM ubuntu:24.04

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    bash-completion \
    build-essential \
    ca-certificates \
    curl \
    fd-find \
    fuse3 \
    git \
    golang-go \
    iproute2 \
    jq \
    less \
    locales \
    nano \
    ncurses-term \
    procps \
    python3 \
    python3-dev \
    python3-pip \
    python3-venv \
    ripgrep \
    sudo \
    unzip \
    vim \
    zip \
    && locale-gen en_US.UTF-8 \
    && update-locale LANG=en_US.UTF-8 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

ENV LANG=en_US.UTF-8
ENV PYTHONUNBUFFERED=1

RUN set -eux; \
        arch="$(dpkg --print-architecture)"; \
        case "$arch" in \
            amd64) bazelisk_arch="amd64" ;; \
            arm64) bazelisk_arch="arm64" ;; \
            *) echo "Unsupported architecture for Bazelisk: $arch"; exit 1 ;; \
        esac; \
        curl -fsSL "https://github.com/bazelbuild/bazelisk/releases/latest/download/bazelisk-linux-${bazelisk_arch}" -o /usr/local/bin/bazelisk; \
        chmod +x /usr/local/bin/bazelisk; \
        ln -sf /usr/local/bin/bazelisk /usr/local/bin/bazel

RUN curl -LsSf https://astral.sh/uv/install.sh | sh && ln -s /root/.local/bin/uv /usr/local/bin/uv

COPY requirements.txt .
RUN uv pip install --system --no-cache --break-system-packages -r requirements.txt

COPY --from=monofs-client-stage /usr/local/bin/monofs-client /usr/local/bin/monofs-client
COPY --from=monofs-client-stage /usr/local/bin/monofs-session /usr/local/bin/monofs-session
COPY guardianctl /usr/local/bin/guardianctl
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

COPY app/ ./app/

RUN mkdir -p /data /workspace

RUN mkdir -p /etc && \
    touch /etc/fuse.conf && \
    grep -qxF 'user_allow_other' /etc/fuse.conf || echo 'user_allow_other' >> /etc/fuse.conf && \
    ln -sf /proc/self/mounts /etc/mtab

RUN useradd -m -s /bin/bash developer && \
    usermod -aG sudo developer && \
    echo "developer ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/developer && \
    chmod 0440 /etc/sudoers.d/developer && \
    chown -R developer:developer /app /data /workspace

USER developer

EXPOSE 8090

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090"]
