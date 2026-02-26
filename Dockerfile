FROM docker.io/library/python:3.14-trixie AS production
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
LABEL org.opencontainers.image.source=https://github.com/frizzle-chan/frizzle-phone

ARG GIT_COMMIT=unknown

COPY --from=ghcr.io/astral-sh/uv:0.9.18 /uv /uvx /bin/

RUN groupadd --gid 1000 frizzle_phone \
 && useradd --uid 1000 --gid 1000 -m frizzle_phone --shell /bin/bash \
 && mkdir -p /app \
 && chown frizzle_phone:frizzle_phone /app

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends curl locales \
 && sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen \
 && locale-gen \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

USER frizzle_phone

WORKDIR /app

ENV LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8 \
    UV_NO_DEV=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_CACHE_DIR=/home/frizzle_phone/.cache/uv/ \
    PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    PATH=/app/.venv/bin:/home/frizzle_phone/.local/bin:$PATH

# Install dependencies
RUN --mount=type=cache,target=/home/frizzle_phone/.cache/uv,uid=1000,gid=1000 \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

COPY . .

RUN echo "${GIT_COMMIT}" > /app/.commit_sha

RUN --mount=type=cache,target=/home/frizzle_phone/.cache/uv,uid=1000,gid=1000 \
    uv sync --locked

CMD [ "python", "main.py" ]

FROM production AS devcontainer

ENV UV_NO_DEV=0 \
    UV_COMPILE_BYTECODE=0 \
    UV_NO_CACHE=0 \
    UV_LINK_MODE=copy \
    DISABLE_TELEMETRY=1

USER root

# install stuff
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       curl \
       git \
       jq \
       just \
       libpq5 \
       postgresql-client \
       procps \
       ripgrep \
       tmux \
       vim \
       zsh \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/* \
 && chsh -s /bin/zsh frizzle_phone

USER frizzle_phone
