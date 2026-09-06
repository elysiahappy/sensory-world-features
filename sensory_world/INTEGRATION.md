# 阶段一：周期事件系统（PeriodicEventSystem）—— 集成说明

## 概述

周期事件系统为城市模拟添加周期性大事件的调度能力。系统在每个游戏 tick 中检查是否有事件需要触发、推进或结束，自动管理事件的完整生命周期（预告 → 执行 → 收尾）。

## 快速集成

### 1. 在主循环中挂载

```python
from sensory_world.periodic_event import PeriodicEventSystem

# 初始化（在主项目启动时调用一次）
event_system = PeriodicEventSystem(
    llm=your_llm_client,           # 实现 LLMClientProtocol
    diary=world_diary,             # 实现 WorldDiaryProtocol
    memory=npc_memory_store,       # 实现 NPCMemoryStoreProtocol
    emotion=npc_emotion,           # 实现 NPCEmotionProtocol
    group_scene=group_scene_mgr,   # 实现 GroupSceneProtocol
    chatter=chatter_mgr,           # 实现 ChatterProtocol
    schedule=schedule_book,        # 实现 ScheduleBookProtocol
    clock=game_clock,              # 实现 GameClockProtocol
    config_dir="path/to/configs",  # 可选，默认使用包内样例配置
    max_concurrent_scenes=4,       # 并发群聊上限（推荐 ≤ 推理槽位数/2）
)
await event_system.initialize()

# 设置城市居民池（用于普通居民概率汇聚）
event_system.set_resident_pool(["npc_a", "npc_b", ...])

# 在主循环的每个 tick 中调用
async def main_loop():
    while True:
        game_time = await game_clock.now()
        await event_system.tick(game_time)
        await asyncio.sleep(tick_interval)
```

### 2. 需要主项目实现的协议接口

所有接口定义在 `sensory_world/protocols.py`，主项目需实现以下 Protocol：

| 协议 | 对应现有模块 | 关键方法 | 需确认 |
|------|------------|---------|--------|
| `GameClockProtocol` | 时间系统 | `now() -> datetime`, `weekday() -> int` | 游戏时间精度 |
| `WorldDiaryProtocol` | `world_diary` | `write_entry(category, content, **metadata)` | 是否需要 add_npc 联动 |
| `NPCMemoryStoreProtocol` | `npc_memory_store` | `add_entry(npc_id, entry)`, `recall(npc_id, query, top_k)` | MemoryEntry 格式兼容 |
| `NPCEmotionProtocol` | `npc_emotion` | `inject_emotion(npc_id, emotion, intensity, ...)` | 情绪类型枚举映射 |
| `GroupSceneProtocol` | `group_scene` | `start_scene(...)`, `end_scene(run_id)` | 场景 ID 命名规则 |
| `ChatterProtocol` | `chatter` | `trigger_chatter(npc_a, npc_b, topic, boost_probability)` | boost_probability 语义 |
| `ScheduleBookProtocol` | `schedule_book` | `override_schedule(...)`, `get_location(npc_id)` | 日程恢复机制 |
| `LLMClientProtocol` | `npc_brain_layer` | `chat(messages) -> str` | 消息格式兼容 |

### 3. 配置项

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `config_dir` | str/Path | 包内 `configs/` | 事件配置 YAML 目录 |
| `max_concurrent_scenes` | int | 4 | 最大并发 LLM 群聊数 |
| `acquire_timeout` | float | 30.0 | 并发槽获取超时（秒） |
| `enabled` | bool | True | 系统总开关 |

## 事件配置格式（YAML）

```yaml
event_id: "unique_id"           # 唯一标识
name: "事件名称"
event_type: "gathering|scene"   # 聚集型 / 场景型

trigger:
  trigger_type: "cron|daily"    # 周期 / 每日
  day_of_week: [5]              # cron 时指定（0=周一~6=周日）
  hour: 20
  minute: 0

main_venue: "plaza"             # 主场地 ID

slices:                         # 分片场地（控制并发）
  - slice_id: "main_stage"
    location: "plaza"
    display_name: "广场主舞台"
    capacity: 10

participation:
  required_npcs: ["sparkle"]    # 必到 NPC
  optional_npcs: ["robin"]      # 可选 NPC
  resident_probability: 0.3     # 普通居民汇聚概率

duration_minutes: 60            # 持续时间（游戏内分钟）
pre_notice_minutes: 15          # 提前预告时间
chatter_boost: 0.2              # 事件期间私语概率提升
chatter_topic_hint: "话题提示"
enabled: true
```

