ARG QWENPAW_IMAGE=agentscope/qwenpaw:v2.1.0-beta.2
FROM ${QWENPAW_IMAGE}

ARG OPENAI_CODEX_VERSION=0.144.4

# QwenPaw keeps third-party runtimes optional. Install the exact Codex
# version pinned by the selected QwenPaw release, plus ripgrep for the
# local knowledge-search skill.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && /app/venv/bin/python -m pip install --no-cache-dir \
        "openai-codex==${OPENAI_CODEX_VERSION}" \
    && /app/venv/bin/python -c \
        "from qwenpaw.harnesses.codex.discovery import resolve_codex_binary; assert resolve_codex_binary() is not None"

ENV CODEX_HOME=/root/.codex
