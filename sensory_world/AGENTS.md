# AGENTS.md —— 小爱世界扩展模块项目导航

## 项目概览

sensory_world 是一个 Python 异步城市生命模拟系统的扩展模块集合。本仓库包含三个阶段的独立功能模块，阶段一（周期事件系统）、阶段二（邮差系统 + 相册系统）、阶段三（日历系统 + 新店开张协议）均已完成开发。

- **语言**：Python 3.11+
- **异步框架**：asyncio
- **数据校验**：pydantic v2
- **配置格式**：YAML
- **测试框架**：pytest + pytest-asyncio

## 目录结构

```
sensory_world/
├── pyproject.toml                          # 项目配置与依赖
├── INTEGRATION.md                          # 集成说明文档
├── AGENTS.md                               # 本文件
├── src/
│   └── sensory_world/
│       ├── __init__.py                     # 包入口
│       ├── protocols.py                    # 协议接口定义（所有外部依赖抽象）
│       ├── llm_client.py                   # LLM 客户端（SafeLLMClient + 降级）
│       ├── configs/                        # 配置 YAML
│       │   ├── fireworks_night.yaml        # 周六烟火大会
│       │   ├── robin_concert.yaml          # 每晚演唱会
│       │   ├── school_bell.yaml            # 学园上课铃（含逃课事件链）
│       │   ├── postal.yaml                 # 邮差系统配置
│       │   └── album.yaml                  # 相册系统配置
│       ├── periodic_event/                 # 阶段一：周期事件系统
│       │   ├── __init__.py                 # 模块入口（导出所有公开 API）
│       │   ├── models.py                   # 数据模型（EventConfig, EventInstance 等）
│       │   ├── event_config.py             # 配置加载器（YAML → pydantic）
│       │   ├── concurrency_guard.py        # 并发护栏（Semaphore 控制）
│       │   ├── event_runner.py             # 事件执行器（PRE/ACTIVE/POST 三阶段）
│       │   └── scheduler.py                # 主调度器（PeriodicEventSystem）
│       ├── postal/                         # 阶段二：邮差系统（NPC：violet 薇尔莉特）
│       │   ├── __init__.py
│       │   ├── postal_system.py            # PostalSystem 主入口（信件/城外信箱/回信）
│       │   ├── content_checker.py          # 信件内容检查器（不指令铁律 + 破墙词）
│       │   ├── letter_store.py             # 信件 JSONL 持久化
│       │   └── models.py                   # Letter / OutsideMailbox / PostalConfig
│       └── album/                          # 阶段二：相册系统（NPC：march7th 三月七）
│           ├── __init__.py
│           ├── album_system.py             # PhotoAlbumSystem 主入口（拍照/共同记忆/翻看相册/生日合照/开张首照）
│           ├── photo_store.py              # 照片 JSONL 持久化
│           └── models.py                   # Photo / AlbumConfig / PhotoSceneType
│       ├── calendar/                       # 阶段三：日历系统（CityCalendar）
│           ├── __init__.py
│           ├── calendar_system.py          # CityCalendar 主入口（历法/季节注释/生日链路/年度回声/开张协议）
│           ├── config_loader.py            # calendar.yaml / birthdays.yaml 加载
│           └── models.py                   # CityDate / Season / SeasonNote / Birthday / CalendarConfig / ShopOpening
│       ├── adapters/                       # 阶段四：主项目（小爱世界）集成适配层
│       │   ├── __init__.py                 # 导出全部 Real*Adapter
│       │   ├── constants.py                # 并发默认值（信号量2/群≤5/片≤6/波浪2-3分/节流5分）
│       │   ├── host_interfaces.py          # 主项目真实接口形状 Protocol（勘测事实，不 import 主项目）
│       │   ├── clock_adapter.py            # RealClockAdapter（分钟换算/自维护周历/回调桥接/MinuteThrottle）
│       │   ├── memory_adapter.py           # RealMemoryAdapter（_append_memory 高信度/共同记忆正文含人名/recall 异步包装）
│       │   ├── emotion_adapter.py          # RealEmotionAdapter（全同步情绪方法 async 薄包装）
│       │   ├── group_scene_adapter.py      # RealGroupSceneAdapter（移动调度成群+波浪状态机）/ RealChatterAdapter（话题走记忆正文）
│       │   ├── diary_adapter.py            # RealDiaryAdapter（公开方法优先，否则追加 day-XXXX.md）
│       │   ├── llm_adapter.py              # RealLLMAdapter（OpenAI 8089，urllib 零依赖，重试超时返空串）
│       │   └── schedule_adapter.py         # RealScheduleAdapter（自由活动写入 + 显式移动聚集）
│       └── configs/                        # YAML 配置（事件/邮差/相册/日历/生日）
│           ├── fireworks_night.yaml ...    # 阶段一事件配置
│           ├── postal.yaml / album.yaml    # 阶段二配置
│           └── calendar.yaml / birthdays.yaml  # 阶段三配置
├── PATCHES.md                              # 阶段四：主项目两个最小补丁（T7 挂钩 + WorldDiary 公开方法）
├── DEPLOY.md                               # 阶段四：部署运维手册（文件清单/目录/开关/回滚/降级验证）
├── tests/
│   ├── __init__.py
│   ├── conftest.py                         # pytest fixtures
│   ├── mocks.py                            # Mock 实现（功能模块外部依赖）
│   ├── adapter_fakes.py                    # 阶段四：主项目接口 mock（FakeWorldClock/FakeMemoryStore 等）
│   ├── test_models.py                      # 数据模型测试
│   ├── test_event_config.py                # 配置加载测试
│   ├── test_concurrency_guard.py           # 并发护栏测试
│   ├── test_event_runner.py                # 事件执行器测试
│   ├── test_llm_client.py                  # LLM 客户端测试
│   ├── test_scheduler.py                   # 调度器测试
│   ├── test_content_checker.py             # 信件内容检查器测试（阶段二）
│   ├── test_letter_store.py                # 信件存储测试（阶段二）
│   ├── test_postal_system.py               # 邮差系统测试（阶段二，含贺卡接口）
│   ├── test_album_system.py                # 相册系统测试（阶段二）
│   ├── test_calendar_models.py             # 历法/季节/生日模型测试（阶段三）
│   ├── test_calendar_system.py             # 日历系统/生日链路/年度回声/开张协议测试（阶段三）
│   ├── test_clock_adapter.py               # 时钟适配/周历/节流/边界回调测试（阶段四）
│   ├── test_memory_adapter.py              # 记忆适配/共同记忆正文/召回包装测试（阶段四）
│   ├── test_other_adapters.py              # 情绪/闲聊/日记适配测试（阶段四）
│   ├── test_llm_adapter.py                 # LLM 适配/重试/降级测试（阶段四）
│   └── test_group_schedule_adapter.py      # 波浪分片调度/日程移动测试（阶段四）
└── data/                                   # 运行时数据目录（运行时自动生成）
    ├── postal/letters.jsonl                # 信件存档（append-only）
    └── album/photos.jsonl                  # 照片存档（append-only）
```

