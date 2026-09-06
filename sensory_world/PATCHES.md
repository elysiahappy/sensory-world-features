# 主项目最小补丁说明（PATCHES.md）

> 适配层（`sensory_world/adapters/`）本身**不改动主项目任何文件**即可降级运行。
> 以下两处补丁是为了让集成更顺、让世界日记条目与主项目原生日记完全同构。
> 所有补丁都给出**文件:行号**、补丁内容与理由；不打补丁时的替代方案也一并写明。

打补丁前务必备份：
```bash
cp core/simulation_loop.py core/simulation_loop.py.bak
cp core/world_diary.py     core/world_diary.py.bak
```

---

## 补丁一（必需）：T7 事件挂钩 + 按游戏分钟节流

**位置**：`core/simulation_loop.py` 的 `t7_event_condition_check`，预留空事件挂钩桩
（勘测：L793-794）。

**为什么需要**：功能模块（周期事件/日历）需要在主循环里被驱动；但主循环是 10Hz，
事件系统不需要这个频率，必须按"游戏时间分钟变化"节流，否则每秒空转十次。

**补丁内容**（示意；变量名请与主项目实际保持一致）：

```python
# core/simulation_loop.py  —— t7_event_condition_check 内，L793-794 预留桩处
def t7_event_condition_check(self):
    # ... 原有逻辑 ...

    # === sensory_world 功能模块挂钩（周期事件 / 日历） ===
    # features 句柄在主循环初始化时创建（见 DEPLOY.md「接入初始化」）
    feats = getattr(self.world, "sensory_features", None)
    if feats is not None:
        # 节流：事件/日历系统按游戏分钟驱动，无需 10Hz。
        # total_game_minutes 是 WorldClock 唯一真相源（整数）。
        cur_minutes = self.world.clock.total_game_minutes
        if feats["throttle"].should_fire(cur_minutes):
            # 群聊波浪推进（每节拍放一批分片群）
            await feats["group_scene"].advance_wave(cur_minutes)
            # 日历 tick（跨天推进历法、生日、季节注释）
            await feats["calendar"].tick(feats["clock_adapter"].now_sync())
            # 周期事件 tick
            await feats["events"].tick(feats["clock_adapter"].now_sync())
    # === 挂钩结束 ===
```

配套初始化（在 SimulationLoop / World 初始化处，一次性）：

```python
from sensory_world.adapters import (
    RealClockAdapter, RealMemoryAdapter, RealEmotionAdapter,
    RealGroupSceneAdapter, RealChatterAdapter, RealDiaryAdapter,
    RealLLMAdapter, RealScheduleAdapter, MinuteThrottle, build_safe_llm,
)
from sensory_world.periodic_event import PeriodicEventSystem
from sensory_world.postal import PostalSystem
from sensory_world.album import PhotoAlbumSystem
from sensory_world.calendar import CityCalendar

# 1) 适配器
clock_adapter = RealClockAdapter(self.clock)                         # WorldClock
memory_adapter = RealMemoryAdapter(self.memory_store, name_resolver=self._npc_name)
emotion_adapter = RealEmotionAdapter(self.emotion)
schedule_adapter = RealScheduleAdapter(
    schedule_book=getattr(self, "schedule_book", None),
    event_bus=self.event_bus,   # 主项目事件总线，发布 schedule.npc_command 事件
)
group_scene_adapter = RealGroupSceneAdapter(event_bus=self.event_bus, memory_adapter=memory_adapter)
chatter_adapter = RealChatterAdapter(memory_adapter, emotion_adapter)
diary_adapter = RealDiaryAdapter(world_diary=self.diary, clock_adapter=clock_adapter)
llm = build_safe_llm()   # 默认打 127.0.0.1:8089，失败模板兜底

# 2) 三大功能系统
events  = PeriodicEventSystem(llm=llm, memory=memory_adapter, clock=clock_adapter,
                              diary=diary_adapter, emotion=emotion_adapter,
                              group_scene=group_scene_adapter, chatter=chatter_adapter)
postal  = PostalSystem(llm=llm, memory=memory_adapter, clock=clock_adapter, diary=diary_adapter)
album   = PhotoAlbumSystem(llm=llm, memory=memory_adapter, clock=clock_adapter,
                           chatter=chatter_adapter, diary=diary_adapter)
calendar = CityCalendar.from_config(clock=clock_adapter, memory=memory_adapter,
                                    diary=diary_adapter, chatter=chatter_adapter,
                                    group_scene=group_scene_adapter,
                                    postal=postal, album=album)

self.world.sensory_features = {
    "clock_adapter": clock_adapter,
    "group_scene": group_scene_adapter,
    "calendar": calendar,
    "events": events,
    "throttle": MinuteThrottle(interval_minutes=5),   # 每 5 游戏分钟驱动一次
}
```

