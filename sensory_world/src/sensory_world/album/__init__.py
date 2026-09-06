"""
相册系统（PhotoAlbumSystem）—— 阶段二模块二

承载 NPC：三月七（march7th），用照片为城市留下记忆物证。

核心模块：
  - album_system.PhotoAlbumSystem: 相册系统主入口
  - photo_store.PhotoStore: 照片 JSONL 持久化
  - models: 照片/配置数据模型
"""

from sensory_world.album.album_system import PhotoAlbumSystem
from sensory_world.album.models import AlbumConfig, Photo, PhotoSceneType, roll_event_photo_count
from sensory_world.album.photo_store import PhotoStore

__all__ = [
    "PhotoAlbumSystem",
    "AlbumConfig",
    "Photo",
    "PhotoSceneType",
    "PhotoStore",
    "roll_event_photo_count",
]
