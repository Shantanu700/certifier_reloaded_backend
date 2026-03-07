FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ARG USER

RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin \
    libgdal-dev \
    curl \
    ca-certificates \
    git \
    zsh \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /backend

RUN useradd -ms /bin/bash $USER && \
    usermod -aG root $USER && \
    chown -R $USER:$USER /backend && \
    chmod -R 755 /backend

RUN chmod -R g+w /usr/local/

USER $USER

ENV PATH="/home/$USER/.local/bin/:$PATH" \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT="/usr/local/" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml .
COPY uv.lock .
RUN uv lock
RUN uv sync --locked

RUN sh -c "$(curl -L https://github.com/deluan/zsh-in-docker/releases/download/v1.2.1/zsh-in-docker.sh)" -- \
    -t robbyrussell \
    -p git \
    -p ssh-agent \
    -p 'history-substring-search' \
    -p https://github.com/zsh-users/zsh-autosuggestions \
    -p https://github.com/zsh-users/zsh-completions \
    -a 'bindkey "\$terminfo[kcuu1]" history-substring-search-up' \
    -a 'bindkey "\$terminfo[kcud1]" history-substring-search-down'