**⚠ 需主项目确认**：`self._move_npc(npc_id, location_id, reason)` —— 勘测确定"事件聚集
必须显式下移动命令"（新居民 schedule_book=None 不参与日程移动），但**移动函数的确切
名称未在勘测中给出**。请绑定主项目真实的 NPC 移动 API（形如
`async def move_npc_to(npc_id, location_id)`），包一层 `async def _move_npc(npc_id, loc, reason="")`。

**节流依据**：`MinuteThrottle(interval_minutes=5)`；事件最小粒度是"分"（如 20:00 开场），
5 游戏分钟检测一次既不漏触发，又把调度频率降到 10Hz 的几十分之一。

**回滚**：删除上面挂钩代码块即可，主循环恢复原状。

---

## 补丁二（可选）：WorldDiary 公开写条目方法

**位置**：`core/world_diary.py` 的 `WorldDiary` 类内。

**背景**：勘测事实 7——WorldDiary 只订阅 `npc.brain.thought` 写 `data/diary/day-XXXX.md`，
**没有公开写条目方法**。功能模块需要写"事件预告/摘要/生日/开张"等条目。

### 方案 A（默认，零补丁，推荐先上线）
适配层 `RealDiaryAdapter` 已内置：检测不到公开写方法时，**直接以一致格式追加**
`data/diary/day-XXXX.md`（`- HH:MM 【城市记事】...`），文件不存在会创建。
- 优点：不动主项目，即插即用，回滚零成本。
- 缺点：绕过 WorldDiary 的内部状态（若其维护内存索引则不感知）；多进程写同一文件需注意（本项目单进程，无此问题）。

### 方案 B（可选补丁，让日记条目与主项目原生完全同构）
在 `WorldDiary` 类中补一个公开方法，把功能条目纳入原生写盘通道：

```python
# core/world_diary.py —— WorldDiary 类内新增
def add_event_entry(self, text: str, category: str = "event") -> None:
    """
    供 sensory_world 功能模块写入城市记事（事件预告/摘要/生日/开张等）。
    与 npc.brain.thought 走同一套写盘逻辑，确保 day-XXXX.md 格式一致。
    """
    # 复用本类已有的"取当前 day 文件 + 追加一行"私有逻辑（方法名以实际为准）
    self._write_diary_line(f"【城市记事·{category}】{text}")
```

> 适配层会**自动探测** `add_event_entry / write_event_entry / append_entry / add_entry`
> 任一方法，探测到就走方案 B，否则走方案 A，无需改适配层代码。

- 优点：条目与原生日记同一通道、同一格式、同一索引。
- 缺点：需改主项目、需理解 WorldDiary 内部写盘函数名（`_write_diary_line` 为占位，以实际为准）。

**回滚**：删除新增方法即可；适配层自动退回方案 A。

---

## 两处"需主项目确认"的接口清单

| 接口 | 用途 | 现状 | 适配层处理 |
|------|------|------|-----------|
| event_bus 事件总线 | 事件分片把参与者移到分片地点触发自动聚类 | 主项目通过 `event_bus.publish("schedule.npc_command", payload)` 广播移动命令 | `event_bus` 注入，发布标准 payload（npc_id/target_location/anchor_id/activity/lateness_policy/schedule_condition） |
| WorldClock 边界回调注册器名 | on_new_hour/day/season/year | 事件名已知，注册器名待定 | 容错探测常见名，失败用轮询 `poll_boundaries()` 兜底 |
| WorldDiary 公开写方法 | 写城市记事 | 不存在 | 方案 A 直接追加文件 / 方案 B 打补丁 |
| ScheduleEntry 构造字段 | 写"翻看相册"等活动 | activity 自由字符串已知 | `entry_factory` 可选注入，否则传 dict |
| 记忆 metadata 的 co_present | 年度回声/祝福召回好友 | 适配器已把人名写进正文 | 正文含人名保证 RAG 命中，metadata 仅辅助 |
