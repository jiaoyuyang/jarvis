ARG QWENPAW_IMAGE=docker.io/agentscope/qwenpaw:v2.1.0-beta.2
FROM ${QWENPAW_IMAGE}

ARG OPENAI_CODEX_VERSION=0.144.4

# QwenPaw keeps third-party runtimes optional. Install the exact Codex
# version pinned by the selected QwenPaw release, plus ripgrep for the
# local knowledge-search skill. Keep this layer independent from Jarvis
# patches so later reliability fixes do not repeat large downloads.
RUN sed -i \
        's/qwenpaw app --host 0.0.0.0/qwenpaw app --host 127.0.0.1/' \
        /etc/supervisor/conf.d/supervisord.conf.template \
    && grep -q 'qwenpaw app --host 127.0.0.1' \
        /etc/supervisor/conf.d/supervisord.conf.template \
    && apt-get update \
    && apt-get install -y --no-install-recommends fonts-wqy-zenhei \
    && if ! command -v rg >/dev/null 2>&1; then \
         apt-get install -y --no-install-recommends ripgrep; \
       fi \
    && test -r /usr/share/fonts/truetype/wqy/wqy-zenhei.ttc \
    && rm -rf /var/lib/apt/lists/* \
    && /app/venv/bin/python -m pip install --no-cache-dir \
        "openai-codex==${OPENAI_CODEX_VERSION}"

COPY patches/patch_qwenpaw_codex_final_only.py /opt/jarvis/patches/patch_qwenpaw_codex_final_only.py
COPY patches/patch_qwenpaw_codex_turn_timeout.py /opt/jarvis/patches/patch_qwenpaw_codex_turn_timeout.py
COPY patches/patch_qwenpaw_stop_command.py /opt/jarvis/patches/patch_qwenpaw_stop_command.py
COPY patches/patch_qwenpaw_dingtalk_turn_recovery.py /opt/jarvis/patches/patch_qwenpaw_dingtalk_turn_recovery.py
COPY patches/patch_qwenpaw_local_artifact_delivery.py /opt/jarvis/patches/patch_qwenpaw_local_artifact_delivery.py

RUN /app/venv/bin/python \
        /opt/jarvis/patches/patch_qwenpaw_codex_final_only.py \
    && /app/venv/bin/python \
        /opt/jarvis/patches/patch_qwenpaw_codex_turn_timeout.py \
    && /app/venv/bin/python \
        /opt/jarvis/patches/patch_qwenpaw_stop_command.py \
    && /app/venv/bin/python \
        /opt/jarvis/patches/patch_qwenpaw_dingtalk_turn_recovery.py \
    && /app/venv/bin/python \
        /opt/jarvis/patches/patch_qwenpaw_local_artifact_delivery.py \
    && /app/venv/bin/python -c \
        "import py_compile; from qwenpaw.harnesses.codex import adapter; py_compile.compile(adapter.__file__, doraise=True)" \
    && /app/venv/bin/python -c \
        "import py_compile; from qwenpaw.app.channels import base; py_compile.compile(base.__file__, doraise=True)" \
    && /app/venv/bin/python -c \
        "import py_compile; from qwenpaw.app.channels import renderer; py_compile.compile(renderer.__file__, doraise=True)" \
    && /app/venv/bin/python -c \
        "import py_compile; from qwenpaw.app.channels.dingtalk import channel; py_compile.compile(channel.__file__, doraise=True)" \
    && /app/venv/bin/python -c \
        "from qwenpaw.harnesses.codex.discovery import resolve_codex_binary; assert resolve_codex_binary() is not None"

ENV CODEX_HOME=/root/.codex
