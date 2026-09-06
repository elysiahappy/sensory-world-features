# AGENTS.md —— 小爱世界扩展模块项目导航

## 项目概览

sensory_world 是一个 Python 异步城市生命模拟系统的扩展模块集合。本仓库包含三个阶段的独立功能模块，阶段一（周期事件系统）已完成开发。

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
│       ├── configs/                        # 事件配置 YAML
│       │   ├── fireworks_night.yaml        # 周六烟火大会
│       │   ├── robin_concert.yaml          # 每晚演唱会
│       │   └── school_bell.yaml            # 学园上课铃（含逃课事件链）
│       └── periodic_event/                 # 阶段一：周期事件系统
│           ├── __init__.py                 # 模块入口（导出所有公开 API）
│           ├── models.py                   # 数据模型（EventConfig, EventInstance 等）
│           ├── event_config.py             # 配置加载器（YAML → pydantic）
│           ├── concurrency_guard.py        # 并发护栏（Semaphore 控制）
│           ├── event_runner.py             # 事件执行器（PRE/ACTIVE/POST 三阶段）
│           └── scheduler.py               # 主调度器（PeriodicEventSystem）
├── tests/
│   ├── __init__.py
│   ├── conftest.py                         # pytest fixtures
│   ├── mocks.py                            # Mock 实现（所有外部依赖）
│   ├── test_models.py                      # 数据模型测试（14 个）
│   ├── test_event_config.py                # 配置加载测试（10 个）
│   ├── test_concurrency_guard.py           # 并发护栏测试（10 个）
│   ├── test_event_runner.py                # 事件执行器测试（13 个）
│   ├── test_llm_client.py                  # LLM 客户端测试（8 个）
│   └── test_scheduler.py                   # 调度器测试（12 个）
└── data/                                   # 运行时数据目录（阶段二使用）
    └── postal/                             # 邮差系统数据（预留）
```

## 构建与测试命令

```bash
# 安装依赖（含开发依赖）
pip install -e ".[dev]"

# 运行全部测试
python -m pytest tests/ -v

# 运行特定模块测试
python -m pytest tests/test_scheduler.py -v

# 运行并显示覆盖率
python -m pytest tests/ --cov=sensory_world --cov-report=term-missing
```

## 核心模块说明

### protocols.py —— 协议接口
- 定义所有与现有系统交互的 Protocol 接口
- 主项目需实现这些 Protocol 并注入
- 包含：GameClockProtocol, WorldDiaryProtocol, NPCMemoryStoreProtocol, NPCEmotionProtocol, GroupSceneProtocol, ChatterProtocol, ScheduleBookProtocol, LLMClientProtocol

### llm_client.py —— LLM 客户端
- `SafeLLMClient`：包装真实 LLM，失败时自动降级
- `FallbackLLMClient`：关键词匹配的模板回复
- 所有模块通过 SafeLLMClient 调用 LLM

### periodic_event/scheduler.py —— 主调度器
- `PeriodicEventSystem`：入口类，主项目调用 `tick(game_time)` 驱动
- 管理事件配置加载、触发判断、生命周期推进
- 支持运行时启用/禁用

### periodic_event/event_runner.py —— 事件执行器
- `EventRunner`：处理 PRE/ACTIVE/POST 三阶段逻辑
- `TruantEventChain`：学园上课铃的逃课事件链子模块

### periodic_event/concurrency_guard.py —— 并发护栏
- `ConcurrencyGuard`：基于 Semaphore 的并发控制
- 防止 LLM 推理槽位被打爆
- 支持动态调整上限

## 代码风格

- 全部中文注释和文档
- asyncio 异步设计
- pydantic v2 数据校验
- dataclass 运行时实例
- logging 日志
- 类型注解完整
- 零配置兜底降级（LLM 不可用时不崩溃）

## 阶段规划

- [x] **阶段一**：周期事件系统（PeriodicEventSystem）—— 已完成
- [ ] **阶段二**：邮差系统 + 相册系统 —— 待开发
- [ ] **阶段三**：日历系统（CityCalendar）—— 待开发