## 内置样例事件

| 事件 | 文件 | 触发规则 | 核心 NPC |
|------|------|---------|---------|
| 周六烟火大会 | `fireworks_night.yaml` | 每周六 20:00 | sparkle（主办） |
| 每晚演唱会 | `robin_concert.yaml` | 每日 20:00 | robin（演唱） |
| 学园上课铃 | `school_bell.yaml` | 工作日 8:00 | theresa + 学生 |

学园上课铃包含**逃课事件链**（`TruantEventChain`），配置在 YAML 的 `truant_config` 字段中。

## 事件生命周期

```
时间轴：
  ───[预告]──────[开始]────────[结束]───
       │            │              │
     PRE 阶段    ACTIVE 阶段    POST 阶段
       │            │              │
  ·写日记预告  ·拉起分片群聊  ·结束所有场景
  ·注入期待情绪  ·上调私语概率  ·生成事件摘要
  ·覆盖NPC日程  ·（逃课事件链） ·写入共同记忆
```

## 并发护栏

- 基于 `asyncio.Semaphore` 控制同时运行的 LLM 群聊数
- 默认上限 4（可配置），推荐设为推理槽位数的一半
- 超额请求等待，超时则跳过该分片（不影响其他分片）
- 运行时可通过 `guard.update_max_concurrent(n)` 动态调整

## 降级行为

| 场景 | 降级策略 |
|------|---------|
| LLM 不可用 | 所有文本生成使用模板兜底，系统不崩溃 |
| 世界日记写入失败 | 记录 warning 日志，继续执行 |
| 记忆写入失败 | 记录 warning 日志，继续执行 |
| 情绪注入失败 | 记录 warning 日志，继续执行 |
| 群聊场景启动失败 | 记录 error 日志，跳过该分片 |
| 并发槽获取超时 | 跳过该分片场景，不影响其他分片 |

## 开关控制

```python
# 运行时禁用（不触发新事件，不影响已活跃事件）
event_system.disable()

# 重新启用
event_system.enable()

# 查询状态
event_system.active_events        # 当前活跃事件
event_system.concurrency_stats    # 并发统计
event_system.get_event_status("fireworks_night")  # 指定事件状态
```

## 数据文件

| 文件 | 说明 |
|------|------|
| `src/sensory_world/configs/*.yaml` | 事件配置（可自定义扩展） |

本阶段不产生新的持久化数据文件。事件摘要和记忆通过现有 `world_diary` 和 `npc_memory_store` 写入。

## 测试

```bash
cd sensory_world
pip install -e ".[dev]"
python -m pytest tests/ -v
```

全部 67 个测试可离线运行（LLM、记忆库、事件总线全部 mock）。

---

# 阶段二：邮差系统 + 相册系统

## 模块结构

```
src/sensory_world/
├── postal/                        # 邮差系统（承载 NPC：violet 薇尔莉特）
│   ├── postal_system.py           # PostalSystem 主入口
│   ├── content_checker.py         # 信件内容检查器（不指令铁律）
│   ├── letter_store.py            # 信件 JSONL 持久化
│   └── models.py                  # Letter / OutsideMailbox / PostalConfig
├── album/                         # 相册系统（承载 NPC：march7th 三月七）
│   ├── album_system.py            # PhotoAlbumSystem 主入口
│   ├── photo_store.py             # 照片 JSONL 持久化
│   └── models.py                  # Photo / AlbumConfig
└── configs/
    ├── postal.yaml                # 邮差配置
    └── album.yaml                 # 相册配置

data/
├── postal/letters.jsonl           # 信件存档（append-only）
└── album/photos.jsonl             # 照片存档（append-only）
```

## 快速接入

