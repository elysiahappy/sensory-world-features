"""城市历法系统（CityCalendar）—— 主模块。

在现有游戏时间之上叠加"城市纪元"历法：
  1. 日期推进：记录第 N 天/周/月/季/年；季节流转（春夏秋冬），
     季节通过 SeasonNote 向事件系统与 chatter 提供天气倾向与行为注释。
  2. 居民生日：生日当天联动
     - 邮差系统最先得知（贺卡链路，复用 PostalSystem.send_greeting_card）
     - 相册系统生日合照（复用 PhotoAlbumSystem.on_birthday）
     - 亲近 NPC 概率触发祝福私语（chatter.trigger_topic）
     - 世界日记记录生日条目
  3. 年度回声：周期事件每年同期再次触发时，召回参与者去年同期记忆，
     注入私语话题（"去年烟火大会……"）；无记忆静默跳过。
  4. 新店开张协议：register_new_shop() 标准化开张流程。

设计原则：
  - 所有下游系统（邮差/相册/chatter/日记/群聊）均为可选注入，缺失时降级跳过；
  - LLM 不直接被日历调用（贺卡/照片的 LLM 由邮差/相册内部处理）；
  - 时间仅以 GameClock 为准，日历本身不推进游戏时间。
"""

from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING, Any

from sensory_world.calendar.config_loader import load_birthdays, load_calendar_config
from sensory_world.calendar.models import (
    Birthday,
    BirthdayRecord,
    CalendarConfig,
    CityDate,
    Season,
    SeasonNote,
    ShopOpening,
)
from sensory_world.protocols import (
    ChatterProtocol,
    GameClockProtocol,
    GroupSceneProtocol,
    NPCMemoryStoreProtocol,
    WorldDiaryProtocol,
)

if TYPE_CHECKING:
    from sensory_world.album.album_system import PhotoAlbumSystem
    from sensory_world.postal.postal_system import PostalSystem

logger = logging.getLogger(__name__)


