"""Restart recovery notifications for persisted maintenance tasks."""
from __future__ import annotations

import logging
from typing import Callable

from core.task_manager import COMPLETED, TaskManager


logger = logging.getLogger("codex-dingtalk-recovery")


class RecoveryManager:
    def __init__(self, task_manager: TaskManager, notifier: Callable[[dict, str], object]):
        self.task_manager = task_manager
        self.notifier = notifier

    def recover_waiting_restart(self) -> int:
        """Notify each active task interrupted by a service restart exactly once."""
        delivered = 0
        for task in self.task_manager.interrupted_by_restart():
            if task.get("task_type") == "self_maintenance":
                message = "重启已完成，之前的自维护任务已恢复。请发送“查看最近重启报告”查看重启与回滚结果。"
            else:
                message = "服务重启已完成，但上一条执行在重启时被中断，未能返回完整结果。请重新发送该请求。"
            try:
                self.notifier(task, message)
                self.task_manager.record_event(task["id"], "RESTART_INTERRUPTED", "service restarted before task completed")
                self.task_manager.transition(task["id"], COMPLETED, "restart recovery notification delivered", result_text=message)
                delivered += 1
            except Exception:
                # Keep WAITING_RESTART so the next successful service startup can retry.
                logger.exception("recovery notification failed task_id=%s", task["id"])
        return delivered
