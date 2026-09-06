"""
相册系统（PhotoAlbumSystem）—— 主模块

承载 NPC：三月七（march7th），热爱用相机记录城市瞬间的少女。

核心价值：
  每张照片给【所有在场者】的记忆库写入同一条共享记忆，
  形成跨 NPC 的记忆交集——未来两个 NPC 私语时可召回共同在场的事件。

拍照时机：
  - 周期事件（阶段一）：每个事件 1-3 张，三月七概率在场
  - NPC 自发聚会：概率拍照
  - 日常随机：低频
  - 生日合照 / 新店开张（阶段三钩子）
"""

from __future__ import annotations

import logging
import random
from datetime import datetime
from typing import Any

from sensory_world.album.models import AlbumConfig, Photo, PhotoSceneType, roll_event_photo_count
from sensory_world.album.photo_store import PhotoStore
from sensory_world.llm_client import FallbackLLMClient, SafeLLMClient
from sensory_world.protocols import (
    ChatterProtocol,
    GameClockProtocol,
    LLMClientProtocol,
    MemoryEntry,
    NPCMemoryStoreProtocol,
    WorldDiaryProtocol,
)

logger = logging.getLogger(__name__)


# 共享记忆模板（所有在场者写同一条——核心价值）
_SHARED_MEMORY_TEMPLATE = (
    "在「{event}」上和{others}合了影，照片在三月七的相册里。{description}"
)
_DAILY_MEMORY_TEMPLATE = (
    "和{others}在一起时被三月七拍了照，照片收在相册里。{description}"
)

# LLM 失败时的画面描述兜底
_FALLBACK_DESCRIPTIONS = [
    "镜头里大家都笑得很开心",
    "那一天的光线格外温柔",
    "每个人脸上都写着当下的心情",
    "快门响起时，谁也没有准备好",
]


