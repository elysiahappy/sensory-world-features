"""
事件配置加载测试 —— 验证 YAML 加载与校验
"""

import pytest
import tempfile
from pathlib import Path

import yaml

from sensory_world.periodic_event.event_config import (
    load_event_config,
    load_all_event_configs,
    load_event_configs_from_dicts,
)


class TestLoadEventConfig:
    """单文件加载测试"""

    def test_load_valid_yaml(self, tmp_path):
        """加载有效的 YAML 配置"""
        config_data = {
            "event_id": "test_event",
            "name": "测试事件",
            "event_type": "scene",
            "trigger": {"trigger_type": "daily", "hour": 12, "minute": 0},
            "main_venue": "plaza",
            "slices": [
                {"slice_id": "main", "location": "plaza", "capacity": 5},
            ],
            "participation": {
                "required_npcs": ["npc_a"],
                "optional_npcs": [],
                "resident_probability": 0.2,
            },
            "duration_minutes": 30,
        }
        yaml_file = tmp_path / "test_event.yaml"
        with open(yaml_file, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f, allow_unicode=True)

        config = load_event_config(yaml_file)
        assert config.event_id == "test_event"
        assert config.name == "测试事件"
        assert len(config.slices) == 1

    def test_load_nonexistent_file(self):
        """加载不存在的文件"""
        with pytest.raises(FileNotFoundError):
            load_event_config("/nonexistent/path.yaml")

    def test_load_invalid_yaml(self, tmp_path):
        """加载格式错误的 YAML"""
        yaml_file = tmp_path / "bad.yaml"
        with open(yaml_file, "w") as f:
            f.write("just a string, not a dict")

        with pytest.raises(ValueError, match="格式错误"):
            load_event_config(yaml_file)

    def test_load_validation_error(self, tmp_path):
        """加载校验失败的数据"""
        config_data = {
            "event_id": "bad",
            # 缺少必填字段
        }
        yaml_file = tmp_path / "bad_config.yaml"
        with open(yaml_file, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        with pytest.raises(ValueError, match="校验失败"):
            load_event_config(yaml_file)


class TestLoadAllEventConfigs:
    """批量加载测试"""

    def test_load_from_directory(self, tmp_path):
        """从目录批量加载"""
        for i in range(3):
            config_data = {
                "event_id": f"event_{i}",
                "name": f"事件{i}",
                "event_type": "scene",
                "trigger": {"trigger_type": "daily", "hour": 12, "minute": 0},
                "main_venue": "plaza",
            }
            yaml_file = tmp_path / f"event_{i}.yaml"
            with open(yaml_file, "w", encoding="utf-8") as f:
                yaml.dump(config_data, f, allow_unicode=True)

        configs = load_all_event_configs(tmp_path)
        assert len(configs) == 3

    def test_skip_disabled_events(self, tmp_path):
        """跳过已禁用的事件"""
        enabled = {
            "event_id": "enabled",
            "name": "启用",
            "event_type": "scene",
            "trigger": {"trigger_type": "daily", "hour": 12, "minute": 0},
            "main_venue": "plaza",
            "enabled": True,
        }
        disabled = {
            "event_id": "disabled",
            "name": "禁用",
            "event_type": "scene",
            "trigger": {"trigger_type": "daily", "hour": 12, "minute": 0},
            "main_venue": "plaza",
            "enabled": False,
        }
        for data in [enabled, disabled]:
            yaml_file = tmp_path / f"{data['event_id']}.yaml"
            with open(yaml_file, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True)

        configs = load_all_event_configs(tmp_path)
        assert len(configs) == 1
        assert configs[0].event_id == "enabled"

    def test_nonexistent_directory(self):
        """不存在的目录返回空列表"""
        configs = load_all_event_configs("/nonexistent/dir")
        assert configs == []

    def test_load_builtin_configs(self):
        """加载包内自带的样例配置"""
        configs = load_all_event_configs()  # 默认使用包内 configs 目录
        assert len(configs) >= 3  # 至少有烟火、演唱会、学园三个


class TestLoadFromDicts:
    """字典列表加载测试"""

    def test_load_from_dicts(self, sample_event_config_dict):
        """从字典列表加载"""
        configs = load_event_configs_from_dicts([sample_event_config_dict])
        assert len(configs) == 1
        assert configs[0].event_id == "test_fireworks"

    def test_skip_invalid_dicts(self):
        """跳过无效字典"""
        dicts = [
            {"event_id": "valid", "name": "有效", "event_type": "scene",
             "trigger": {"trigger_type": "daily", "hour": 12, "minute": 0},
             "main_venue": "x"},
            {"bad": "data"},  # 无效
        ]
        configs = load_event_configs_from_dicts(dicts)
        assert len(configs) == 1
