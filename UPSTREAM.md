# Upstream policy

- Runtime base: `agentscope-ai/QwenPaw`
- License: Apache-2.0
- Default image: `docker.io/agentscope/qwenpaw:v2.1.0-beta.2`
- Codex runtime: `openai-codex==0.144.4`
- V1 target: QwenPaw 2.1.0 beta.2, the first official line with direct Codex
  third-party agent backend support

The repository intentionally keeps QwenPaw outside the Jarvis source tree.
The derived image installs the optional Codex runtime and ripgrep. It also
applies one narrowly scoped, build-time compatibility patch to the pinned Codex
adapter: when `backend_settings.final_only=true`, all intermediate Codex
`agentMessage` items are buffered and only the last answer is delivered after
`turn/completed`. This prevents plans and progress commentary from leaking
through DingTalk while preserving reasoning and tool execution internally.

The patch is maintained in
`patches/patch_qwenpaw_codex_final_only.py`. Its source anchors are strict and
the image build compiles the patched module; an incompatible upstream change
must fail the build. Remove the patch when QwenPaw provides an equivalent
official final-only channel option.

Jarvis also applies
`patches/patch_qwenpaw_dingtalk_turn_recovery.py` to the pinned DingTalk
channel. QwenPaw's upstream AI Card recovery does not persist the incoming
message `Thinking` reaction and does not cover markdown-mode turns. The Jarvis
patch records only minimal delivery metadata, recalls stale processing state
after restart and sends a concise interruption notice without storing the
user's prompt or model output. Its anchors are strict and the patched channel
is compiled during the image build.

After the first successful Ubuntu deployment, record the resolved Docker image
digest here before production cutover.

Upgrades must follow this order: backup, pull candidate image, parallel validation, acceptance test, digest pinning, then cutover. Never use an untested `latest` image for the final production switch.