class CityCalendar:
    """
    城市历法系统。

    主循环每 tick 调用 ``await calendar.tick(game_time)``，由日历判断
    是否跨天，并在跨天时推进历法、处理生日 / 季节注释。
    """

    def __init__(
        self,
        clock: GameClockProtocol,
        memory: NPCMemoryStoreProtocol | None = None,
        diary: WorldDiaryProtocol | None = None,
        chatter: ChatterProtocol | None = None,
        group_scene: GroupSceneProtocol | None = None,
        postal: "PostalSystem | None" = None,
        album: "PhotoAlbumSystem | None" = None,
        config: CalendarConfig | None = None,
        birthdays: dict[str, Birthday] | None = None,
    ):
        self._clock = clock
        self._memory = memory
        self._diary = diary
        self._chatter = chatter
        self._group_scene = group_scene
        self._postal = postal
        self._album = album
        self._config = config or CalendarConfig()
        self._birthdays = birthdays or {}

        # 当前城市日期与季节注释
        self._current: CityDate | None = None
        self._season_note: SeasonNote | None = None
        # 上一次处理的 total_days，用于检测跨天
        self._last_total_days: int = -1
        # 已触发的生日（幂等：同 npc 同年只触发一次）
        self._celebrated: set[str] = set()
        # 已开张的店铺（防重复注册）
        self._openings: list[ShopOpening] = []

    # ========================================================
    # 初始化
    # ========================================================

    @classmethod
    def from_config(
        cls,
        clock: GameClockProtocol,
        calendar_config_path: str | None = None,
        birthdays_config_path: str | None = None,
        **deps: Any,
    ) -> "CityCalendar":
        """从 YAML 配置文件构造（配置缺失时零配置兜底）。"""
        config = load_calendar_config(calendar_config_path)
        birthdays = load_birthdays(birthdays_config_path)
        return cls(clock=clock, config=config, birthdays=birthdays, **deps)

    async def initialize(self) -> None:
        """初始化：根据当前游戏时间建立城市日期。"""
        if not self._config.enabled:
            logger.info("日历系统已禁用")
            return
        game_time = await self._clock.now()
        total = await self._clock.total_days()
        # 初始化即一次推进：若开局当天恰逢生日/新年，也应正常触发
        self._advance_to(total, trigger_events=self._config.birthday_enabled)
        self._last_total_days = total
        logger.info(
            "城市历法初始化：%s（季节：%s）",
            self._current, self._current.season.cn if self._current else "?",
        )

    # ========================================================
    # 主 tick：跨天时推进历法
    # ========================================================

    async def tick(self, game_time: Any) -> None:
        """
        每 tick 调用。仅在"游戏日"发生跨越时推进历法并处理当日事件。

        :param game_time: 当前游戏时间（datetime），与 clock.now() 一致
        """
        if not self._config.enabled:
            return
        total = await self._clock.total_days()
        if total == self._last_total_days:
            return  # 同一天内，无需推进
        self._advance_to(total, trigger_events=True)
        self._last_total_days = total

    def _advance_to(self, total_days: int, trigger_events: bool) -> CityDate:
        """推进到指定累计天数对应的城市日期。"""
        city_date = CityDate.from_total_days(
            total_days - self._config.epoch_start_total_day
        )
        # 季节注释（季节变化时更新；无变化也保持最新）
        self._current = city_date
        self._season_note = SeasonNote(city_date.season)

        if trigger_events:
            logger.info("城市历法推进到 %s", city_date)
            # 新年第一天
            if city_date.is_year_start and city_date.year > 1:
                self._safe_diary(
                    f"城市纪元第 {city_date.year} 年",
                    f"新的一年开始了，城市迎来了第 {city_date.year} 个春天。",
                    ["calendar", "new_year"],
                )
            # 当日生日
            if self._config.birthday_enabled:
                self._process_birthdays(city_date)

        return city_date

    # ========================================================
    # 季节注释（供事件系统 / chatter 读取）
    # ========================================================

    @property
    def current_date(self) -> CityDate | None:
        return self._current

    @property
    def season_note(self) -> SeasonNote | None:
        """当前季节注释；系统未初始化时为 None。"""
        return self._season_note

    def get_season_hint_text(self) -> str:
        """供 chatter 话题注入的季节旁白（无季节信息时返回空串）。"""
        if self._season_note is None:
            return ""
        return self._season_note.as_text()

    # ========================================================
    # 生日链路
    # ========================================================

    def _process_birthdays(self, city_date: CityDate) -> None:
        """找出当天生日的 NPC，触发生日链路。

        生日配置 day_of_year 为 1~360；城市日期 day_of_year 为 0~359，
        故当天命中条件为 bday.day_of_year == city_date.day_of_year + 1。
        """
        today_doy = city_date.day_of_year + 1
        for npc_id, bday in self._birthdays.items():
            if bday.day_of_year != today_doy:
                continue
            record_key = f"{npc_id}:{city_date.year}"
            if record_key in self._celebrated:
                continue
            self._celebrated.add(record_key)
            # 异步触发生日链路（内部各自 try/except 降级）
            self._schedule_birthday(npc_id, bday, city_date)

    def _schedule_birthday(self, npc_id: str, bday: Birthday, city_date: CityDate) -> None:
        """以任务方式触发生日链路（不阻塞 tick）。"""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._run_birthday_chain(npc_id, bday, city_date))
        except RuntimeError:
            # 无事件循环（测试直调）时同步尽力执行
            logger.debug("无运行中事件循环，生日链路以协程形式返回，需主项目调度")

    async def _run_birthday_chain(
        self, npc_id: str, bday: Birthday, city_date: CityDate
    ) -> None:
        """
        生日当天完整链路：
          邮差贺卡 → 生日合照 → 亲近 NPC 祝福私语 → 世界日记。
        每一步独立降级，任一失败不影响其他步骤。
        """
        logger.info("今天是 %s 的生日（城市纪元第 %s 年）", npc_id, city_date.year)

        # 世界日记：生日条目
        self._safe_diary(
            f"今天是{npc_id}的生日",
            f"城市里悄悄传开了——今天是{npc_id}的生日，居民们打算给点惊喜。",
            ["calendar", "birthday"],
        )

        # 1) 邮差系统最先得知：贺卡/信件链路
        if self._postal and random.random() < self._config.birthday_card_probability:
            try:
                await self._postal.send_greeting_card(
                    recipient_id=npc_id,
                    occasion="birthday",
                    reason=f"{npc_id}的生日",
                    sender_id="city_friends",
                    event_ref=f"birthday:{npc_id}:{city_date.year}",
                )
            except Exception as e:
                logger.warning("生日贺卡链路失败 [%s]: %s", npc_id, e)

        # 2) 相册系统：生日合照（在场者用亲近 NPC 近似——由主项目补充实际在场名单）
        if self._album and random.random() < self._config.birthday_photo_probability:
            try:
                await self._album.on_birthday(
                    npc_id=npc_id,
                    year=city_date.year,
                )
            except Exception as e:
                logger.warning("生日合照链路失败 [%s]: %s", npc_id, e)

        # 3) 亲近 NPC 概率触发祝福私语
        await self._birthday_greeting_chatter(npc_id)

    async def _birthday_greeting_chatter(self, npc_id: str) -> None:
        """亲近 NPC 概率触发祝福私语（chatter.trigger_topic）。"""
        if not self._chatter or not self._memory:
            return
        if random.random() >= self._config.birthday_greeting_chatter_probability:
            return
        # 从记忆中召回与生日 NPC 相处密切的角色作为"亲近 NPC"候选
        try:
            friends = await self._recall_close_npcs(npc_id)
            random.shuffle(friends)
            count = 0
            for friend in friends:
                if count >= self._config.birthday_greeting_max_npcs:
                    break
                if friend == npc_id:
                    continue
                # 向祝福者注入"今天是 npc_id 生日"的私语话题
                await self._chatter.trigger_topic(
                    npc_id=friend,
                    topic=f"今天是{npc_id}的生日，找他/她说上一句生日祝福吧",
                    context={"occasion": "birthday", "about": npc_id},
                )
                count += 1
        except Exception as e:
            logger.warning("生日祝福私语失败 [%s]: %s", npc_id, e)

    async def _recall_close_npcs(self, npc_id: str) -> list[str]:
        """
        从记忆中召回与某 NPC 相处密切的角色名单。

        依赖记忆条目里的共同在场者 / 人物标签；若记忆协议无法提供，
        返回空列表（调用方静默跳过）。需主项目确认记忆 metadata 结构。
        """
        if not self._memory:
            return []
        try:
            entries = await self._memory.recall(
                npc_id, query="朋友 伙伴 一起 相处", top_k=8
            )
        except Exception:
            return []
        friends: list[str] = []
        for e in entries:
            # 优先从 metadata.co_present（相册共同记忆）取共同在场者
            for key in ("co_present", "participant_ids", "with_npcs"):
                val = e.metadata.get(key) if e.metadata else None
                if isinstance(val, list):
                    friends.extend(str(x) for x in val)
        # 去重、排除自己
        seen: set[str] = set()
        result = []
        for f in friends:
            if f and f != npc_id and f not in seen:
                seen.add(f)
                result.append(f)
        return result

    # ========================================================
    # 年度回声
    # ========================================================

    async def on_periodic_event_start(
        self,
        event_name: str,
        participant_ids: list[str],
        event_year: int | None = None,
    ) -> None:
        """
        周期事件开始时调用（阶段一事件启动钩子）：
        若该事件在"去年同期"也曾发生，召回参与者去年的记忆，注入私语话题。

        - 第 1 年没有"去年"，静默跳过；
        - 召回通过记忆协议（recall / tag 查询），无记忆静默跳过。

        :param event_name: 事件名（如"周六烟火大会"）
        :param participant_ids: 本届参与者
        :param event_year: 当前年份（不传则用历法当前年份）
        """
        if not self._config.enabled or not self._config.echo_enabled:
            return
        if self._current is None:
            return
        year = event_year if event_year is not None else self._current.year
        if year <= 1:
            logger.debug("城市纪元第 1 年，无去年回声: %s", event_name)
            return

        logger.info("年度回声检查：%s（第 %s 年）", event_name, year)
        topics = 0
        for npc_id in participant_ids:
            if topics >= self._config.echo_max_topics:
                break
            memory_text = await self._recall_last_year_memory(
                npc_id, event_name, year - 1
            )
            if not memory_text:
                continue
            # 找另一位同样参加过去年的参与者配成私语
            partner = await self._find_echo_partner(
                npc_id, event_name, year - 1, participant_ids
            )
            if partner and self._chatter:
                try:
                    await self._chatter.trigger_topic(
                        npc_id=npc_id,
                        topic=f"去年{event_name}的时候——{memory_text}，今年又到了这个时候，和{partner}聊聊去年吧",
                        context={"occasion": "annual_echo", "event": event_name, "with": partner},
                    )
                    topics += 1
                except Exception as e:
                    logger.warning("年度回声私语失败 [%s]: %s", npc_id, e)

    async def _recall_last_year_memory(
        self, npc_id: str, event_name: str, last_year: int
    ) -> str:
        """召回某 NPC 关于去年同期事件的记忆摘要；无则返回空串。"""
        if not self._memory:
            return ""
        # 优先按事件名语义召回
        try:
            entries = await self._memory.recall(
                npc_id,
                query=f"去年 {event_name} 活动 合影",
                top_k=self._config.echo_memory_per_npc,
            )
        except Exception:
            entries = []
        # 过滤出与事件名相关、且记忆时间早于今年（近似：内容含事件名）的条目
        for e in entries:
            if event_name in e.content or "合影" in e.content or event_name in " ".join(e.tags):
                return e.content[:50]
        return ""

    async def _find_echo_partner(
        self,
        npc_id: str,
        event_name: str,
        last_year: int,
        current_participants: list[str],
    ) -> str | None:
        """在本届参与者中找一位也留有去年记忆的角色作为私语对象。"""
        if not self._memory:
            return None
        for other in current_participants:
            if other == npc_id:
                continue
            mem = await self._recall_last_year_memory(other, event_name, last_year)
            if mem:
                return other
        return None

    # ========================================================
    # 新店开张协议（城市生长）
    # ========================================================

    async def register_new_shop(
        self,
        npc_id: str,
        shop_location: str,
        shop_type: str,
        visitor_ids: list[str] | None = None,
    ) -> ShopOpening | None:
        """
        新 NPC 入住并带有经营场所时，标准化"开张事件"流程：

          1. 世界日记开张条目
          2. 周边 NPC 首日造访 group_scene
          3. 摄影 NPC 首张店铺照片（复用相册系统）
          4. 邮差系统发送开业邀请函（复用邮差系统）

        :param npc_id: 店主 NPC
        :param shop_location: 店铺地点 ID
        :param shop_type: 店铺类型（bookstore/bakery/restaurant/post_office...）
        :param visitor_ids: 首日造访 NPC 名单；不传则由调用方/主项目补充
        :return: ShopOpening 记录；系统禁用返回 None
        """
        if not self._config.enabled or not self._config.new_shop_enabled:
            return None

        # 防重复注册
        for o in self._openings:
            if o.shop_location == shop_location:
                logger.warning("店铺已注册过开张: %s", shop_location)
                return o

        city_date = self._current or CityDate.from_total_days(0)
        shop_cn = self._config.shop_type_names.get(shop_type, shop_type)
        opening = ShopOpening(
            npc_id=npc_id,
            shop_location=shop_location,
            shop_type=shop_type,
            day=city_date.day,
            city_date_desc=str(city_date),
        )
        self._openings.append(opening)

        logger.info("新店开张：%s 的%s（%s）", npc_id, shop_cn, shop_location)

        # 1) 世界日记开张条目
        self._safe_diary(
            f"{npc_id}的{shop_cn}开张了",
            f"城市里新开了一家{shop_cn}，店主是{npc_id}，地点在{shop_location}，街坊们都来道贺。",
            ["calendar", "new_shop", shop_type],
        )

        visitors = visitor_ids or []
        # 2) 周边 NPC 首日造访 group_scene
        if self._group_scene and visitors:
            try:
                await self._group_scene.start_scene(
                    scene_id=f"new_shop_{shop_location}",
                    location=shop_location,
                    participant_ids=[npc_id, *visitors][: self._config.new_shop_visitor_count + 1],
                    topic=f"{npc_id}的{shop_cn}开张首日，大家进店道贺、参观",
                )
            except Exception as e:
                logger.warning("开张首日 group_scene 失败: %s", e)

        # 3) 摄影 NPC 首张店铺照片（复用相册系统）
        if self._album and random.random() < self._config.new_shop_photo_probability:
            try:
                await self._album.on_new_shop(
                    npc_id=npc_id,
                    shop_location=shop_location,
                    shop_type=shop_cn,
                )
            except Exception as e:
                logger.warning("开张拍照链路失败: %s", e)

        # 4) 邮差系统发送开业邀请函
        if self._postal and visitors and random.random() < self._config.new_shop_invitation_probability:
            for guest in visitors[: self._config.new_shop_invitation_max]:
                try:
                    await self._postal.send_greeting_card(
                        recipient_id=guest,
                        occasion="opening",
                        reason=f"{npc_id}的{shop_cn}开张了，邀请你来坐坐",
                        sender_id=npc_id,
                        event_ref=f"new_shop:{shop_location}",
                    )
                except Exception as e:
                    logger.warning("开业邀请函发送失败 [%s]: %s", guest, e)

        return opening

    def list_openings(self) -> list[ShopOpening]:
        """已登记的开张记录。"""
        return list(self._openings)

    # ========================================================
    # 内部工具
    # ========================================================

    def _safe_diary(self, title: str, content: str, tags: list[str]) -> None:
        """安全写世界日记（失败仅告警）。

        世界日记协议方法为 write_entry(category, content, **metadata)；
        title 通过 metadata 传入，category 取首个标签。
        """
        if not self._diary:
            return
        import asyncio

        async def _write() -> None:
            try:
                await self._diary.write_entry(
                    tags[0] if tags else "calendar",
                    f"{title}：{content}",
                    title=title,
                    tags=tags,
                )
            except Exception as e:
                logger.warning("写日历日记失败: %s", e)

        try:
            asyncio.get_running_loop()
            asyncio.ensure_future(_write())
        except RuntimeError:
            logger.debug("无事件循环，日记条目跳过（%s）", title)

    def add_birthday(self, npc_id: str, day_of_year: int, note: str = "") -> None:
        """运行期动态补充生日配置（如新 NPC 入住）。"""
        self._birthdays[npc_id] = Birthday(
            npc_id=npc_id, day_of_year=day_of_year, note=note
        )

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def enable(self) -> None:
        self._config.enabled = True

    def disable(self) -> None:
        self._config.enabled = False
