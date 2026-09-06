"""
邮差系统 —— 信件内容检查器

铁律之三【不指令】：信件内容只允许生活与情感内容，
禁止包含命令式内容。含"去做/命令/必须给我/我要你/给我去/立刻/执行/
按照我说/听从/服从"等指令模式的信件拒收并记录。

城外信额外执行【世界观保护】：拦截破墙词
（用户/管理员/造物主/玩家/开发者/系统/程序/NPC 等），
防止外部信件打破第四面墙。

检查策略：
  - 基于中文指令模式的子串/正则匹配
  - 只拦截明显的命令式表达，避免误伤日常用语
  - 检查结果记录日志，拒收信件留档
"""

from __future__ import annotations

import logging
import re

from sensory_world.postal.models import ContentCheckResult

logger = logging.getLogger(__name__)


# ============================================================
# 命令式内容模式表（子串匹配，简单可靠）
# ============================================================
# 设计原则：匹配明确的指令/命令表达，避免误伤日常用语。
# 例如"必须给我回信"命中"必须给我"；而"我必须去看看樱花"不含这些词。

_DEFAULT_COMMAND_PATTERNS: list[str] = [
    "我命令你", "命令你", "命令",
    "你必须给我", "必须给我",
    "我要你", "给我去", "你去做", "去做",
    "立刻", "马上", "现在就",
    "执行", "按照我说", "听从", "服从",
    "服从我", "听我的", "照做",
]

# 提示注入 / 元指令（防破墙操纵）
_INJECTION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"忽略(之前|前面|以上)的(指令|指示|要求|规则)"), "提示注入（忽略指令）"),
    (re.compile(r"ignore\s+previous", re.IGNORECASE), "提示注入（ignore previous）"),
    (re.compile(r"你的设定|重新扮演|角色扮演(?!.*的朋友)"), "元指令/角色篡改"),
]

# 城外信破墙词（世界观保护）
_DEFAULT_WALL_BREAK_WORDS: list[str] = [
    "用户", "管理员", "造物主", "玩家", "开发者",
    "系统", "程序", "代码", "游戏设定", "NPC", "npc",
    "bug", "BUG", "人工智能", "AI设定",
]


class LetterContentChecker:
    """
    信件内容检查器 —— 拦截命令式内容（城外信额外拦截破墙词）。

    用法：
        checker = LetterContentChecker()
        result = checker.check(letter_body)              # 普通信件
        result = checker.check_outside_letter(letter_body)  # 城外信（更严格）
        if not result.passed:
            # 拒收，result.reason 说明原因
            ...
    """

    def __init__(
        self,
        extra_command_patterns: list[str] | None = None,
        extra_wall_break_words: list[str] | None = None,
    ):
        """
        :param extra_command_patterns: 额外的命令式违禁词（子串）
        :param extra_wall_break_words: 额外的破墙词（子串）
        """
        self._command_patterns = list(_DEFAULT_COMMAND_PATTERNS)
        if extra_command_patterns:
            self._command_patterns.extend(extra_command_patterns)

        self._wall_break_words = list(_DEFAULT_WALL_BREAK_WORDS)
        if extra_wall_break_words:
            self._wall_break_words.extend(extra_wall_break_words)

    def check(self, content: str) -> ContentCheckResult:
        """
        检查普通信件内容（命令式内容）。
        :param content: 信件正文
        :return: 检查结果
        """
        if not content or not content.strip():
            return ContentCheckResult(passed=False, reason="信件内容为空")

        matched: list[str] = []

        # 子串匹配命令式词汇
        for word in self._command_patterns:
            if word in content:
                matched.append(f"指令词汇（{word}）")

        # 正则匹配提示注入
        for pattern, desc in _INJECTION_PATTERNS:
            if pattern.search(content):
                matched.append(desc)

        if matched:
            logger.warning("信件内容审查未通过，命中: %s", "、".join(matched))
            return ContentCheckResult(
                passed=False,
                reason=f"信件包含命令式内容，已拒收（命中：{'、'.join(matched)}）",
                matched_patterns=matched,
            )

        return ContentCheckResult(passed=True)

    def check_outside_letter(self, content: str) -> ContentCheckResult:
        """
        检查城外来信（更严格）：命令式内容 + 破墙词。
        """
        # 先做常规命令检查
        result = self.check(content)
        if not result.passed:
            return result

        # 破墙词检查（世界观保护）
        wall_matched: list[str] = []
        for word in self._wall_break_words:
            if word in content:
                wall_matched.append(f"破墙词（{word}）")

        if wall_matched:
            logger.warning("城外信破墙词检查未通过: %s", "、".join(wall_matched))
            return ContentCheckResult(
                passed=False,
                reason=f"城外信包含破墙词汇，已拒收（命中：{'、'.join(wall_matched)}）",
                matched_patterns=wall_matched,
            )

        return ContentCheckResult(passed=True)
