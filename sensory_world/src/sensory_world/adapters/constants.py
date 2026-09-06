"""
适配层并发与接入默认参数。

依据（阶段四勘测事实 + 推理槽位预算）：
  - 主项目 LLM 为 Qwen3-8B，-np 8 共 **8 个推理槽**；
  - 主项目群聊 GroupSceneSystem 每 tick 全局 ≤ 3 条台词，且按 NPC 实时位置
    自动聚类，**每群 ≤ 5 人**（勘测事实 5）；
  - 功能模块自身也会并发发起创意类 LLM 调用（事件摘要/信件/照片描述等）。

因此并发护栏的取值原则：**给主项目常驻的大脑+群聊留出推理槽位**，
功能模块的群聊分片与创意调用必须错峰、限峰。所有数值均可在
data/config/*.yaml 中覆盖，此处仅给出安全默认值。
"""

from __future__ import annotations

from dataclasses import dataclass


# ============================================================
# 并发护栏默认值（注释标明依据）
# ============================================================

# 功能模块内部"事件创意 LLM 调用"（事件摘要/信件正文/照片描述等）
# 的全局信号量：同一时刻最多 2 个。依据：主项目 8 槽中，常驻的大脑
# 对话与群聊台词随时可能占用 5~6 个，功能创意调用若再并发会把槽位
# 打爆；错峰到事件结束后生成摘要/记忆，且限 2 并发，留足余量。
FEATURE_LLM_SEMAPHORE_LIMIT = 2

# 周期事件 group_scene 分片群聊的并发护栏：同一时刻最多 N 个分片群
# 被"波浪式"拉起。阶段一默认 4；接入真实群聊后，主项目群聊自身每
# tick 最多产出 3 条台词，分片群开太多会挤占台词预算，故接入侧取 4。
EVENT_GROUP_SCENE_CONCURRENCY = 4

# 单个分片群的人数上限。依据：主项目自动聚类 **每群 ≤ 5 人**（勘测），
# 超过 5 人会聚成多个自动小群，无法保证落在同一片地点语境，故拆分时
# 严格按 5 人/片切。
WAVE_GROUP_MAX_SIZE = 5

# 同一时刻允许活跃的分片群数量上限（波浪窗口宽度）。
# 依据：主项目全局群聊台词预算 ≤3 条/tick，同时活跃 6 个分片群时，
# 每个群分摊到台词节奏最慢但仍能轮转；再高会导致大部分群"有人没台词"。
WAVE_MAX_ACTIVE_GROUPS = 6

# 波浪节拍：每 2~3 个游戏分钟开放下一批分片群。
# 依据：游戏 1 天 ≈ 4.8 真实分钟、1 天 = 1440 游戏分钟，即约 5 游戏分钟/
# 真实秒；2~3 游戏分钟约半秒内，既能让前一批群聊产生台词，又不至于让
# 参与者久站。取区间，调度时用下限。
WAVE_INTERVAL_MINUTES_MIN = 2
WAVE_INTERVAL_MINUTES_MAX = 3

# 事件系统主循环节流：主循环 10Hz（T7 每秒约 10 次），事件系统不需要
# 这个频率。按"游戏时间每推进 N 分钟"才真正 tick 一次事件调度器。
# 依据：事件最小粒度是"分"（如 20:00 开场），1 游戏分钟级检测足够，
# 既不漏触发又把调度开销降到 1/（N*tick 换算）。
EVENT_TICK_THROTTLE_MINUTES = 5


@dataclass(frozen=True)
class AdapterDefaults:
    """适配层接入默认参数的聚合视图，供部署文档与初始化统一引用。"""

    # 时钟
    minutes_per_game_day: int = 1440
    # 城市纪元锚定：total_game_minutes=0 对应这个真实日期（周一），
    # 用于反推 datetime；仅当主项目 WorldClock 未直接暴露 now 时使用。
    epoch_reference: str = "2024-01-01 00:00:00"

    # 群聊波浪
    group_max_size: int = WAVE_GROUP_MAX_SIZE
    max_active_groups: int = WAVE_MAX_ACTIVE_GROUPS
    wave_interval_minutes: tuple[int, int] = (
        WAVE_INTERVAL_MINUTES_MIN,
        WAVE_INTERVAL_MINUTES_MAX,
    )
    group_scene_concurrency: int = EVENT_GROUP_SCENE_CONCURRENCY

    # LLM
    feature_llm_semaphore: int = FEATURE_LLM_SEMAPHORE_LIMIT
    llm_base_url: str = "http://127.0.0.1:8089/v1/chat/completions"
    llm_model: str = "Qwen3-8B"
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2

    # 节流
    event_tick_throttle_minutes: int = EVENT_TICK_THROTTLE_MINUTES


DEFAULT_DEFAULTS = AdapterDefaults()
