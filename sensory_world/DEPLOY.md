# 部署运维手册（DEPLOY.md）

sensory_world 三个功能系统（周期事件 / 邮差相册 / 日历）+ 阶段四集成适配层，
接入小爱世界主项目的部署指南。

---

## 1. 文件清单与拷贝目标

主项目根目录记为 `world/`。建议把 sensory_world 包放到不与主项目冲突的路径：

```
world/features/sensory_world/            # ← 包目标路径（features/ 为新建目录）
├── __init__.py
├── protocols.py                        # 三阶段功能协议
├── llm_client.py                       # SafeLLMClient / FallbackLLMClient
├── periodic_event/                     # 阶段一：周期事件系统
├── postal/                             # 阶段二：邮差系统
├── album/                              # 阶段二：相册系统
├── calendar/                           # 阶段三：日历系统
└── adapters/                           # 阶段四：主项目集成适配层
    ├── clock_adapter.py
    ├── memory_adapter.py
    ├── emotion_adapter.py
    ├── group_scene_adapter.py
    ├── diary_adapter.py
    ├── llm_adapter.py
    ├── schedule_adapter.py
    ├── constants.py
    └── host_interfaces.py
```

拷贝命令（示例）：

```bash
mkdir -p world/features
cp -r sensory_world/src/sensory_world world/features/sensory_world
# 让 features 成为可导入包
touch world/features/__init__.py
```

导入方式（主项目内）：
```python
from features.sensory_world.adapters import RealClockAdapter, build_safe_llm  # 等
```

> 依赖：`pydantic`、`PyYAML`（主项目已装 PyYAML，勘测事实 10）。无需额外 HTTP 库
> （LLM 适配器用标准库 urllib）。

---

## 2. 新建数据目录与配置

### 2.1 数据目录（生产，JSONL 持久化）

```bash
mkdir -p world/data/postal      # 信件存档 letters.jsonl（运行时数据）
mkdir -p world/data/album       # 照片存档 photos.jsonl（运行时数据）
mkdir -p world/data/events      # 事件运行时数据（事件实例/状态，运行时写入）
mkdir -p world/data/config      # 功能模块配置文件（events/postal/calendar/birthdays.yaml）
mkdir -p world/data/diary       # 世界日记（主项目已有则复用，勿清空）
```

### 2.2 配置文件（放 world/data/config/）

生产配置全 JSON（勘测事实 10），但功能模块配置用 YAML（PyYAML 已装，零冲突）。
样例 4 个 YAML：

**events.yaml**（周期事件）—— 事件定义可沿用仓库 `src/sensory_world/configs/` 下
`fireworks_night.yaml / robin_concert.yaml / school_bell.yaml`，复制到 data/config/ 后
按需调整 `enabled`、分片地点 id（须与 city_locations_all.json 对齐）。

**postal.yaml**（邮差）关键项：
```yaml
enabled: true
postman_npc_id: violet
letters_file: data/postal/letters.jsonl
outside_weekly_quota: 3          # 城外信周配额（稀疏铁律）
reply_probability: 0.4
reply_window_days: 5
npc_outside_reply_probability: 0.2
content_check_enabled: true      # 不指令铁律开关
```

**album.yaml**（相册）关键项：
```yaml
enabled: true
photographer_npc_id: march7th
photos_file: data/album/photos.jsonl
event_photo_min: 1
event_photo_max: 3               # 每事件 1-3 张
daily_photo_probability: 0.02    # 日常低频
casual_photo_probability: 0.5
browse_recall_probability: 0.6
```

**calendar.yaml + birthdays.yaml**（日历/生日）：
```yaml
# calendar.yaml
enabled: true
epoch_start_total_day: 0
birthday_enabled: true
birthday_card_probability: 0.9
birthday_photo_probability: 0.85
birthday_greeting_chatter_probability: 0.7
echo_enabled: true               # 年度回声
echo_memory_per_npc: 2
echo_max_topics: 3
new_shop_enabled: true
new_shop_visitor_count: 5
new_shop_photo_probability: 0.95
new_shop_invitation_probability: 0.8
new_shop_invitation_max: 6
```
```yaml
# birthdays.yaml —— 城市内生日（day_of_year 或 month+day），未配置的 NPC 不触发
birthdays:
  - npc_id: march7th
    name: 三月七
    month: 3
    day: 7
  - npc_id: violet
    name: 薇尔莉特
    day_of_year: 120
```

---

## 3. 接入初始化与挂钩

按 `PATCHES.md`：
1. 在主循环初始化处创建适配器 + 三大功能系统，挂到 `world.sensory_features`；
2. 在 `core/simulation_loop.py` t7 挂钩桩（L793-794）调用，含 `MinuteThrottle(5)` 节流；
3. 注入 `event_bus`（主项目事件总线，`publish(event_name, payload)` 方法）到
   `RealGroupSceneAdapter` 和 `RealScheduleAdapter`。事件聚集移动通过
   `event_bus.publish("schedule.npc_command", payload)` 执行，无需绑定私有 move_fn。

