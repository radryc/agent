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

# Reuse the Bazelisk binary built into the monofs-client image; downloading
# GitHub release assets is unreliable on this network (504s).
COPY --from=monofs-client-stage /usr/local/bin/bazelisk /usr/local/bin/bazelisk
RUN ln -sf /usr/local/bin/bazelisk /usr/local/bin/bazel

# Pin the uv installer version; never pipe an unversioned remote script.
ARG UV_VERSION=0.7.13
RUN curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" | sh \
    && ln -s /root/.local/bin/uv /usr/local/bin/uv

COPY requirements.txt .
RUN uv pip install --system --no-cache --break-system-packages -r requirements.txt

COPY --from=monofs-client-stage /usr/local/bin/monofs-client /usr/local/bin/monofs-client
COPY --from=monofs-client-stage /usr/local/bin/monofs-session /usr/local/bin/monofs-session
COPY guardianctl /usr/local/bin/guardianctl
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

COPY app/ ./app/

# FUSE mountpoint for the MonoFS workspace. It must exist and be owned by the
# non-root container user, otherwise monofs-client cannot mount into it.
RUN mkdir -p /data /workspace /monofs

RUN mkdir -p /etc && \
    touch /etc/fuse.conf && \
    grep -qxF 'user_allow_other' /etc/fuse.conf || echo 'user_allow_other' >> /etc/fuse.conf && \
    ln -sf /proc/self/mounts /etc/mtab

RUN useradd -m -s /bin/bash developer && \
    chown -R developer:developer /app /data /workspace /monofs

USER developer

EXPOSE 8090

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# app.server applies STRATA_TLS_* to uvicorn's ssl_* options. With TLS off
# (the default) this is equivalent to the previous uvicorn CLI command.
CMD ["python", "-m", "app.server", "--app", "app.main:app", "--host", "0.0.0.0", "--port", "8090"]
