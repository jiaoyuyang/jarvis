#!/usr/bin/env python3
"""Add a bounded wall-clock timeout to QwenPaw Codex turns.

The pinned QwenPaw Codex adapter waits indefinitely for ``turn/completed``.
If a tool call or resumed Codex thread stalls, TaskTracker remains busy and
all later messages in that chat are ignored.  This patch bounds the whole
turn, interrupts Codex on expiry, and lets the normal channel error path clear
the card, reaction, dedup state, and TaskTracker entry.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


MARKER = "# JARVIS_CODEX_TURN_TIMEOUT_PATCH_V1"

STATE_ANCHOR = """        buffered_agent_order: list[str] = []
        try:
"""

STATE_REPLACEMENT = f"""        buffered_agent_order: list[str] = []
        {MARKER}
        configured_timeout = settings.get("turn_timeout_seconds", 600)
        try:
            turn_timeout_seconds = max(30.0, float(configured_timeout))
        except (TypeError, ValueError):
            turn_timeout_seconds = 600.0
        turn_deadline = (
            asyncio.get_running_loop().time() + turn_timeout_seconds
        )
        try:
"""

WAIT_ANCHOR = """            turn_id = str((result or {}).get("turn", {}).get("id", ""))
            while True:
                message = await queue.get()
"""

WAIT_REPLACEMENT = """            turn_id = str((result or {}).get("turn", {}).get("id", ""))
            while True:
                remaining = turn_deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                message = await asyncio.wait_for(
                    queue.get(),
                    timeout=remaining,
                )
"""

ERROR_ANCHOR = """        except asyncio.CancelledError:
            if turn_id:
                await self._interrupt_turn(client, thread_id, turn_id)
            raise
"""

ERROR_REPLACEMENT = """        except asyncio.TimeoutError as exc:
            if turn_id:
                await self._interrupt_turn(client, thread_id, turn_id)
            raise CodexAppServerError(
                "Codex turn timed out after "
                f"{int(turn_timeout_seconds)} seconds; the task was "
                "interrupted. Please retry."
            ) from exc
        except asyncio.CancelledError:
            if turn_id:
                await self._interrupt_turn(client, thread_id, turn_id)
            raise
"""

REPLACEMENTS = (
    (STATE_ANCHOR, STATE_REPLACEMENT),
    (WAIT_ANCHOR, WAIT_REPLACEMENT),
    (ERROR_ANCHOR, ERROR_REPLACEMENT),
)


def resolve_adapter_path() -> Path:
    spec = importlib.util.find_spec("qwenpaw.harnesses.codex.adapter")
    if spec is None or not spec.origin:
        raise SystemExit("QwenPaw Codex adapter was not found")
    return Path(spec.origin)


def patch(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        print(f"Jarvis Codex timeout patch already present: {path}")
        return

    for anchor, _replacement in REPLACEMENTS:
        if source.count(anchor) != 1:
            raise SystemExit(
                "QwenPaw Codex timeout anchor did not match exactly once: "
                + anchor.splitlines()[0]
            )
    for anchor, replacement in REPLACEMENTS:
        source = source.replace(anchor, replacement)
    path.write_text(source, encoding="utf-8")
    print(f"Applied Jarvis Codex turn timeout patch: {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--path",
        type=Path,
        help="Adapter path for tests; defaults to installed QwenPaw",
    )
    args = parser.parse_args()
    patch(args.path or resolve_adapter_path())


if __name__ == "__main__":
    main()