```python
from sensory_world.postal import PostalSystem, PostalConfig
from sensory_world.album import PhotoAlbumSystem, AlbumConfig

# ---- 邮差系统 ----
postal = PostalSystem(
    llm=llm_client,             # LLMClientProtocol
    memory=npc_memory_store,    # NPCMemoryStoreProtocol
    clock=game_clock,           # GameClockProtocol
    diary=world_diary,          # WorldDiaryProtocol（可选）
    config=PostalConfig(),      # 可选，默认即可
)
await postal.initialize()

# ---- 相册系统 ----
album = PhotoAlbumSystem(
    llm=llm_client,
    memory=npc_memory_store,
    clock=game_clock,
    chatter=chatter_system,     # ChatterProtocol（可选，回忆私语用）
    diary=world_diary,          # 可选
    config=AlbumConfig(),
)
await album.initialize()
```

## 邮差系统挂钩点

| 触发场景 | 调用方式 | 说明 |
|---------|---------|------|
| NPC 委托写信 | `await postal.request_letter(sender_id, recipient_id, reason, event_ref)` | 由 NPC 大脑层/事件驱动，邮差 LLM 代笔 |
| **城外信投入**（外部通道） | `await postal.receive_outside_letter(recipient_id, content, subject)` | 外部系统投入"旧邮筒"，自动过配额+审查 |
| **城外发件箱取走**（外部通道） | `await postal.collect_outbox()` | 外部系统取走 NPC 寄往城外的信 |
| 收信后回信 | `await postal.maybe_reply(original_letter)` | 窗口期内概率回信；城外信回信自动走出城通道 |
| 大事件后 | `await postal.on_event_occurred(event_name, participant_ids, event_ref)` | 概率触发 NPC 寄信往城外（须有事件由头） |

### 城外信箱三条铁律（代码层强制）

1. **稀疏**：外部寄信周配额（默认 3 封/周，`outside_weekly_quota` 可配），超额拒收；NPC 寄往城外必须有事件由头 + 概率触发（`npc_outside_reply_probability`）。
2. **平权**：城外信进入记忆库与普通记忆同权重（confidence 0.9），不打特殊标记、不特殊召回。
3. **不指令**：`LetterContentChecker.check_outside_letter()` 拦截命令式内容（去做/命令/必须给我/我要你/立刻/执行/听从/服从…）与破墙词（用户/管理员/造物主/玩家/系统/程序/NPC…），拒收并留档。

**世界观包装**：城外寄信人统一称"城外的朋友"（sender_id = `outside_friend`），信件无落款；世界日记维护"守护这座城的人"的城市传说。代码与信件文本中严禁破墙词。

> 说明：爱莉希雅（elysia）的信箱在代码中保持空置，未绑定任何特殊收件人或特殊逻辑。

## 相册系统挂钩点

| 触发场景 | 调用方式 | 说明 |
|---------|---------|------|
| **周期事件拍照**（衔接阶段一） | `await album.on_event_photos(event_name, event_ref, location, participants_by_slice, force_photographer)` | 事件 ACTIVE 阶段调用；每事件 1-3 张，三月七概率在场 |
| NPC 自发聚会 | `await album.on_casual_gathering(location, participant_ids)` | 概率拍照 |
| 日常随机 | `await album.maybe_daily_photo(location, participant_ids)` | 低频（默认 2%/tick） |
| **翻看相册**（日程钩子） | `await album.browse_album(npc_id)` | 召回旧照片，概率触发回忆型私语（chatter.trigger_topic） |
| 查询共同照片 | `await album.get_shared_photos(npc_a, npc_b)` | 两 NPC 共同在场的照片（记忆交集） |

### 与阶段一的衔接

在阶段一 `EventRunner` 事件进行中（分片群聊拉起后），增加一行调用即可让三月七拍照：

```python
# EventRunner._run_active 阶段，分片拉起后
await album.on_event_photos(
    event_name=instance.config.name,
    event_ref=instance.instance_id,
    location=instance.config.main_location,
    participants_by_slice=assignments,  # 各分片 NPC 列表
)
```

### 核心价值：共同记忆交集

每张照片给**所有在场者**写入**同一条**共享记忆（含 `co_present` 共同在场者字段）：

> "在「周六烟火大会」上和 robin、eden 合了影，照片在三月七的相册里。……"

未来两 NPC 私语时，可通过 `get_shared_photos(a, b)` 召回共同在场事件，形成跨 NPC 记忆交集。

## 数据文件格式

### `data/postal/letters.jsonl`（每行一封信）

