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
