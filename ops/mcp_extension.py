"""Register bounded operations on the existing v1 FastMCP or v2 MCPServer."""
from __future__ import annotations

import json
import os
import subprocess
from typing import Literal
from mcp.types import ToolAnnotations

HELPER = "/usr/local/sbin/jarvis-network-ops"
VERSION = "1.2.0"


def hints(read_only=False, destructive=False, idempotent=False, open_world=False):
    # v1 names model fields in camelCase; v2 uses snake_case. Do not rely on
    # aliases being accepted: ignored keywords would silently discard hints.
    fields = ToolAnnotations.model_fields
    values = {"read_only_hint": read_only, "destructive_hint": destructive,
              "idempotent_hint": idempotent, "open_world_hint": open_world}
    aliases = {"read_only_hint": "readOnlyHint", "destructive_hint": "destructiveHint",
               "idempotent_hint": "idempotentHint", "open_world_hint": "openWorldHint"}
    return ToolAnnotations(**{key if key in fields else aliases[key]: value
                              for key, value in values.items()})


def invoke(action, **arguments):
    argv = [HELPER, action]
    if os.geteuid() != 0:
        argv = ["sudo", "-n", *argv]
    timeout = 110 if action in ("model-smoke", "upgrade") else 90
    try:
        result = subprocess.run(argv, input=json.dumps(arguments), capture_output=True,
                                text=True, timeout=timeout)
        data = json.loads(result.stdout)
        if result.returncode or not data.get("ok"):
            return {"ok": False, "error": data.get("error", "helper_failed")}
        return data
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "helper_timeout_check_change_status_before_retry"}
    except Exception:
        return {"ok": False, "error": "helper_unavailable"}


def register_tools(mcp):
    @mcp.tool(annotations=hints(read_only=True, idempotent=True))
    def ops_extension_status() -> dict:
        """Report this extension's version; does not prove network or model health."""
        return invoke("extension-status")

    @mcp.tool(annotations=hints(read_only=True, idempotent=True))
    def network_snapshot() -> dict:
        """Read sanitized groups, DNS configuration, WG host routes and pending changes."""
        return invoke("snapshot")

    @mcp.tool(annotations=hints(read_only=True, idempotent=True, open_world=True))
    def probe_proxy_nodes(provider: Literal["primary", "backup"] = "primary",
                          offset: int = 0, limit: int = 4, timeout_ms: int = 4000) -> dict:
        """Freshly probe up to 6 provider leaves, concurrency 2, without selecting them.

        primary is 梯子猫订阅; backup is 一元机场. Tests gstatic 204 and OpenAI 401.
        Return receipts, not model-generation proof. Follow next_offset for more nodes.
        """
        return invoke("probe", provider=provider, offset=offset, limit=limit, timeout_ms=timeout_ms)

    @mcp.tool(annotations=hints(open_world=True))
    def prepare_network_change(profile: Literal["switch_primary", "switch_backup", "stabilize_dns", "persist_wireguard"],
                               primary_receipt: str = "", backup_receipt: str = "") -> dict:
        """Prepare a reviewable plan, no live traffic change.

        switch_primary needs successful primary+backup receipts <=180s old; keeps direct
        backup in both fallback groups. switch_backup needs a fresh successful backup
        receipt and prefers it without requiring a healthy primary. stabilize_dns preserves bootstrap and restores
        the configured provider's allowlisted node DNS via backup when applicable.
        persist_wireguard only mirrors existing public host routes.
        """
        return invoke("prepare", profile=profile, primary_receipt=primary_receipt,
                      backup_receipt=backup_receipt)

    @mcp.tool(annotations=hints(destructive=True))
    def apply_network_change(change_id: str) -> dict:
        """Apply an authorized prepared plan after activating an independent 120s rollback.

        Requires authorized scope. Verify and confirm within the deadline or it restores.
        Does not restart Jarvis/WireGuard or accept arbitrary shell, URLs, YAML or paths.
        """
        return invoke("apply", change_id=change_id)

    @mcp.tool(annotations=hints(open_world=True))
    def confirm_network_change(change_id: str) -> dict:
        """Verify the pending change before cancelling rollback; cannot force success.

        Transport/DNS tests do not prove model generation. If primary_not_selected_yet,
        allow fallback health checks to finish and retry before the unchanged deadline.
        """
        return invoke("confirm", change_id=change_id)

    @mcp.tool(annotations=hints(read_only=True, idempotent=True))
    def network_change_status(change_id: str) -> dict:
        """Read an exact transaction's state; use after a timeout before retrying a write."""
        return invoke("status", change_id=change_id)

    @mcp.tool(annotations=hints(destructive=True, idempotent=True))
    def rollback_network_change(change_id: str) -> dict:
        """Restore a pending transaction, idempotently; committed changes are not overwritten."""
        return invoke("rollback", change_id=change_id)

    @mcp.tool(annotations=hints(read_only=True, idempotent=True))
    def read_application_logs(lines: int = 80) -> dict:
        """Read up to 160 recent matching application log lines, with credentials redacted.

        May contain diagnostic message text. Reads app.err.log/app.out.log inside Jarvis,
        not only Docker startup or Mihomo network-connection logs.
        """
        return invoke("logs", lines=lines)

    @mcp.tool(annotations=hints(open_world=True))
    def model_smoke_test() -> dict:
        """Bounded gpt-6.1-sol/high test with existing ChatGPT OAuth and fixed prompt.

        Read-only agent sandbox, ephemeral independent thread, 90s inner timeout; no API key.
        Returns exit code and completed final output. Does not reuse a DingTalk conversation.
        """
        return invoke("model-smoke")

    @mcp.tool(annotations=hints(destructive=True, open_world=True))
    def upgrade_ops_extension(commit_sha: str, package_sha256: str) -> dict:
        """Install an authorized reviewed Ops release from jiaoyuyang/jarvis origin/main.

        Accepts only exact commit and package hashes, verifies every fixed package file,
        preserves existing Ops tools, and schedules protected Ops reload. No arbitrary URLs.
        Check new version/tool discovery after reconnect; this does not deploy Jarvis.
        """
        return invoke("upgrade", commit_sha=commit_sha, package_sha256=package_sha256)