## 构建与测试命令

```bash
# 安装依赖（含开发依赖）
pip install -e ".[dev]"

# 运行全部测试
python -m pytest tests/ -v

# 运行特定模块测试
python -m pytest tests/test_postal_system.py -v

# 运行并显示覆盖率
python -m pytest tests/ --cov=sensory_world --cov-report=term-missing
```

当前全部 147 个测试可离线通过（LLM、记忆库、事件总线、chatter 全部 mock）。

## 核心模块说明

### protocols.py —— 协议接口
- 定义所有与现有系统交互的 Protocol 接口
- 主项目需实现这些 Protocol 并注入
- 包含：GameClockProtocol, WorldDiaryProtocol, NPCMemoryStoreProtocol, NPCEmotionProtocol, GroupSceneProtocol, ChatterProtocol（含 trigger_chatter / trigger_topic）, ScheduleBookProtocol, LLMClientProtocol

### llm_client.py —— LLM 客户端
- `SafeLLMClient`：包装真实 LLM，失败时自动降级
- `FallbackLLMClient`：关键词匹配的模板回复
- 所有模块通过 SafeLLMClient 调用 LLM

### periodic_event/ —— 阶段一：周期事件系统
- `scheduler.PeriodicEventSystem`：入口类，主项目调用 `tick(game_time)` 驱动
- `event_runner.EventRunner`：处理 PRE/ACTIVE/POST 三阶段逻辑；`TruantEventChain` 逃课事件链
- `concurrency_guard.ConcurrencyGuard`：基于 Semaphore 的并发控制，防 LLM 槽位打爆