```json
{
  "letter_id": "letter_xxxxxxxx",
  "direction": "npc_to_npc | outside_in | npc_to_outside",
  "sender_id": "robin | outside_friend",
  "recipient_id": "eden | outside_friend",
  "postman_id": "violet",
  "subject": "主题",
  "body": "信件正文",
  "reason": "写信缘由",
  "status": "draft | delivered | pending | rejected",
  "event_ref": "关联事件ID",
  "created_at": "2026-07-18T20:00:00",
  "delivered_at": "2026-07-18T20:05:00",
  "metadata": {}
}
```

可被世界日记与小说管线直接读取。

### `data/album/photos.jsonl`（每行一张照片）

```json
{
  "photo_id": "photo_xxxxxxxx",
  "photographer_id": "march7th",
  "timestamp": "2026-07-18T20:30:00",
  "location": "square",
  "scene_type": "periodic_event | casual | birthday | new_shop | daily",
  "event_name": "周六烟火大会",
  "event_ref": "evt_xxx",
  "participant_ids": ["march7th", "sparkle", "robin"],
  "description": "一句话画面描述（LLM 生成/模板兜底）",
  "metadata": {"photo_index": 1}
}
```

## 配置项（PostalConfig / AlbumConfig）

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `True` | 系统总开关 |
| PostalConfig `letters_file` | `data/postal/letters.jsonl` | 信件存档 |
| PostalConfig `outside_weekly_quota` | `3` | 城外信周配额 |
| PostalConfig `npc_outside_reply_probability` | `0.2` | NPC 寄往城外概率 |
| PostalConfig `reply_probability` | `0.4` | 回信概率 |
| PostalConfig `reply_window_days` | `5` | 回信窗口期 |
| PostalConfig `content_check_enabled` | `True` | 内容审查开关 |
| AlbumConfig `photos_file` | `data/album/photos.jsonl` | 照片存档 |
| AlbumConfig `event_photo_min/max` | `1 / 3` | 每事件拍照张数范围 |
| AlbumConfig `daily_photo_probability` | `0.02` | 日常拍照频率 |
| AlbumConfig `casual_photo_probability` | `0.5` | 聚会拍照概率 |
| AlbumConfig `browse_recall_probability` | `0.6` | 翻看相册触发回忆概率 |

## 降级行为

- **LLM 不可用**：信件正文、照片画面描述均自动降级为模板文本，流程不中断。
- **记忆库/日记/chatter 异常**：单条写入失败仅告警，不影响主流程。
- **审查拒收**：命令式/破墙内容拒收并落档（REJECTED），城外信不消耗配额。
- **系统禁用**：`enabled=False` 时所有入口返回 None/空，不产生任何数据。

## 阶段二测试

```bash
cd sensory_world
python -m pytest tests/test_postal_system.py tests/test_album_system.py \
                 tests/test_content_checker.py tests/test_letter_store.py -v
```

阶段二新增 51 个测试，全项目累计 118 个测试全部可离线通过（LLM、记忆库、事件总线、chatter 全部 mock）。

---

# 阶段三：日历系统 CityCalendar + 新店开张协议

## 模块结构

```
src/sensory_world/calendar/        # 日历系统
├── calendar_system.py             # CityCalendar 主入口（历法/季节/生日/回声/开张）
├── config_loader.py               # calendar.yaml / birthdays.yaml 加载
└── models.py                      # CityDate / Season / SeasonNote / Birthday / CalendarConfig / ShopOpening

src/sensory_world/configs/
├── calendar.yaml                  # 日历/生日链路/回声/开张配置
└── birthdays.yaml                 # 居民生日（month+day 或 day_of_year）
```

## 城市历法规则（游戏历法，自洽简单）

- 城市纪元第 1 天 = 游戏累计天数 `total_days=0`（可配 `epoch_start_total_day`）；
- 周 = 7 天，月 = 30 天，季 = 90 天，年 = 360 天；
- 季节：春（day 1~90）、夏（91~180）、秋（181~270）、冬（271~360）；
- **需主项目确认**：`GameClock.total_days()` —— 现有时间系统若无此接口，可用 `(now - epoch).days` 推导。已在协议中补充声明。

## 快速接入

