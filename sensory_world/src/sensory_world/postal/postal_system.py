"""
邮差系统（PostalSystem）—— 主模块

承载 NPC：薇尔莉特（violet），人设为"代人写信、正在学习理解爱"的代笔邮差。

核心能力：
  1. NPC 间信件链路：NPC 委托写信 → 邮差（violet）用 LLM 生成正文 → 投递 → 双方写记忆
  2. 城外信箱（特殊通道）：
     - 外部来信通过 receive_outside_letter() 投入"旧邮筒"
     - 邮差发现后投递，信件进入收信 NPC 记忆
     - NPC 回信进入城外发件箱，等待外部取走
  3. 回信链路：收信方在窗口期内概率回信
  4. 三条铁律代码层强制：稀疏（配额）、平权（记忆同权重）、不指令（内容审查）

世界观：
  - 城外寄信人统称"城外的朋友"，无落款
  - NPC 间流传"有个守护这座城的人"的城市传说
  - 严禁出现"用户/管理员/造物主/玩家"等破墙词
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sensory_world.llm_client import FallbackLLMClient, SafeLLMClient
from sensory_world.postal.content_checker import LetterContentChecker
from sensory_world.postal.letter_store import LetterStore
from sensory_world.postal.models import (
    ContentCheckResult,
    Letter,
    LetterDirection,
    LetterStatus,
    OutsideMailbox,
    PostalConfig,
)
from sensory_world.protocols import (
    GameClockProtocol,
    LLMClientProtocol,
    MemoryEntry,
    NPCMemoryStoreProtocol,
    WorldDiaryProtocol,
)

logger = logging.getLogger(__name__)


# ============================================================
# 模板兜底文本（LLM 不可用时使用）
# ============================================================

_FALLBACK_LETTER_TEMPLATES = [
    "见字如面。{sender}想对{recipient}说：{reason}。愿你一切安好。",
    "亲爱的{recipient}：写下这封信时，{sender}正想念着你。{reason}。",
    "{recipient}，近来可好？{sender}托我捎来一句话：{reason}。",
]

_FALLBACK_OUTSIDE_TEMPLATES = [
    "致城里的{recipient}：我在远方惦念着你，愿这座城永远温暖。",
    "亲爱的{recipient}：听说了你的故事，想让你知道，有人在默默守护着这座城。",
]

_FALLBACK_REPLY_TEMPLATES = [
    "收到你的信，{sender}很开心。谢谢你的惦念，我在城里一切都好。",
]

# 收信记忆模板（城外信与普通信使用同样的记忆权重——平权铁律）
_MEMORY_RECEIVED_TEMPLATE = "收到了{sender}的信，信里说：{body_brief}"
_MEMORY_SENT_TEMPLATE = "托邮差给{recipient}寄了一封信，说了说{reason}"
_MEMORY_POSTMAN_TEMPLATE = "替{sender}把信送到了{recipient}手中"


class PostalSystem:
    """
    邮差系统 —— 城市书信往来的管理者。

    用法：
        postal = PostalSystem(
            llm=my_llm_client,
            memory=npc_memory_store,
            diary=world_diary,
            clock=game_clock,
            config=PostalConfig(...),   # 可选
        )
        await postal.initialize()

        # NPC 委托写信
        await postal.request_letter(
            sender_id="robin", recipient_id="eden",
            reason="想感谢伊甸上次的二重唱",
        )

        # 城外信投入旧邮筒（外部通道）
        await postal.receive_outside_letter(
            recipient_id="sparkle",
            content="……（城外朋友的来信正文）",
        )
    """

    def __init__(
        self,
        llm: LLMClientProtocol,
        memory: NPCMemoryStoreProtocol,
        clock: GameClockProtocol,
        diary: WorldDiaryProtocol | None = None,
        config: PostalConfig | None = None,
        store: LetterStore | None = None,
        checker: LetterContentChecker | None = None,
    ):
        self._safe_llm = SafeLLMClient(primary=llm, fallback=FallbackLLMClient())
        self._memory = memory
        self._diary = diary
        self._clock = clock
        self._config = config or PostalConfig()
        self._store = store or LetterStore(self._config.letters_file)
        self._checker = checker or LetterContentChecker()

        # 城外信箱
        self._mailbox = OutsideMailbox()

    async def initialize(self) -> None:
        """初始化：加载历史信件"""
        if not self._config.enabled:
            logger.info("邮差系统已禁用")
            return
        await self._store.load()
        logger.info("邮差系统初始化完成（邮差: %s）", self._config.postman_npc_id)

    # ========================================================
    # 1. NPC 间信件链路
    # ========================================================

    async def request_letter(
        self,
        sender_id: str,
        recipient_id: str,
        reason: str,
        event_ref: str = "",
    ) -> Letter | None:
        """
        NPC 委托写信：发起方委托邮差给收信方写一封信。

        :param sender_id: 发信 NPC
        :param recipient_id: 收信 NPC
        :param reason: 写信缘由
        :param event_ref: 关联事件 ID（如有）
        :return: 已送达的 Letter；失败返回 None
        """
        if not self._config.enabled:
            return None
        if sender_id == recipient_id:
            logger.warning("不能给自己写信: %s", sender_id)
            return None

        now = await self._clock.now()

        # 邮差（violet）用 LLM 生成信件正文
        body = await self._compose_letter_body(
            sender_id=sender_id,
            recipient_id=recipient_id,
            reason=reason,
            is_outside=False,
        )

        letter = Letter(
            direction=LetterDirection.NPC_TO_NPC,
            sender_id=sender_id,
            recipient_id=recipient_id,
            postman_id=self._config.postman_npc_id,
            subject=reason[:20],
            body=body,
            reason=reason,
            status=LetterStatus.DRAFT,
            event_ref=event_ref,
            created_at=now,
        )

        # 内容审查（NPC 间信件同样检查命令式内容）
        if self._config.content_check_enabled:
            check = self._checker.check(body)
            if not check.passed:
                letter.status = LetterStatus.REJECTED
                letter.metadata["reject_reason"] = check.reason
                await self._store.append(letter)
                logger.warning("NPC 信件被拒收: %s→%s (%s)", sender_id, recipient_id, check.reason)
                return None

        # 投递
        return await self._deliver_letter(letter, now)

    async def _deliver_letter(self, letter: Letter, now: datetime) -> Letter:
        """执行投递：更新状态、写记忆、记日记、持久化"""
        letter.status = LetterStatus.DELIVERED
        letter.delivered_at = now

        # 写入记忆 —— 收信方（高信度记忆）
        await self._write_memory(
            npc_id=letter.recipient_id,
            content=_MEMORY_RECEIVED_TEMPLATE.format(
                sender=self._display_name(letter.sender_id),
                body_brief=self._brief(letter.body),
            ),
            confidence=0.9,
            tags=["letter", "received"],
            extra={"letter_id": letter.letter_id, "direction": letter.direction.value},
            now=now,
        )

        # 发信方留一条记忆
        await self._write_memory(
            npc_id=letter.sender_id,
            content=_MEMORY_SENT_TEMPLATE.format(
                recipient=self._display_name(letter.recipient_id),
                reason=letter.reason or "一些心里话",
            ),
            confidence=0.8,
            tags=["letter", "sent"],
            extra={"letter_id": letter.letter_id},
            now=now,
        )

        # 邮差留投递记录
        await self._write_memory(
            npc_id=letter.postman_id,
            content=_MEMORY_POSTMAN_TEMPLATE.format(
                sender=self._display_name(letter.sender_id),
                recipient=self._display_name(letter.recipient_id),
            ),
            confidence=0.7,
            tags=["letter", "delivery"],
            extra={"letter_id": letter.letter_id},
            now=now,
        )

        # 世界日记
        if self._diary:
            try:
                await self._diary.write_entry(
                    category="postal",
                    content=(
                        f"{self._display_name(letter.sender_id)}托邮差"
                        f"给{self._display_name(letter.recipient_id)}捎去了一封信。"
                    ),
                    letter_id=letter.letter_id,
                )
            except Exception as e:
                logger.warning("写入信件日记失败: %s", e)

        await self._store.append(letter)
        logger.info(
            "信件已送达: %s → %s（%s）",
            letter.sender_id, letter.recipient_id, letter.letter_id,
        )
        return letter

    # ========================================================
    # 2. 城外信箱（特殊通道）
    # ========================================================

    async def receive_outside_letter(
        self,
        recipient_id: str,
        content: str,
        subject: str = "",
    ) -> Letter | None:
        """
        【城外通道】投入一封来自城外的信。

        世界观：信被放在城市边缘那个没有寄件人姓名的旧邮筒里，
        邮差发现后投递。寄信人是"城外的朋友"。

        铁律强制：
          - 稀疏：检查周配额，超额拒收
          - 不指令：内容审查（命令式 + 破墙词）
          - 平权：投递后记忆与普通信同权重（见 _deliver_letter）

        :param recipient_id: 收信 NPC
        :param content: 信件正文（只许生活情感内容）
        :param subject: 主题（可选）
        :return: 已送达的 Letter；被拒收返回 None
        """
        if not self._config.enabled:
            return None

        now = await self._clock.now()
        self._refresh_quota_period(now)

        # 铁律一【稀疏】：周配额检查
        if not self._mailbox.has_quota(self._config.outside_weekly_quota):
            logger.warning(
                "城外信配额已满（本周 %d/%d），拒收",
                self._mailbox.weekly_received_count,
                self._config.outside_weekly_quota,
            )
            return None

        # 铁律三【不指令】：城外信严格审查（命令式 + 破墙词）
        if self._config.content_check_enabled:
            check = self._checker.check_outside_letter(content)
            if not check.passed:
                rejected = Letter(
                    direction=LetterDirection.OUTSIDE_IN,
                    sender_id=OutsideMailbox.OUTSIDE_SENDER_ID,
                    recipient_id=recipient_id,
                    postman_id=self._config.postman_npc_id,
                    subject=subject or "城外来信",
                    body=content,
                    reason="城外来信（审查未通过）",
                    status=LetterStatus.REJECTED,
                    created_at=now,
                )
                rejected.metadata["reject_reason"] = check.reason
                await self._store.append(rejected)
                logger.warning("城外信审查未通过，已拒收留档: %s", check.reason)
                return None

        # 投入旧邮筒（收件箱）
        letter = Letter(
            direction=LetterDirection.OUTSIDE_IN,
            sender_id=OutsideMailbox.OUTSIDE_SENDER_ID,  # 城外的朋友
            recipient_id=recipient_id,
            postman_id=self._config.postman_npc_id,
            subject=subject or "来自城外的信",
            body=content,
            reason="城外的朋友捎来的信",
            status=LetterStatus.DRAFT,
            created_at=now,
        )
        self._mailbox.inbox.append(letter)
        self._mailbox.weekly_received_count += 1

        # 邮差发现旧邮筒里的信并投递
        delivered = await self._deliver_letter(letter, now)
        if letter in self._mailbox.inbox:
            self._mailbox.inbox.remove(letter)

        # 世界日记记录城市传说（不点名寄信人，维护"守护者"传说）
        if self._diary:
            try:
                await self._diary.write_entry(
                    category="postal_outside",
                    content=(
                        f"邮差在城市边缘的旧邮筒里发现一封没有落款的信，"
                        f"收信人是{self._display_name(recipient_id)}。"
                        f"居民们私下说，或许真有个守护这座城的人。"
                    ),
                    letter_id=letter.letter_id,
                )
            except Exception as e:
                logger.warning("写入城外信日记失败: %s", e)

        logger.info(
            "城外信已投递: 城外的朋友 → %s（本周配额 %d/%d）",
            recipient_id, self._mailbox.weekly_received_count,
            self._config.outside_weekly_quota,
        )
        return delivered

    async def maybe_send_to_outside(
        self,
        npc_id: str,
        reason: str,
        event_ref: str = "",
        force: bool = False,
    ) -> Letter | None:
        """
        NPC 主动寄信往城外（回信给"城外的朋友"）。

        铁律一【稀疏】：必须有事件由头（生日/大事件后），
        且按概率触发，不可随便寄。

        :param npc_id: 寄信 NPC
        :param reason: 事件由头（如"烟火大会后想感谢守护城市的人"）
        :param event_ref: 关联事件 ID
        :param force: 是否强制（测试用，跳过概率）
        :return: 进入城外发件箱的 Letter；未触发返回 None
        """
        if not self._config.enabled:
            return None

        # 必须有事件由头
        if not reason or not reason.strip():
            logger.info("NPC 寄往城外需有事件由头，跳过: %s", npc_id)
            return None

        # 概率触发
        if not force and random.random() > self._config.npc_outside_reply_probability:
            logger.info("NPC %s 这次没有决定寄信往城外", npc_id)
            return None

        now = await self._clock.now()
        body = await self._compose_letter_body(
            sender_id=npc_id,
            recipient_id=OutsideMailbox.OUTSIDE_SENDER_ID,
            reason=reason,
            is_outside=True,
        )

        letter = Letter(
            direction=LetterDirection.NPC_TO_OUTSIDE,
            sender_id=npc_id,
            recipient_id=OutsideMailbox.OUTSIDE_SENDER_ID,
            postman_id=self._config.postman_npc_id,
            subject=reason[:20],
            body=body,
            reason=reason,
            status=LetterStatus.PENDING,  # 等待外部取走
            event_ref=event_ref,
            created_at=now,
        )

        # 出城信也做破墙词检查（NPC 不应写出破墙内容）
        if self._config.content_check_enabled:
            check = self._checker.check_outside_letter(body)
            if not check.passed:
                letter.status = LetterStatus.REJECTED
                letter.metadata["reject_reason"] = check.reason
                await self._store.append(letter)
                logger.warning("出城信审查未通过: %s", check.reason)
                return None

        # 进入城外发件箱，等待外部取走
        self._mailbox.outbox.append(letter)
        await self._store.append(letter)

        # NPC 自己留一条记忆
        await self._write_memory(
            npc_id=npc_id,
            content=f"把一封写给出城朋友的信放进了城市边缘的旧邮筒，想说：{self._brief(body)}",
            confidence=0.8,
            tags=["letter", "sent_outside"],
            extra={"letter_id": letter.letter_id},
            now=now,
        )

        logger.info("NPC %s 的信已放入城外发件箱（等待取走）", npc_id)
        return letter

    async def collect_outbox(self) -> list[Letter]:
        """
        【外部通道】取走城外发件箱中所有待取信件。
        外部系统（管理员侧）调用此接口收取 NPC 寄往城外的信。
        取走后信件标记为已送达（对城外而言）。
        """
        pending = [
            l for l in self._mailbox.outbox
            if l.status == LetterStatus.PENDING
        ]
        now = await self._clock.now()
        for letter in pending:
            letter.status = LetterStatus.DELIVERED
            letter.delivered_at = now
            self._mailbox.outbox.remove(letter)
            # 重新持久化状态（追加一条状态记录）
            await self._store.append(letter)
        logger.info("外部取走 %d 封出城信", len(pending))
        return pending

    # ========================================================
    # 3. 回信链路
    # ========================================================

    async def maybe_reply(
        self,
        original_letter: Letter,
        force: bool = False,
    ) -> Letter | None:
        """
        收信方在回信窗口期内概率回信。

        :param original_letter: 收到的原信
        :param force: 强制回信（测试用）
        :return: 回信 Letter；未触发返回 None
        """
        if not self._config.enabled:
            return None

        now = await self._clock.now()

        # 城外信的回信走城外通道
        if original_letter.direction == LetterDirection.OUTSIDE_IN:
            return await self.maybe_send_to_outside(
                npc_id=original_letter.recipient_id,
                reason=f"收到城外朋友的信后想回信",
                event_ref="",
                force=force,
            )

        # 窗口期检查
        if original_letter.delivered_at:
            window_end = original_letter.delivered_at + timedelta(
                days=self._config.reply_window_days
            )
            if now > window_end and not force:
                logger.info("已过回信窗口期，不回信")
                return None

        # 概率检查
        if not force and random.random() > self._config.reply_probability:
            return None

        # 收信方 → 原发信方 回信
        return await self.request_letter(
            sender_id=original_letter.recipient_id,
            recipient_id=original_letter.sender_id,
            reason=f"回信给{self._display_name(original_letter.sender_id)}",
            event_ref=original_letter.event_ref,
        )

    # ========================================================
    # 4. 事件钩子（由阶段一/阶段三调用）
    # ========================================================

    async def on_event_occurred(
        self,
        event_name: str,
        participant_ids: list[str],
        event_ref: str = "",
    ) -> None:
        """
        事件发生钩子：大事件后，概率触发 NPC 寄信往来或寄信往城外。

        :param event_name: 事件名称
        :param participant_ids: 参与者列表
        :param event_ref: 事件 ID
        """
        if not self._config.enabled or len(participant_ids) < 1:
            return

        # 概率：某参与者想给城外的朋友写信（事件由头）
        npc = random.choice(participant_ids)
        await self.maybe_send_to_outside(
            npc_id=npc,
            reason=f"「{event_name}」结束后，想把这份心情告诉守护城市的人",
            event_ref=event_ref,
        )

    # ========================================================
    # 内部辅助
    # ========================================================

    async def _compose_letter_body(
        self,
        sender_id: str,
        recipient_id: str,
        reason: str,
        is_outside: bool,
    ) -> str:
        """邮差用 LLM 代写信件正文，失败则模板兜底"""
        sender_display = self._display_name(sender_id)
        recipient_display = self._display_name(recipient_id)

        system_prompt = (
            "你是薇尔莉特，一位代人写信的邮差，正在学习理解爱。"
            "请根据委托人的缘由，代写一封简短、真挚、有温度的信（80字以内）。"
            "只写信件正文，语气细腻克制，只包含生活与情感内容，"
            "不要出现任何命令、指令或要求。"
        )
        user_prompt = (
            f"委托人：{sender_display}\n"
            f"收信人：{recipient_display}\n"
            f"写信缘由：{reason}\n"
            f"请代写信件正文。"
        )

        try:
            body = await self._safe_llm.chat([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ])
            if body and body.strip():
                return body.strip()
        except Exception as e:
            logger.warning("LLM 写信失败，使用模板兜底: %s", e)

        # 模板兜底
        if is_outside:
            template = random.choice(_FALLBACK_OUTSIDE_TEMPLATES)
        else:
            template = random.choice(_FALLBACK_LETTER_TEMPLATES)
        return template.format(
            sender=sender_display,
            recipient=recipient_display,
            reason=reason or "想念你",
        )

    async def _write_memory(
        self,
        npc_id: str,
        content: str,
        confidence: float,
        tags: list[str],
        extra: dict[str, Any],
        now: datetime,
    ) -> None:
        """写入记忆（统一入口，城外信与普通信同权重——平权铁律）"""
        import uuid
        try:
            entry = MemoryEntry(
                entry_id=f"mem_letter_{uuid.uuid4().hex[:10]}",
                npc_id=npc_id,
                content=content,
                timestamp=now,
                confidence=confidence,
                tags=tags,
                metadata=extra,
            )
            await self._memory.add_entry(npc_id, entry)
        except Exception as e:
            logger.warning("写入信件记忆失败 [%s]: %s", npc_id, e)

    def _display_name(self, npc_id: str) -> str:
        """NPC 展示名（城外朋友特殊处理）"""
        if npc_id == OutsideMailbox.OUTSIDE_SENDER_ID:
            return OutsideMailbox.OUTSIDE_SENDER_DISPLAY
        return npc_id

    @staticmethod
    def _brief(text: str, max_len: int = 40) -> str:
        """截取信件正文摘要"""
        text = text.strip().replace("\n", " ")
        return text[:max_len] + ("…" if len(text) > max_len else "")

    def _refresh_quota_period(self, now: datetime) -> None:
        """刷新配额周期（按游戏周重置）"""
        if self._mailbox.quota_period_start is None:
            self._mailbox.quota_period_start = now
            return
        # 每 7 个游戏天重置一次
        elapsed = now - self._mailbox.quota_period_start
        if elapsed >= timedelta(days=7):
            self._mailbox.reset_quota(now)
            logger.info("城外信周配额已重置")

    # ========================================================
    # 状态查询
    # ========================================================

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def outside_quota_remaining(self) -> int:
        """本周城外信剩余配额"""
        return max(0, self._config.outside_weekly_quota - self._mailbox.weekly_received_count)

    @property
    def mailbox(self) -> OutsideMailbox:
        return self._mailbox

    async def get_letters_for_npc(self, npc_id: str) -> list[Letter]:
        """获取 NPC 相关信件"""
        return await self._store.get_by_npc(npc_id)

    def enable(self) -> None:
        self._config.enabled = True

    def disable(self) -> None:
        self._config.enabled = False
