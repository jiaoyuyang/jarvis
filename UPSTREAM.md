# Upstream policy

- Runtime base: `agentscope-ai/QwenPaw`
- License: Apache-2.0
- Default image: `agentscope/qwenpaw:latest`
- V1 validated target: QwenPaw 2.0.x stable line

The repository intentionally keeps QwenPaw outside the Jarvis source tree. After the first successful Ubuntu deployment, record the resolved Docker image digest here and pin `QWENPAW_IMAGE` in `.env` before production cutover.

Upgrades must follow this order: backup, pull candidate image, parallel validation, acceptance test, digest pinning, then cutover. Never use an untested `latest` image for the final production switch.