```python
from sensory_world.calendar import CityCalendar, CalendarConfig

calendar = CityCalendar(
    clock=game_clock,
    memory=npc_memory_store,    # 生日亲近NPC/年度回声召回用（可选）
    diary=world_diary,          # 可选
    chatter=chatter_system,     # 生日祝福/年度回声私语用（可选）
    group_scene=group_scene,    # 开张首日群聊用（可选）
    postal=postal_system,       # 阶段二邮差（生日贺卡/开业邀请函）
    album=album_system,         # 阶段二相册（生日合照/开张首照）
    config=CalendarConfig(),
    birthdays=...,              # 或用 from_config 读 YAML
)
# 推荐：直接从配置文件构造
calendar = CityCalendar.from_config(
    clock=game_clock,
    calendar_config_path="src/sensory_world/configs/calendar.yaml",
    birthdays_config_path="src/sensory_world/configs/birthdays.yaml",
    memory=..., diary=..., chatter=..., group_scene=...,
    postal=postal, album=album,
)
await calendar.initialize()
```

## 挂钩点

| 触发场景 | 调用方式 | 说明 |
|---------|---------|------|
| 主循环每 tick | `await calendar.tick(game_time)` | 跨天时推进历法、处理当日生日/新年 |
| 季节注释 | `calendar.season_note` / `calendar.get_season_hint_text()` | 供事件系统与 chatter 读取天气倾向与行为注释（夏季傍晚出门/冬季室内） |
| **年度回声**（衔接阶段一） | `await calendar.on_periodic_event_start(event_name, participant_ids)` | 周期事件**启动时**调用；第 2 年起召回去年同期记忆注入私语话题 |
| **新店开张** | `await calendar.register_new_shop(npc_id, shop_location, shop_type, visitor_ids)` | 标准化开张流程 |
| 运行期补生日 | `calendar.add_birthday(npc_id, day_of_year)` | 新 NPC 入住时补充 |

### 生日联动链路（当天自动触发，复用阶段二）

1. **邮差最先得知**：`PostalSystem.send_greeting_card(occasion="birthday")` 联名贺卡（"城里的朋友们"），进记忆同权重 0.9；
2. **相册生日合照**：`PhotoAlbumSystem.on_birthday(npc_id, year)` 给在场者写共同记忆；
3. **亲近 NPC 祝福私语**：从寿星记忆 `co_present`/共同在场者召回好友，`chatter.trigger_topic` 注入祝福话题；
4. **世界日记**：`write_entry("calendar", ...)` 记录生日条目。

> 亲近 NPC 名单依赖记忆 metadata 中的 `co_present` / `participant_ids` / `with_npcs` 字段（相册共同记忆已写 `co_present`）。**需主项目确认**记忆 metadata 结构；取不到则静默跳过祝福私语。

### 年度回声

- 仅城市纪元第 2 年起生效（第 1 年无"去年"）；
- 事件启动时对参与者 `memory.recall(npc, "去年 {事件名} 活动 合影")`，命中含事件名/"合影"的记忆则注入话题："去年{事件}的时候——……，今年又到了，和 XX 聊聊去年吧"；
- 无记忆静默跳过，不报错；话题数受 `echo_max_topics` 限制。

### 新店开张协议流程

`register_new_shop(npc_id, shop_location, shop_type, visitor_ids)`：

1. 世界日记开张条目（店铺类型中文名，如"邮差小屋"）；
2. 周边 NPC 首日造访 `group_scene.start_scene(...)`；
3. 摄影 NPC 首张店铺照片 `album.on_new_shop(...)`（给在场者写共同记忆）；
4. 邮差发开业邀请函 `postal.send_greeting_card(occasion="opening", sender_id=店主)`；
5. 同地点防重复注册；返回 `ShopOpening` 记录。

**地点注册**：开张记录 `ShopOpening.to_dict()` 含 `shop_location` / `shop_type`，**需主项目确认**与 `data/city_locations_all.json` 的地点注册格式对接（本模块只产出记录，不直接改地点文件）。

## 邮差/相册新增接口（阶段三复用，非新模块）

| 接口 | 模块 | 用途 |
|------|------|------|
| `postal.send_greeting_card(recipient_id, occasion, reason, sender_id)` | 邮差 | 生日贺卡/开业邀请函；`occasion="birthday"/"opening"`；联名贺卡 sender 用 `city_friends` |
| `album.on_birthday(npc_id, year, location, participant_ids)` | 相册 | 生日合照 |
| `album.on_new_shop(npc_id, shop_location, shop_type, visitor_ids)` | 相册 | 开张首照 |