### postal/ —— 阶段二：邮差系统（NPC：violet）
- `postal_system.PostalSystem`：信件链路、城外信箱、回信链路主入口
  - `request_letter()` NPC 委托写信；`receive_outside_letter()` 城外信投入；`collect_outbox()` 城外发件箱取走；`maybe_reply()` 回信
  - 城外信箱三铁律代码层强制：稀疏（周配额）、平权（记忆同权重）、不指令（内容审查）
- `content_checker.LetterContentChecker`：命令式内容 + 破墙词拦截
  - `check()` 普通信件；`check_outside_letter()` 城外信（额外查破墙词）
- `letter_store.LetterStore`：letters.jsonl 持久化（append-only）
- 世界观：城外寄信人统称"城外的朋友"（sender_id=outside_friend），无落款，维护"守护这座城的人"传说；严禁破墙词

### album/ —— 阶段二：相册系统（NPC：march7th）
- `album_system.PhotoAlbumSystem`：拍照、共同记忆、翻看相册主入口
  - `on_event_photos()` 周期事件拍照（衔接阶段一）；`on_casual_gathering()` 聚会；`maybe_daily_photo()` 日常；`browse_album()` 翻看相册触发回忆私语
  - `on_birthday()` 生日合照、`on_new_shop()` 开张首照（阶段三日历复用）
  - 核心：每张照片给所有在场者写同一条共享记忆（含 co_present 共同在场者），`get_shared_photos(a,b)` 查询记忆交集
- `photo_store.PhotoStore`：photos.jsonl 持久化

### postal 贺卡接口（阶段三复用）
- `PostalSystem.send_greeting_card(recipient_id, occasion, reason, sender_id)`：生日贺卡/开业邀请函，occasion=birthday/opening；联名贺卡 sender 用 city_friends；贺卡同样过内容审查、进 letters.jsonl、记忆同权重 0.9

### calendar/ —— 阶段三：日历系统（CityCalendar）
- `calendar_system.CityCalendar`：主入口，`tick(game_time)` 跨天推进；`from_config()` 读 YAML
  - 历法：CityDate（年/季/月/周/天，年=360天/季=90天）；SeasonNote 季节注释（天气倾向+行为注释），`get_season_hint_text()` 供 chatter
  - 生日链路：`_run_birthday_chain()` 邮差贺卡→相册合照→亲近NPC祝福私语→日记；同 NPC 同年幂等
  - 年度回声：`on_periodic_event_start(事件名, 参与者)` 第2年起召回去年同期记忆注入私语；无记忆静默跳过
  - 新店开张：`register_new_shop(npc_id, shop_location, shop_type, visitor_ids)` 日记→group_scene→开张首照→开业邀请函；同地点防重
- `models`：CityDate / Season / SeasonNote / Birthday / CalendarConfig / ShopOpening；`parse_day_of_year(月,日)`
- 需主项目确认：`GameClock.total_days()`（已在协议声明）、记忆 metadata 的 co_present 字段、新店与 data/city_locations_all.json 地点注册对接

## 代码风格

- 全部中文注释和文档
- asyncio 异步设计
- pydantic v2 数据校验
- dataclass / pydantic 运行时实例
- logging 日志
- 类型注解完整
- 零配置兜底降级（LLM 不可用时不崩溃）
- 下游系统全部可选注入，缺失即降级跳过

## 阶段规划

- [x] **阶段一**：周期事件系统（PeriodicEventSystem）—— 已完成（67 测试）
- [x] **阶段二**：邮差系统（PostalSystem）+ 相册系统（PhotoAlbumSystem）—— 已完成（新增 51 测试，累计 118）
- [x] **阶段三**：日历系统（CityCalendar）+ 新店开张协议 —— 已完成（新增 29 测试，累计 147）
