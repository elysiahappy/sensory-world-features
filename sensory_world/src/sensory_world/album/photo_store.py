"""
照片持久化存储 —— data/album/photos.jsonl

每行一张照片的 JSON 记录，可被世界日记与小说管线读取。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sensory_world.album.models import Photo

logger = logging.getLogger(__name__)


class PhotoStore:
    """照片 JSONL 存储"""

    def __init__(self, file_path: str | Path):
        self.file_path = Path(file_path)
        self._photos: list[Photo] = []

    async def load(self) -> None:
        """从 JSONL 加载历史照片"""
        self._photos = []
        if not self.file_path.exists():
            logger.info("照片存档不存在，将新建: %s", self.file_path)
            return
        try:
            with self.file_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        self._photos.append(Photo.from_dict(data))
                    except Exception as e:
                        logger.warning("跳过损坏的照片记录: %s", e)
            logger.info("加载了 %d 张照片", len(self._photos))
        except Exception as e:
            logger.error("加载照片存档失败: %s", e)
            self._photos = []

    async def append(self, photo: Photo) -> None:
        """追加一张照片（内存 + 落盘）"""
        self._photos.append(photo)
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            with self.file_path.open("a", encoding="utf-8") as f:
                f.write(photo.to_jsonl() + "\n")
        except Exception as e:
            logger.error("写入照片存档失败: %s", e)

    @property
    def photos(self) -> list[Photo]:
        return list(self._photos)

    async def get_by_event(self, event_ref: str) -> list[Photo]:
        """获取某事件的所有照片"""
        return [p for p in self._photos if p.event_ref == event_ref]

    async def get_by_npc(self, npc_id: str) -> list[Photo]:
        """获取某 NPC 在场的所有照片（用于回忆召回）"""
        return [p for p in self._photos if npc_id in p.participant_ids]

    async def get_shared_photos(self, npc_a: str, npc_b: str) -> list[Photo]:
        """
        获取两个 NPC 共同在场的照片 —— 共同记忆交集。
        未来两个 NPC 私语时可召回共同在场的事件。
        """
        return [
            p for p in self._photos
            if npc_a in p.participant_ids and npc_b in p.participant_ids
        ]

    def count(self) -> int:
        return len(self._photos)