贺卡同样过内容审查（不指令铁律）、进 `letters.jsonl`（`metadata.card=true`）、记忆同权重。

## 配置项（CalendarConfig）

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `True` | 日历总开关 |
| `epoch_start_total_day` | `0` | 城市纪元起点对应游戏累计天数 |
| `birthday_enabled` | `True` | 生日链路开关 |
| `birthday_card_probability` | `0.9` | 生日贺卡概率 |
| `birthday_photo_probability` | `0.85` | 生日合照概率 |
| `birthday_greeting_chatter_probability` | `0.7` | 祝福私语概率 |
| `echo_enabled` | `True` | 年度回声开关 |
| `echo_memory_per_npc` / `echo_max_topics` | `2` / `3` | 回声召回条数/话题上限 |
| `new_shop_enabled` | `True` | 开张协议开关 |
| `new_shop_visitor_count` | `5` | 首日造访群聊人数 |
| `new_shop_photo_probability` | `0.95` | 开张首照概率 |
| `new_shop_invitation_probability` / `_max` | `0.8` / `6` | 邀请函概率/上限 |
| `shop_type_names` | 见 yaml | 店铺类型中文名映射 |

## 降级行为

- 下游系统（邮差/相册/chatter/群聊/日记/记忆）全部可选注入，缺失或抛异常时对应步骤静默跳过，不影响历法推进与其他链路；
- LLM 不被日历直接调用（贺卡/照片文案由邮差/相册内部 LLM + 模板兜底）；
- 系统禁用时 `tick`/`register_new_shop` 返回 None/空，不产生数据。

---

# 三系统统一集成：总挂钩顺序建议

主循环一个 tick 内，推荐按以下顺序挂钩（全部 `await`，各自独立降级）：

```
每个游戏 tick（game_time）:
│
├─ 1. 【日历】calendar.tick(game_time)
│      · 跨天时推进城市历法、切换季节注释
│      · 当天生日 → 内部触发 邮差贺卡 + 相册合照 + 祝福私语 + 日记
│
├─ 2. 【周期事件】event_system.tick(game_time)
│      · 事件 PRE：日记预告 + 情绪期待注入
│      · 事件启动时 ──→ calendar.on_periodic_event_start(事件名, 参与者)   # 年度回声
│      · 事件 ACTIVE（分片拉起 group_scene，受并发护栏限制）:
│      │     每个分片群聊后 ──→ album.on_event_photos(...)                 # 事件拍照
│      │     事件大场面后   ──→ postal.on_event_occurred(...)              # 事件由头→城外信
│      · 事件 POST：摘要日记 + 参与者共同记忆 + LLM 印象（模板兜底）
│
├─ 3. 【邮差】postal 日常
│      · 处理窗口期回信 maybe_reply()
│      · 城外信箱：外部轮询 receive_outside_letter() / collect_outbox()
│
├─ 4. 【相册】album 日常
│      · maybe_daily_photo()（低频）
│      · NPC 日程"翻看相册" → browse_album()（回忆私语）
│
└─ 5. chatter / 情绪 / 记忆 RAG（现有系统）
       · 季节旁白：calendar.get_season_hint_text() 可注入 chatter 话题倾向

一次性事件（非 tick）:
· 新 NPC 带店铺入住 → calendar.register_new_shop(npc_id, location, shop_type, visitors)
· 外部投信       → postal.receive_outside_letter(recipient_id, content, subject)
```

**关键衔接关系**：
- 日历是"时间刻度"，最先 tick，决定季节与当天生日；
- 周期事件是"大场面"，启动时向日历要回声、进行中向相册要照片、结束后向邮差给由头；
- 邮差与相册是"物证层"，被日历（生日/开张）与事件（现场）共同调用，不主动驱动剧情；
- 共同记忆（相册 `co_present`）是跨系统的记忆交集底座，被年度回声与祝福私语复用。

## 阶段三测试

```bash
cd sensory_world
python -m pytest tests/test_calendar_models.py tests/test_calendar_system.py -v
```

阶段三新增 29 个测试（历法/季节/生日链路/年度回声/开张协议 + 贺卡接口），全项目累计 **147 个测试全部可离线通过**。