class PhotoAlbumSystem:
    """
    相册系统 —— 城市照片的记录者。

    用法：
        album = PhotoAlbumSystem(
            llm=my_llm_client,
            memory=npc_memory_store,
            chatter=chatter_system,
            clock=game_clock,
            diary=world_diary,
        )
        await album.initialize()

        # 周期事件拍照钩子
        await album.on_event_photos(
            event_name="周六烟火大会",
            event_ref="evt_xxx",
            location="square",
            participants_by_slice=[...],
        )
    """

    def __init__(
        self,
        llm: LLMClientProtocol,
        memory: NPCMemoryStoreProtocol,
        clock: GameClockProtocol,
        chatter: ChatterProtocol | None = None,
        diary: WorldDiaryProtocol | None = None,
        config: AlbumConfig | None = None,
        store: PhotoStore | None = None,
    ):
        self._safe_llm = SafeLLMClient(primary=llm, fallback=FallbackLLMClient())
        self._memory = memory
        self._chatter = chatter
        self._clock = clock
        self._diary = diary
        self._config = config or AlbumConfig()
        self._store = store or PhotoStore(self._config.photos_file)

    async def initialize(self) -> None:
        """初始化：加载历史照片"""
        if not self._config.enabled:
            logger.info("相册系统已禁用")
            return
        await self._store.load()
        logger.info(
            "相册系统初始化完成（摄影 NPC: %s，已有照片 %d 张）",
            self._config.photographer_npc_id, self._store.count(),
        )

    # ========================================================
    # 1. 周期事件拍照（阶段一钩子）
    # ========================================================

    async def on_event_photos(
        self,
        event_name: str,
        event_ref: str,
        location: str,
        participants_by_slice: list[list[str]],
        force_photographer: bool = False,
    ) -> list[Photo]:
        """
        周期事件现场拍照钩子。

        由阶段一 EventRunner 在事件 ACTIVE 阶段调用。
        三月七概率在场；在场则为不同分片拍 1-3 张照片。

        :param event_name: 事件名
        :param event_ref: 事件实例 ID
        :param location: 主场地
        :param participants_by_slice: 各分片在场 NPC（波浪式分片）
        :param force_photographer: 强制三月七在场（测试用）
        :return: 本次拍摄的照片列表
        """
        if not self._config.enabled:
            return []

        # 三月七是否在场（事件中她在某个分片里，或概率在场）
        photographer = self._config.photographer_npc_id
        all_participants = self._flatten(participants_by_slice)
        photographer_present = (
            force_photographer
            or photographer in all_participants
            or random.random() < 0.7  # 大事件三月七通常会来拍照
        )
        if not photographer_present:
            logger.info("摄影师 %s 未在场，本次事件不拍照", photographer)
            return []

        now = await self._clock.now()
        target_count = roll_event_photo_count(self._config)

        # 从各分片挑选画面（每张照片记录一个分片的人群）
        non_empty_slices = [s for s in participants_by_slice if s]
        if not non_empty_slices:
            return []

        photos: list[Photo] = []
        # 保证摄影师在每张照片的在场者里
        for i in range(min(target_count, len(non_empty_slices))):
            slice_group = non_empty_slices[i % len(non_empty_slices)]
            participants = list(dict.fromkeys([photographer] + slice_group))

            photo = await self._take_photo(
                now=now,
                location=location,
                scene_type=PhotoSceneType.PERIODIC_EVENT,
                event_name=event_name,
                event_ref=event_ref,
                participants=participants,
                photo_index=i + 1,
            )
            if photo:
                photos.append(photo)

        logger.info("事件「%s」拍摄 %d 张照片", event_name, len(photos))
        return photos

    # ========================================================
    # 2. NPC 自发聚会拍照
    # ========================================================

    async def on_casual_gathering(
        self,
        location: str,
        participant_ids: list[str],
        force: bool = False,
    ) -> Photo | None:
        """
        NPC 自发聚会拍照钩子。

        :param force: 强制拍照（测试用）
        """
        if not self._config.enabled:
            return None
        if len(participant_ids) < self._config.daily_min_participants:
            return None

        photographer = self._config.photographer_npc_id
        if not force and photographer not in participant_ids:
            # 三月七不在场则概率不拍
            if random.random() >= self._config.casual_photo_probability * 0.5:
                return None
        if not force and random.random() >= self._config.casual_photo_probability:
            return None

        now = await self._clock.now()
        participants = list(dict.fromkeys([photographer] + participant_ids))
        return await self._take_photo(
            now=now,
            location=location,
            scene_type=PhotoSceneType.CASUAL_GATHERING,
            event_name="日常小聚",
            event_ref="",
            participants=participants,
        )

    # ========================================================
    # 3. 日常随机拍照（低频）
    # ========================================================

    async def maybe_daily_photo(
        self,
        location: str,
        participant_ids: list[str],
        force: bool = False,
    ) -> Photo | None:
        """日常 tick 中低频触发的随机拍照"""
        if not self._config.enabled:
            return None
        if not force and random.random() >= self._config.daily_photo_probability:
            return None
        if len(participant_ids) < self._config.daily_min_participants:
            return None

        photographer = self._config.photographer_npc_id
        participants = list(dict.fromkeys([photographer] + participant_ids))
        now = await self._clock.now()
        return await self._take_photo(
            now=now,
            location=location,
            scene_type=PhotoSceneType.DAILY,
            event_name="日常一刻",
            event_ref="",
            participants=participants,
        )

    # ========================================================
    # 3.5 特殊场合：生日合照 / 新店开张首照（阶段三日历系统复用）
    # ========================================================

    async def on_birthday(
        self,
        npc_id: str,
        year: int,
        location: str = "square",
        participant_ids: list[str] | None = None,
    ) -> Photo | None:
        """
        生日合照钩子（由日历系统生日链路调用）。

        :param npc_id: 过生日的 NPC
        :param year: 城市纪元年份
        :param location: 合照地点（默认广场；主项目可传实际聚会地点）
        :param participant_ids: 在场名单；不传则由摄影 NPC 与寿星组成
        """
        if not self._config.enabled:
            return None
        photographer = self._config.photographer_npc_id
        participants = list(dict.fromkeys(
            [photographer, npc_id, *(participant_ids or [])]
        ))
        now = await self._clock.now()
        return await self._take_photo(
            now=now,
            location=location,
            scene_type=PhotoSceneType.BIRTHDAY,
            event_name=f"{npc_id}的生日",
            event_ref=f"birthday:{npc_id}:{year}",
            participants=participants,
        )

    async def on_new_shop(
        self,
        npc_id: str,
        shop_location: str,
        shop_type: str,
        visitor_ids: list[str] | None = None,
    ) -> Photo | None:
        """
        新店开张首张店铺照片钩子（由日历系统开张协议调用）。

        :param npc_id: 店主 NPC
        :param shop_location: 店铺地点 ID
        :param shop_type: 店铺类型中文名（如"书店"）
        :param visitor_ids: 首日造访 NPC 名单
        """
        if not self._config.enabled:
            return None
        photographer = self._config.photographer_npc_id
        participants = list(dict.fromkeys(
            [photographer, npc_id, *(visitor_ids or [])]
        ))
        now = await self._clock.now()
        return await self._take_photo(
            now=now,
            location=shop_location,
            scene_type=PhotoSceneType.NEW_SHOP,
            event_name=f"{npc_id}的{shop_type}开张",
            event_ref=f"new_shop:{shop_location}",
            participants=participants,
        )

    # ========================================================
    # 4. 翻看相册（日程钩子）—— 触发回忆型私语
    # ========================================================

    async def browse_album(
        self,
        npc_id: str,
        force_recall: bool = False,
    ) -> Photo | None:
        """
        NPC 日程"翻看相册"钩子。

        召回一张该 NPC 在场的旧照片，概率触发回忆型私语
        （召回旧照片记忆，可与照片里的共同在场者开启话题）。

        :return: 被翻到的照片；无照片或未触发返回 None
        """
        if not self._config.enabled:
            return None

        my_photos = await self._store.get_by_npc(npc_id)
        if not my_photos:
            return None

        photo = random.choice(my_photos)

        # 概率触发回忆型私语
        if force_recall or random.random() < self._config.browse_recall_probability:
            others = [p for p in photo.participant_ids if p != npc_id]
            recall_topic = (
                f"翻看相册时看到了{photo.event_name or '那天'}的照片，"
                f"想起了和{('、'.join(others[:3])) or '大家'}在一起的时光"
            )
            if self._chatter:
                try:
                    # 召回旧照片记忆作为私语话题
                    await self._chatter.trigger_topic(
                        npc_id=npc_id,
                        topic=recall_topic,
                        context={"photo_id": photo.photo_id, "memory_tags": ["photo"]},
                    )
                except Exception as e:
                    logger.warning("触发回忆私语失败: %s", e)
            logger.info("%s 翻看相册，回忆起 %s", npc_id, photo.photo_id)

        return photo

    # ========================================================
    # 内部：拍照 + 写共同记忆
    # ========================================================

    async def _take_photo(
        self,
        now: datetime,
        location: str,
        scene_type: PhotoSceneType,
        event_name: str,
        event_ref: str,
        participants: list[str],
        photo_index: int = 1,
    ) -> Photo | None:
        """拍一张照片：生成描述 → 落盘 → 给所有在场者写共享记忆"""
        photographer = self._config.photographer_npc_id
        if photographer not in participants:
            participants = [photographer] + participants

        # LLM 生成一句话画面描述，失败模板兜底
        description = await self._compose_description(
            event_name=event_name,
            location=location,
            participants=participants,
        )

        photo = Photo(
            photographer_id=photographer,
            timestamp=now,
            location=location,
            scene_type=scene_type,
            event_name=event_name,
            event_ref=event_ref,
            participant_ids=participants,
            description=description,
            metadata={"photo_index": photo_index},
        )

        # 持久化
        await self._store.append(photo)

        # 核心：给所有在场者写同一条共享记忆
        await self._write_shared_memories(photo, now)

        # 世界日记
        if self._diary:
            try:
                await self._diary.write_entry(
                    category="album",
                    content=(
                        f"三月七在{location}拍下了「{event_name}」的瞬间，"
                        f"照片里有{('、'.join(participants[:6]))}等人。"
                    ),
                    photo_id=photo.photo_id,
                )
            except Exception as e:
                logger.warning("写入照片日记失败: %s", e)

        return photo

    async def _write_shared_memories(self, photo: Photo, now: datetime) -> None:
        """
        给所有在场者写入同一条共享记忆 —— 相册系统核心价值。
        跨 NPC 的记忆交集：未来私语可召回共同在场的事件。
        """
        import uuid

        others_default = "大家"
        for npc_id in photo.participant_ids:
            others = [p for p in photo.participant_ids if p != npc_id]
            others_text = "、".join(others[:4]) if others else others_default
            if photo.scene_type in (PhotoSceneType.PERIODIC_EVENT,
                                    PhotoSceneType.BIRTHDAY,
                                    PhotoSceneType.NEW_SHOP):
                content = _SHARED_MEMORY_TEMPLATE.format(
                    event=photo.event_name or "活动",
                    others=others_text,
                    description=photo.description,
                )
            else:
                content = _DAILY_MEMORY_TEMPLATE.format(
                    others=others_text,
                    description=photo.description,
                )

            try:
                entry = MemoryEntry(
                    entry_id=f"mem_photo_{uuid.uuid4().hex[:10]}",
                    npc_id=npc_id,
                    content=content,
                    timestamp=now,
                    confidence=0.85,
                    tags=["photo", "shared_memory", photo.event_name or "daily"],
                    metadata={
                        "photo_id": photo.photo_id,
                        "event_ref": photo.event_ref,
                        "co_present": others,  # 共同在场者——记忆交集关键
                    },
                )
                await self._memory.add_entry(npc_id, entry)
            except Exception as e:
                logger.warning("写入照片共享记忆失败 [%s]: %s", npc_id, e)

    async def _compose_description(
        self,
        event_name: str,
        location: str,
        participants: list[str],
    ) -> str:
        """LLM 生成一句话画面描述，失败模板兜底"""
        system_prompt = (
            "你是相册的画面描述者。请用一句温柔、有画面感的话描述这张照片的瞬间"
            "（30字以内），只描述画面与氛围，不出现命令式内容。"
        )
        user_prompt = (
            f"场合：{event_name}\n地点：{location}\n"
            f"在场：{('、'.join(participants[:8]))}\n"
            f"请描述快门按下的那一瞬间。"
        )
        try:
            desc = await self._safe_llm.chat([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ])
            if desc and desc.strip():
                return desc.strip()
        except Exception as e:
            logger.warning("LLM 生成照片描述失败，模板兜底: %s", e)
        return random.choice(_FALLBACK_DESCRIPTIONS)

    @staticmethod
    def _flatten(groups: list[list[str]]) -> list[str]:
        seen: list[str] = []
        for g in groups:
            for npc in g:
                if npc not in seen:
                    seen.append(npc)
        return seen

    # ========================================================
    # 状态查询
    # ========================================================

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def photo_count(self) -> int:
        return self._store.count()

    async def get_shared_photos(self, npc_a: str, npc_b: str) -> list[Photo]:
        """获取两个 NPC 的共同照片（记忆交集）"""
        return await self._store.get_shared_photos(npc_a, npc_b)

    async def get_photos_for_npc(self, npc_id: str) -> list[Photo]:
        return await self._store.get_by_npc(npc_id)

    def enable(self) -> None:
        self._config.enabled = True

    def disable(self) -> None:
        self._config.enabled = False
