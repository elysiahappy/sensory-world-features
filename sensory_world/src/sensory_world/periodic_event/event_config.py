"""
周期事件系统 —— 配置加载器

从 YAML 文件加载事件配置，支持目录批量加载和单文件加载。
配置经过 pydantic 校验，确保数据完整性。
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from sensory_world.periodic_event.models import EventConfig

logger = logging.getLogger(__name__)

# 默认配置目录（包内自带样例）
_DEFAULT_CONFIG_DIR = Path(__file__).parent.parent / "configs"


def load_event_config(file_path: str | Path) -> EventConfig:
    """
    从单个 YAML 文件加载事件配置。

    :param file_path: YAML 文件路径
    :return: 校验后的 EventConfig 对象
    :raises ValueError: 配置校验失败
    :raises FileNotFoundError: 文件不存在
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"事件配置文件不存在: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"事件配置文件格式错误（非字典）: {path}")

    try:
        config = EventConfig(**raw)
        logger.info("成功加载事件配置: %s (%s)", config.event_id, config.name)
        return config
    except Exception as e:
        raise ValueError(f"事件配置校验失败 [{path}]: {e}") from e


def load_all_event_configs(
    config_dir: str | Path | None = None,
) -> list[EventConfig]:
    """
    从目录批量加载所有事件配置。

    :param config_dir: 配置目录路径，默认使用包内 configs 目录
    :return: EventConfig 列表（按 event_id 排序）
    """
    dir_path = Path(config_dir) if config_dir else _DEFAULT_CONFIG_DIR

    if not dir_path.is_dir():
        logger.warning("事件配置目录不存在: %s", dir_path)
        return []

    configs: list[EventConfig] = []
    yaml_files = sorted(dir_path.glob("*.yaml")) + sorted(dir_path.glob("*.yml"))

    for yaml_file in yaml_files:
        try:
            config = load_event_config(yaml_file)
            if config.enabled:
                configs.append(config)
            else:
                logger.info("事件配置已禁用，跳过: %s", config.event_id)
        except (ValueError, FileNotFoundError) as e:
            logger.error("加载事件配置失败 [%s]: %s", yaml_file.name, e)

    logger.info("共加载 %d 个有效事件配置（目录: %s）", len(configs), dir_path)
    return configs


def load_event_configs_from_dicts(data_list: list[dict]) -> list[EventConfig]:
    """
    从字典列表加载事件配置（用于测试或动态生成）。

    :param data_list: 事件配置字典列表
    :return: EventConfig 列表
    """
    configs: list[EventConfig] = []
    for i, data in enumerate(data_list):
        try:
            config = EventConfig(**data)
            if config.enabled:
                configs.append(config)
        except Exception as e:
            logger.error("加载第 %d 个事件配置失败: %s", i, e)
    return configs
