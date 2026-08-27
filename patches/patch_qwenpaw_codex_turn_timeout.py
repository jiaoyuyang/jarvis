#!/usr/bin/env python3
"""Bound QwenPaw Codex turns and discard interrupted thread mappings.

The pinned QwenPaw Codex adapter can wait indefinitely while preparing a
runtime, resuming a thread, starting a turn, or waiting for
``turn/completed``.  If a tool call or resumed thread stalls, TaskTracker
remains busy and later messages in that chat are ignored.  This patch applies
one wall-clock deadline to every blocking stage.  A timeout or user
cancellation interrupts the active turn and discards the session-to-thread
mapping so the next message starts a clean Codex thread.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


MARKER = "# JARVIS_CODEX_TURN_TIMEOUT_PATCH_V2"
LEGACY_MARKER = "# JARVIS_CODEX_TURN_TIMEOUT_PATCH_V1"

START_ANCHOR = '''        """Start or resume a Codex thread and stream one turn."""
        client = await self._prepare_runtime(session_id, settings)
        thread_id = await self._thread_for_session(
            client,
            session_id,
            cwd,
            settings,
        )
'''

START_REPLACEMENT = f'''        """Start or resume a Codex thread and stream one turn."""
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
            client = await asyncio.wait_for(
                self._prepare_runtime(session_id, settings),
                timeout=max(
                    0.001,
                    turn_deadline - asyncio.get_running_loop().time(),
                ),
            )
            thread_id = await asyncio.wait_for(
                self._thread_for_session(
                    client,
                    session_id,
                    cwd,
                    settings,
                ),
                timeout=max(
                    0.001,
                    turn_deadline - asyncio.get_running_loop().time(),
                ),
            )
        except asyncio.TimeoutError as exc:
            await self.reset_session(session_id)
            raise CodexAppServerError(
                "Codex turn timed out after "
                f"{{int(turn_timeout_seconds)}} seconds while preparing; "
                "the thread was reset. Please retry."
            ) from exc
'''

STATE_ANCHOR = """        buffered_agent_order: list[str] = []
        buffered_artifact_links: list[str] = []
        try:
"""

STATE_REPLACEMENT = """        buffered_agent_order: list[str] = []
        buffered_artifact_links: list[str] = []
        try:
"""

LEGACY_STATE_ANCHOR = f"""        buffered_agent_order: list[str] = []
        {LEGACY_MARKER}
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

TURN_START_ANCHOR = '''            result = await client.request("turn/start", params)
'''

TURN_START_REPLACEMENT = '''            remaining = (
                turn_deadline - asyncio.get_running_loop().time()
            )
            if remaining <= 0:
                raise asyncio.TimeoutError
            result = await asyncio.wait_for(
                client.request("turn/start", params),
                timeout=remaining,
            )
'''

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
                try:
                    await asyncio.wait_for(
                        self._interrupt_turn(client, thread_id, turn_id),
                        timeout=5.0,
                    )
                except asyncio.TimeoutError:
                    pass
            await self.reset_session(session_id)
            raise CodexAppServerError(
                "Codex turn timed out after "
                f"{int(turn_timeout_seconds)} seconds; the task was "
                "interrupted and its thread was reset. Please retry."
            ) from exc
        except asyncio.CancelledError:
            if turn_id:
                try:
                    await asyncio.wait_for(
                        self._interrupt_turn(client, thread_id, turn_id),
                        timeout=5.0,
                    )
                except asyncio.TimeoutError:
                    pass
            await self.reset_session(session_id)
            raise
"""

LEGACY_ERROR_ANCHOR = """        except asyncio.TimeoutError as exc:
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
    (START_ANCHOR, START_REPLACEMENT),
    (STATE_ANCHOR, STATE_REPLACEMENT),
    (TURN_START_ANCHOR, TURN_START_REPLACEMENT),
    (WAIT_ANCHOR, WAIT_REPLACEMENT),
    (ERROR_ANCHOR, ERROR_REPLACEMENT),
)

LEGACY_REPLACEMENTS = (
    (START_ANCHOR, START_REPLACEMENT),
    (LEGACY_STATE_ANCHOR, STATE_REPLACEMENT),
    (TURN_START_ANCHOR, TURN_START_REPLACEMENT),
    (LEGACY_ERROR_ANCHOR, ERROR_REPLACEMENT),
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

    replacements = (
        LEGACY_REPLACEMENTS if LEGACY_MARKER in source else REPLACEMENTS
    )

    for anchor, _replacement in replacements:
        if source.count(anchor) != 1:
            raise SystemExit(
                "QwenPaw Codex timeout anchor did not match exactly once: "
                + anchor.splitlines()[0]
            )
    for anchor, replacement in replacements:
        source = source.replace(anchor, replacement)
    path.write_text(source, encoding="utf-8")
    action = "Upgraded" if replacements is LEGACY_REPLACEMENTS else "Applied"
    print(f"{action} Jarvis Codex turn timeout patch: {path}")


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
