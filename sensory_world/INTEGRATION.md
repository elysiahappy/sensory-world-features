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
