"""Classify and normalize long-lived memory candidates from one user message.

Memory 2.0 keeps the detector deterministic and auditable.  It does not write
files.  ``analyze`` returns a structured result; ``detect`` remains as a
backwards-compatible candidate-only facade.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import hashlib
from pathlib import Path
import re
from typing import Any

from core.paths import MEMORY_ROOT

SCHEMA_VERSION = 2


class MemoryCandidate:
    """Rule-based P0 memory classifier with conservative write semantics."""

    CONFIRM_PATTERNS = (
        r"^(保存|记住|确认|写入)$",
        r"^(保存|记住|确认|写入)(这|此)?条(记忆)?[吧。！!]?$",
        r"^确认保存(这|此)?条(记忆)?[吧。！!]?$",
        r"^就按(这|此)个记住[吧。！!]?$",
        r"^按(这|此)个保存[吧。！!]?$",
    )
    CANCEL_PATTERNS = (
        r"^(不用|取消|不要保存)$",
        r"^(不用|取消|不要保存)(这|此)?条(记忆)?[吧。！!]?$",
        r"^(这|此)条取消[吧。！!]?$",
    )

    QUESTION_WORDS = (
        "什么", "怎么", "怎样", "为何", "为什么", "是否", "哪里", "哪些",
        "多少", "谁", "能不能", "可以吗", "方便吗", "优势吗",
    )
    QUESTION_PREFIXES = (
        "请问", "请教", "想问", "我想问", "能否", "可否", "告诉我",
        "帮我看看", "帮我查", "查一下", "解释一下", "介绍一下",
    )
    MEMORY_TRIGGERS = (
        "记住", "以后", "后续", "长期", "默认", "统一", "固定", "我的习惯",
        "我的要求", "请保持", "不要再", "以后都", "从现在开始",
    )
    TEMPORARY_MARKERS = (
        "这一次", "本次", "今天", "明天", "下午", "临时", "当前会话",
        "先帮我", "暂时", "仅本轮", "仅这次",
    )
    ATTRIBUTED_OPINION_MARKERS = (
        "认为", "建议", "觉得", "提出", "讨论认为", "尚未确认", "暂未采纳",
    )
    ADOPTION_MARKERS = (
        "已决定", "正式决定", "正式通过", "确认采用", "最终确定", "会议决议",
        "正式纪要确认", "已采纳", "决定采用", "责任人为", "负责人为",
    )
    INTAKE_MARKERS = (
        "发TXT", "发 TXT", "发txt", "发 txt", "上传TXT", "上传 TXT",
        "上传txt", "上传 txt", "发DOCX", "发 DOCX", "上传DOCX", "上传 DOCX",
        "发文件", "上传文件",
    )
    INTAKE_MEMORY_MARKERS = ("长期记忆", "存入记忆", "保存到记忆", "把重要内容记住", "提取记忆")

    GOVERNANCE_OBJECTS = (
        "钉钉机器人", "机器人", "助手", "自身程序", "bot.py", "core", "plugins",
        "tools", ".env", "systemd", "程序依赖", "依赖",
    )
    GOVERNANCE_CONSTRAINTS = (
        "仅拥有诊断和建议权限", "只能诊断", "只能分析", "只分析", "不得修改",
        "不能修改", "不得直接修改", "不得sudo", "不得 sudo", "不得重启",
        "不能重启", "不得停止", "只生成方案", "只能输出修改方案",
    )
    PROJECT_MARKERS = (
        "项目", "架构", "平台", "专项", "机器人", "Codex", "Jarvis", "钉钉助手",
        "用户增长", "数据治理", "业务", "团队",
    )
    STANDARD_MARKERS = ("制度规定", "制度要求", "正式制度", "规范要求", "工作标准", "统一标准")
    PREFERENCE_MARKERS = ("我喜欢", "我偏好", "我的习惯", "我的要求", "以后都", "默认", "请保持")
    OVERRIDE_MARKERS = ("以前", "作废", "以这条新规则为准", "以此为准", "不是以前", "改为", "纠正")

    PPT_WORDS = ("PPT", "汇报", "页面", "图片生成")
    OUTPUT_WORDS = ("输出", "沟通", "写作", "表达")

    PROJECT_RULES = (
        (("Codex", "Jarvis", "钉钉助手", "钉钉机器人"), "project_context", "项目知识", "projects/jarvis.md", "projects.jarvis"),
        (("用户增长平台",), "project_context", "项目知识", "projects/user_growth_platform.md", "projects.user_growth_platform"),
    )

    def __init__(self, memory_root: str | Path = MEMORY_ROOT):
        self.memory_root = Path(memory_root).resolve()

    @classmethod
    def is_confirmation(cls, text: str | None) -> bool:
        normalized = cls._compact_control_text(text)
        return any(re.fullmatch(pattern, normalized) for pattern in cls.CONFIRM_PATTERNS)

    @classmethod
    def is_cancellation(cls, text: str | None) -> bool:
        normalized = cls._compact_control_text(text)
        return any(re.fullmatch(pattern, normalized) for pattern in cls.CANCEL_PATTERNS)

    @staticmethod
    def _compact_control_text(text: str | None) -> str:
        return re.sub(r"\s+", "", (text or "").strip())

    @classmethod
    def is_question(cls, text: str | None) -> bool:
        normalized = (text or "").strip()
        if not normalized:
            return False
        if "?" in normalized or "？" in normalized:
            return True
        if normalized.startswith(cls.QUESTION_PREFIXES):
            return True
        return any(word in normalized for word in cls.QUESTION_WORDS)

    def analyze(self, user_message: str | None) -> dict[str, Any]:
        """Return a structured memory decision without mutating persistent state."""
        text = (user_message or "").strip()
        if not text:
            return self._ignored("non_memory", "空文本")
        if self.is_confirmation(text) or self.is_cancellation(text):
            return self._ignored("non_memory", "确认或取消控制语")
        if self.is_question(text):
            return self._ignored("non_memory", "普通咨询或问句")

        if self._is_temporary(text):
            return self._ignored("temporary_context", "包含一次性或短期时间范围")

        if self._is_attributed_opinion(text) and not self._has_adoption_signal(text):
            return self._ignored("attributed_opinion", "仅为他人观点，未形成正式采纳结果")

        if self._is_intake_request(text):
            return {
                "action": "intake",
                "memory_type": "memory_intake_request",
                "reason": "请求后续从文件摄取长期记忆，不保存当前指令文本",
                "source_text": text,
            }

        candidate: dict[str, Any] | None = None

        if self._is_assistant_policy(text):
            candidate = self._build_candidate(
                memory_type="assistant_policy",
                category="项目运行规则 / 机器人治理边界",
                namespace="assistant_preferences.system_change",
                target_file="projects/jarvis_operations.md",
                raw_content=text,
                normalized_content=self._normalize_assistant_policy(text),
                reason="识别为机器人自身权限与运行治理规则",
                confidence=0.99,
                subject_key="jarvis:self_program_change_boundary",
            )
        else:
            project_state = self._project_state_candidate(text)
            if project_state:
                candidate = project_state
            elif any(word in text for word in self.PPT_WORDS) and self._looks_stable(text):
                candidate = self._build_candidate(
                    memory_type="user_preference",
                    category="PPT规范",
                    namespace="user.preferences.ppt",
                    target_file="standards/ppt_rules.md",
                    raw_content=text,
                    normalized_content=self._normalize_ppt_preference(text),
                    reason="识别为长期稳定的 PPT 输出偏好",
                    confidence=0.96,
                    subject_key="user:ppt_style",
                )
            elif self._has_adoption_signal(text):
                candidate = self._build_candidate(
                    memory_type="historical_decision",
                    category="历史决策",
                    namespace="history.decisions",
                    target_file="history/decisions.md",
                    raw_content=text,
                    normalized_content=self._normalize_general(text),
                    reason="包含明确采纳或正式决策结果",
                    confidence=0.90,
                    subject_key=self._subject_from_text(text, "historical_decision"),
                )
            elif any(marker in text for marker in self.STANDARD_MARKERS):
                candidate = self._build_candidate(
                    memory_type="working_standard",
                    category="工作标准",
                    namespace="standards.working",
                    target_file="standards/output_style.md",
                    raw_content=text,
                    normalized_content=self._normalize_general(text),
                    reason="识别为正式制度、规范或工作标准",
                    confidence=0.84,
                    subject_key=self._subject_from_text(text, "working_standard"),
                )
            else:
                if candidate is None:
                    for keywords, memory_type, category, target, namespace in self.PROJECT_RULES:
                        if any(word in text for word in keywords) and self._looks_stable(text):
                            candidate = self._build_candidate(
                                memory_type=memory_type,
                                category=category,
                                namespace=namespace,
                                target_file=target,
                                raw_content=text,
                                normalized_content=self._normalize_general(text),
                                reason="识别为长期可复用的项目背景或工作规则",
                                confidence=0.82,
                                subject_key=self._subject_from_text(text, namespace),
                            )
                            break

                if candidate is None and any(word in text for word in self.PROJECT_MARKERS) and self._looks_stable(text):
                    candidate = self._build_candidate(
                        memory_type="project_context",
                        category="项目知识",
                        namespace="projects.general",
                        target_file="projects/current_projects.md",
                        raw_content=text,
                        normalized_content=self._normalize_general(text),
                        reason="识别为长期可复用的项目背景或工作规则",
                        confidence=0.80,
                        subject_key=self._subject_from_text(text, "projects.general"),
                    )

                if candidate is None and self._looks_like_user_preference(text):
                    target = "standards/output_style.md" if any(word in text for word in self.OUTPUT_WORDS) else "user/preferences.md"
                    namespace = "user.preferences.output" if target.startswith("standards/") else "user.preferences.general"
                    candidate = self._build_candidate(
                        memory_type="user_preference",
                        category="用户偏好",
                        namespace=namespace,
                        target_file=target,
                        raw_content=text,
                        normalized_content=self._normalize_general(text),
                        reason="识别为用户长期稳定偏好",
                        confidence=0.86,
                        subject_key=self._subject_from_text(text, namespace),
                    )

        if candidate is None:
            return self._ignored("non_memory", "没有足够证据表明该信息应长期保存")

        candidate = self._infer_persisted_operation(candidate)
        return {
            "action": "candidate" if candidate["operation"] != "duplicate" else "duplicate",
            "memory_type": candidate["memory_type"],
            "reason": candidate["reason"],
            "candidate": candidate,
        }

    def detect(self, user_message: str | None) -> dict[str, Any] | None:
        """Backwards-compatible facade returning only a non-duplicate candidate."""
        analysis = self.analyze(user_message)
        return analysis.get("candidate") if analysis.get("action") == "candidate" else None

    @staticmethod
    def _ignored(memory_type: str, reason: str) -> dict[str, Any]:
        return {"action": "ignore", "memory_type": memory_type, "reason": reason}

    def _is_temporary(self, text: str) -> bool:
        if not any(marker in text for marker in self.TEMPORARY_MARKERS):
            return False
        lasting_override = any(marker in text for marker in ("从现在开始", "以后", "长期", "默认", "后续都"))
        explicit_return = any(marker in text for marker in ("之后仍按原", "不改变长期规则", "仅本次", "仅这次"))
        return explicit_return or not lasting_override

    def _is_attributed_opinion(self, text: str) -> bool:
        speaker_pattern = re.search(r"(?:会议中)?[\u4e00-\u9fff]{2,4}(?:认为|建议|觉得|提出)", text)
        return bool(speaker_pattern or any(marker in text for marker in self.ATTRIBUTED_OPINION_MARKERS))

    def _has_adoption_signal(self, text: str) -> bool:
        return any(marker in text for marker in self.ADOPTION_MARKERS)

    def _is_intake_request(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", text)
        file_signal = any(re.sub(r"\s+", "", marker) in compact for marker in self.INTAKE_MARKERS)
        memory_signal = any(re.sub(r"\s+", "", marker) in compact for marker in self.INTAKE_MEMORY_MARKERS)
        return file_signal and memory_signal

    def _is_assistant_policy(self, text: str) -> bool:
        object_hits = sum(1 for marker in self.GOVERNANCE_OBJECTS if marker in text)
        constraint_hits = sum(1 for marker in self.GOVERNANCE_CONSTRAINTS if marker in text)
        return object_hits >= 1 and constraint_hits >= 1

    def _looks_stable(self, text: str) -> bool:
        return any(marker in text for marker in self.MEMORY_TRIGGERS) or any(marker in text for marker in self.PREFERENCE_MARKERS)

    def _looks_like_user_preference(self, text: str) -> bool:
        return any(marker in text for marker in self.PREFERENCE_MARKERS) or text.startswith(("我喜欢", "我偏好", "我的习惯", "我的要求"))

    def _project_state_candidate(self, text: str) -> dict[str, Any] | None:
        patterns = (
            r"(?P<name>[\u4e00-\u9fff]{2,4})现在是(?:我们)?(?:企业架构)?团队负责人",
            r"(?:企业架构)?团队(?:当前)?负责人(?:是|为)(?P<name>[\u4e00-\u9fff]{2,4})",
            r"当前领导(?:是|为)?(?P<name>[\u4e00-\u9fff]{2,4})",
        )
        name = ""
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                name = match.group("name")
                break
        if not name:
            return None
        return self._build_candidate(
            memory_type="project_state",
            category="项目当前状态",
            namespace="projects.current.team_leader",
            target_file="projects/current_projects.md",
            raw_content=text,
            normalized_content=f"当前项目团队负责人为{name}。",
            reason="识别为对当前项目负责人状态的明确更新",
            confidence=0.96,
            subject_key="current_project_team:leader",
            force_replace=any(marker in text for marker in self.OVERRIDE_MARKERS) or "现在" in text or "当前" in text,
        )

    @staticmethod
    def _normalize_assistant_policy(_text: str) -> str:
        return (
            "Jarvis 对自身程序仅拥有诊断和建议权限。涉及 bot.py、core、plugins、tools、.env、"
            "systemd 或程序依赖的变更时，只能输出修改方案，并可将方案写入 data/change_requests；"
            "不得直接修改代码、调用 sudo、停止或重启服务。实际变更由用户在服务器中手工执行经过"
            "校验和可回滚的修复脚本。长期记忆、业务文件和正常输出仍按原流程处理。"
        )

    @staticmethod
    def _normalize_ppt_preference(text: str) -> str:
        return MemoryCandidate._normalize_general(text)

    @staticmethod
    def _normalize_general(text: str) -> str:
        cleaned = text.strip()
        cleaned = re.sub(r"^(从现在开始|以后|后续|长期|默认|请记住|记住|请保持)\s*[，,:：]?\s*", "", cleaned)
        cleaned = re.sub(r"(?m)^\s*\d+[\.、）)]\s*", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = cleaned.replace(" ；", "；").replace(" 。", "。")
        if cleaned and cleaned[-1] not in "。！？!?":
            cleaned += "。"
        return cleaned

    def _build_candidate(
        self,
        *,
        memory_type: str,
        category: str,
        namespace: str,
        target_file: str,
        raw_content: str,
        normalized_content: str,
        reason: str,
        confidence: float,
        subject_key: str,
        force_replace: bool = False,
    ) -> dict[str, Any]:
        target = (self.memory_root / target_file).resolve()
        if self.memory_root not in target.parents:
            raise ValueError("memory candidate target is outside memory root")
        canonical = self.canonicalize(normalized_content)
        dedupe_key = hashlib.sha256(f"{namespace}|{subject_key}|{canonical}".encode("utf-8")).hexdigest()
        return {
            "schema_version": SCHEMA_VERSION,
            "memory_type": memory_type,
            "category": category,
            "namespace": namespace,
            "target_file": target_file,
            "raw_content": raw_content,
            "normalized_content": normalized_content,
            "content": normalized_content,
            "operation": "replace" if force_replace else "create",
            "reason": reason,
            "existing_memory_ref": None,
            "existing_memory_excerpt": None,
            "existing_memory_hash": None,
            "supersedes": [],
            "source_type": "explicit_user_instruction",
            "confidence": confidence,
            "dedupe_key": dedupe_key,
            "subject_key": subject_key,
        }

    def _infer_persisted_operation(self, candidate: dict[str, Any]) -> dict[str, Any]:
        target = (self.memory_root / candidate["target_file"]).resolve()
        if self.memory_root not in target.parents or not target.is_file():
            return candidate
        try:
            file_text = target.read_text(encoding="utf-8")
        except OSError:
            return candidate

        normalized = candidate["normalized_content"]
        canonical = self.canonicalize(normalized)
        if canonical and canonical in self.canonicalize(file_text):
            candidate["operation"] = "duplicate"
            candidate["existing_memory_ref"] = candidate["target_file"]
            candidate["reason"] += "；目标文件已存在语义相同内容"
            return candidate

        excerpts = self._candidate_excerpts(file_text)
        matched: list[tuple[float, str]] = []
        for excerpt in excerpts:
            score = SequenceMatcher(None, canonical, self.canonicalize(excerpt)).ratio()
            if score >= 0.58:
                matched.append((score, excerpt))
        matched.sort(key=lambda item: item[0], reverse=True)

        if candidate["memory_type"] == "project_state":
            state_matches = [
                excerpt for excerpt in excerpts
                if ("负责人" in excerpt or "当前领导" in excerpt)
                and ("企业架构" in excerpt or "团队" in excerpt)
            ]
            if len(state_matches) == 1:
                return self._attach_existing(candidate, "replace", state_matches[0])
            return candidate

        if matched and matched[0][0] >= 0.92:
            return self._attach_existing(candidate, "duplicate", matched[0][1])

        if candidate["operation"] == "replace" and len(matched) == 1:
            return self._attach_existing(candidate, "replace", matched[0][1])

        if candidate["memory_type"] in {"assistant_policy", "user_preference"} and matched:
            score, excerpt = matched[0]
            if score >= 0.72:
                existing_tokens = set(self._tokens(excerpt))
                new_tokens = set(self._tokens(normalized))
                if new_tokens.issubset(existing_tokens):
                    return self._attach_existing(candidate, "duplicate", excerpt)
                if existing_tokens.issubset(new_tokens) or len(new_tokens - existing_tokens) <= 10:
                    candidate["normalized_content"] = self._merge_text(excerpt, normalized)
                    candidate["content"] = candidate["normalized_content"]
                    return self._attach_existing(candidate, "merge", excerpt)

        return candidate

    def _attach_existing(self, candidate: dict[str, Any], operation: str, excerpt: str) -> dict[str, Any]:
        candidate["operation"] = operation
        candidate["existing_memory_ref"] = candidate["target_file"]
        candidate["existing_memory_excerpt"] = excerpt
        candidate["existing_memory_hash"] = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
        candidate["reason"] += f"；识别为对既有记忆的{operation}操作"
        return candidate

    @staticmethod
    def _candidate_excerpts(text: str) -> list[str]:
        blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
        return [block for block in blocks if not block.startswith("#") and not block.startswith("<!--")]

    @staticmethod
    def _merge_text(existing: str, new: str) -> str:
        sentences: list[str] = []
        seen: set[str] = set()
        for source in (existing, new):
            for sentence in re.split(r"(?<=[。！？!?；;])", source):
                sentence = sentence.strip()
                key = MemoryCandidate.canonicalize(sentence)
                if sentence and key and key not in seen:
                    seen.add(key)
                    sentences.append(sentence)
        return "".join(sentences)

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return re.findall(r"[A-Za-z0-9_.-]+|[\u4e00-\u9fff]{1,4}", text.lower())

    @staticmethod
    def canonicalize(text: str | None) -> str:
        normalized = (text or "").lower()
        normalized = re.sub(r"<!--.*?-->", "", normalized, flags=re.S)
        normalized = re.sub(r"[\s\u3000]+", "", normalized)
        normalized = re.sub(r"[，。；：、！？,.!?:;\-—_`'\"“”‘’（）()\[\]{}<>]", "", normalized)
        normalized = re.sub(r"\d+[\.、）)]", "", normalized)
        return normalized

    @staticmethod
    def _subject_from_text(text: str, prefix: str) -> str:
        compact = MemoryCandidate.canonicalize(text)[:80]
        digest = hashlib.sha256(compact.encode("utf-8")).hexdigest()[:16]
        return f"{prefix}:{digest}"
