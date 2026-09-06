# AGENTS.md —— 小爱世界扩展模块项目导航

## 项目概览

sensory_world 是一个 Python 异步城市生命模拟系统的扩展模块集合。本仓库包含三个阶段的独立功能模块，阶段一（周期事件系统）与阶段二（邮差系统 + 相册系统）已完成开发。

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
│           ├── album_system.py             # PhotoAlbumSystem 主入口（拍照/共同记忆）
│           ├── photo_store.py              # 照片 JSONL 持久化
│           └── models.py                   # Photo / AlbumConfig
├── tests/
│   ├── __init__.py
│   ├── conftest.py                         # pytest fixtures
│   ├── mocks.py                            # Mock 实现（所有外部依赖）
│   ├── test_models.py                      # 数据模型测试
│   ├── test_event_config.py                # 配置加载测试
│   ├── test_concurrency_guard.py           # 并发护栏测试
│   ├── test_event_runner.py                # 事件执行器测试
│   ├── test_llm_client.py                  # LLM 客户端测试
│   ├── test_scheduler.py                   # 调度器测试
│   ├── test_content_checker.py             # 信件内容检查器测试（阶段二）
│   ├── test_letter_store.py                # 信件存储测试（阶段二）
│   ├── test_postal_system.py               # 邮差系统测试（阶段二）
│   └── test_album_system.py                # 相册系统测试（阶段二）
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

当前全部 118 个测试可离线通过（LLM、记忆库、事件总线、chatter 全部 mock）。

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
  - 核心：每张照片给所有在场者写同一条共享记忆（含 co_present 共同在场者），`get_shared_photos(a,b)` 查询记忆交集
- `photo_store.PhotoStore`：photos.jsonl 持久化

## 代码风格

- 全部中文注释和文档
- asyncio 异步设计
- pydantic v2 数据校验
- dataclass / pydantic 运行时实例
- logging 日志
- 类型注解完整
- 零配置兜底降级（LLM 不可用时不崩溃）

## 阶段规划

- [x] **阶段一**：周期事件系统（PeriodicEventSystem）—— 已完成（67 测试）
- [x] **阶段二**：邮差系统（PostalSystem）+ 相册系统（PhotoAlbumSystem）—— 已完成（新增 51 测试，累计 118）
- [ ] **阶段三**：日历系统（CityCalendar）—— 待开发
