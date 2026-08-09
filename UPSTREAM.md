# Upstream policy

- Runtime base: `agentscope-ai/QwenPaw`
- License: Apache-2.0
- Default image: `agentscope/qwenpaw:v2.1.0-beta.2`
- Codex runtime: `openai-codex==0.144.4`
- V1 target: QwenPaw 2.1.0 beta.2, the first official line with direct Codex
  third-party agent backend support

The repository intentionally keeps QwenPaw outside the Jarvis source tree and
extends its published image only to install the optional Codex runtime and
ripgrep. After the first successful Ubuntu deployment, record the resolved
Docker image digest here before production cutover.

Upgrades must follow this order: backup, pull candidate image, parallel validation, acceptance test, digest pinning, then cutover. Never use an untested `latest` image for the final production switch.
