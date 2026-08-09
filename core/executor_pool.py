import asyncio
import logging
import os

from core.paths import PROJECT_ROOT, WORKSPACE_ROOT


logger = logging.getLogger(__name__)


class ExecutorPool:
    def __init__(self, max_concurrency=1):
        self.sem = asyncio.Semaphore(max_concurrency)

    async def run(self, prompt, image_paths=None, *, task_manager=None, task_id="", allow_project_access=False):
        self._record_event(task_manager, task_id, "EXECUTION_QUEUED", "waiting for executor slot")
        async with self.sem:
            codex_bin = os.getenv("CODEX_BIN", "/usr/bin/codex")
            workdir = os.getenv("CODEX_WORKDIR", str(WORKSPACE_ROOT))
            # Four minutes keeps image/PPT generation viable while avoiding a long
            # silent wait when an inherited environment still sets a larger value.
            try:
                configured_timeout = int(os.getenv("CODEX_TIMEOUT", "240"))
            except ValueError:
                configured_timeout = 240
            timeout = max(1, min(configured_timeout, 240))

            args = [
                codex_bin,
                "exec",
                "--sandbox",
                "workspace-write",
                "--skip-git-repo-check",
                "--cd",
                workdir,
            ]

            if allow_project_access:
                args.extend(["--add-dir", str(PROJECT_ROOT)])

            for img in image_paths or []:
                args.extend(["--image", str(img)])

            # 关键修复：
            # 用 "-" 告诉 codex 从 stdin 读取 prompt
            args.append("-")

            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            logger.info("codex execution started task_id=%s timeout=%s", task_id or "-", timeout)
            self._record_event(task_manager, task_id, "EXECUTION_STARTED", f"codex started; timeout={timeout}s")

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=(prompt or "").encode("utf-8")),
                    timeout=timeout
                )

                out = stdout.decode("utf-8", errors="ignore").strip()

                if proc.returncode != 0:
                    # stderr may contain connection diagnostics, internal paths,
                    # or retry logs.  Keep those out of the DingTalk response.
                    logger.warning("codex execution failed returncode=%s", proc.returncode)
                    self._record_event(task_manager, task_id, "EXECUTION_FAILED", f"codex exited returncode={proc.returncode}")
                    return self._failure_message()

                logger.info("codex execution completed task_id=%s", task_id or "-")
                self._record_event(task_manager, task_id, "EXECUTION_COMPLETED", "codex completed")
                return out or "Codex 没有返回内容。"

            except asyncio.TimeoutError:
                await self._terminate_process(proc)
                logger.warning("codex execution timed out task_id=%s timeout=%s", task_id or "-", timeout)
                self._record_event(task_manager, task_id, "EXECUTION_TIMEOUT", f"codex timed out after {timeout}s")
                return f"Codex 执行超时，已终止。本次超时时间：{timeout} 秒。"
            except asyncio.CancelledError:
                await self._terminate_process(proc)
                logger.warning("codex execution cancelled task_id=%s", task_id or "-")
                self._record_event(task_manager, task_id, "EXECUTION_CANCELLED", "codex execution cancelled")
                raise

    @staticmethod
    async def _terminate_process(proc) -> None:
        if proc.returncode is None:
            proc.kill()
            try:
                await proc.wait()
            except ProcessLookupError:
                pass

    @staticmethod
    def _record_event(task_manager, task_id: str, event_type: str, message: str) -> None:
        if not task_manager or not task_id:
            return
        try:
            task_manager.record_event(task_id, event_type, message)
        except Exception:
            logger.exception("task event write failed task_id=%s event=%s", task_id, event_type)

    @staticmethod
    def _failure_message() -> str:
        """A user-safe error message that never exposes executor stderr."""
        return "Codex 服务连接暂时异常，本次请求已结束，请稍后重试。"

    @classmethod
    def is_failure_response(cls, answer: str) -> bool:
        return answer == cls._failure_message() or answer.startswith("Codex 执行超时")