LLM 接入：`build_safe_llm()` 默认打 `http://127.0.0.1:8089/v1/chat/completions`，
模型 `Qwen3-8B`；失败自动重试 + 模板兜底，绝不抛异常打断模拟。

---

## 4. 并发参数默认值（依据见 adapters/constants.py）

| 参数 | 默认值 | 依据 |
|------|--------|------|
| 事件创意 LLM 信号量 | **2** | 主项目 8 推理槽，常驻大脑+群聊占 5~6，创意调用错峰且限 2 |
| 分片群聊并发护栏 | **4** | 阶段一默认；接入后受群聊台词预算约束 |
| 单群人数上限 | **5** | 主项目自动聚类**每群 ≤5 人**（勘测事实 5） |
| 同时分片群上限 | **6** | 群聊全局 ≤3 台词/tick，6 群可轮转不失声 |
| 波浪节拍 | **2~3 游戏分钟/批** | 5 游戏分钟≈1 真实秒，半秒放一批，既出声不冷场 |
| 事件 tick 节流 | **5 游戏分钟** | 事件粒度到分，10Hz 空转浪费 |

> 事件创意 LLM 调用（摘要/信件/照片描述）统一**错峰到事件结束后**生成，且走
> 信号量限 2 的 `SafeLLMClient`，避免与开场群聊抢槽。

---

## 5. 功能总开关与单系统开关

| 开关 | 位置 | 作用 |
|------|------|------|
| 总开关 | 不挂钩 t7 / 不创建 sensory_features | 一行不接入即全停 |
| 周期事件 | events 配置 `enabled: false` 或 PeriodicEventSystem(enabled=False) | 事件不触发 |
| 邮差 | postal.yaml `enabled: false` | 信件/城外信箱全停 |
| 城外信箱 | postal.yaml `outside_weekly_quota: 0` | 仅关外部通道，NPC 间信件保留 |
| 内容审查 | postal.yaml `content_check_enabled: false` | 不建议关（铁律） |
| 相册 | album.yaml `enabled: false` | 不拍照、不写合影记忆 |
| 日历 | calendar.yaml `enabled: false` | 历法/生日/回声/开张全停 |
| 生日 | calendar.yaml `birthday_enabled: false` | 只关生日链路 |
| 年度回声 | calendar.yaml `echo_enabled: false` | 只关跨期回声 |
| 新店开张 | calendar.yaml `new_shop_enabled: false` | 只关开张协议 |

---

## 6. 降级行为验证清单（上线后逐项确认）

- [ ] **LLM 全挂**（停 8089）：事件/信件/照片仍跑，文案为模板文本，城市不卡死、无异常堆栈。
- [ ] **记忆库异常**：单条记忆写入失败仅告警，事件主流程继续。
- [ ] **情绪系统缺失**：注入期待/相处记录失败静默跳过。
- [ ] **群聊移动函数未绑定**：日志告警"未绑定移动命令"，不崩；事件其余步骤（日记/记忆）正常。
- [ ] **日记无公开方法**：自动在 data/diary/day-XXXX.md 追加 `【城市记事】` 行，格式与原生日志一致。
- [ ] **城外信配额用尽**：第 4 封/周被拒收并落 REJECTED 记录。
- [ ] **城外信含命令式/破墙词**：被内容检查器拒收，不进记忆。
- [ ] **新居民 schedule_book=None**：事件时通过显式移动仍能到场。
- [ ] **分片超 5 人**：自动拆片，无任何分片 >5 人；同时活跃分片 ≤6。
- [ ] **周历**：游戏第 6 天（total_days=5）weekday()==5（周六），烟火大会在周六触发。

---

## 7. 回滚步骤

```bash
# 1) 摘除挂钩：删除 core/simulation_loop.py 中 sensory_world 挂钩代码块
#    （或恢复备份）
cp core/simulation_loop.py.bak core/simulation_loop.py   # 若打过补丁一
cp core/world_diary.py.bak     core/world_diary.py       # 若打过补丁二

# 2) 移除功能包
rm -rf world/features/sensory_world

# 3) （可选）清理功能数据；建议保留以便再次启用
#    data/postal/  data/album/  data/events/  data/config/*.yaml
```

回滚后主项目恢复到接入前状态；功能模块写入的记忆条目是高信度事实记忆，
即使移除功能包也不会影响主项目运行（RAG 只会少召回这些条目）。

---

## 8. 测试

```bash
cd sensory_world
pip install -e ".[dev]"
python -m pytest tests/ -q          # 全量 181 个测试离线通过
# 适配层专项：
python -m pytest tests/test_clock_adapter.py tests/test_memory_adapter.py \
                 tests/test_other_adapters.py tests/test_llm_adapter.py \
                 tests/test_group_schedule_adapter.py -v
```

适配层测试全部 mock 主项目接口（FakeWorldClock/FakeMemoryStore/FakeEmotionSystem/
FakeScheduleBook/urllib 拦截），不连真实服务器。
