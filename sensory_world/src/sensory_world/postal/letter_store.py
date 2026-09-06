"""
邮差系统 —— 信件持久化存储

所有信件持久化为 JSONL（data/postal/letters.jsonl），
每行一封信，可被世界日记和小说管线读取。

特性：
  - 追加写入（append-only）
  - 启动时加载历史
  - 按方向/寄信人/收信人查询
  - 目录不存在时自动创建
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sensory_world.postal.models import Letter, LetterDirection, LetterStatus

logger = logging.getLogger(__name__)


class LetterStore:
    """
    信件 JSONL 存储。

    用法：
        store = LetterStore("data/postal/letters.jsonl")
        await store.load()              # 启动时加载历史
        await store.append(letter)      # 追加一封信
        letters = await store.get_all() # 获取全部
    """

    def __init__(self, file_path: str | Path):
        self._file_path = Path(file_path)
        self._letters: list[Letter] = []

    @property
    def file_path(self) -> Path:
        return self._file_path

    async def load(self) -> list[Letter]:
        """从 JSONL 文件加载历史信件"""
        if not self._file_path.exists():
            logger.info("信件文件不存在，将新建: %s", self._file_path)
            self._letters = []
            return []

        letters: list[Letter] = []
        try:
            with open(self._file_path, "r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        letters.append(Letter.from_jsonl_dict(data))
                    except (json.JSONDecodeError, KeyError, ValueError) as e:
                        logger.error("信件文件第 %d 行解析失败: %s", line_no, e)
        except Exception as e:
            logger.error("加载信件文件失败: %s", e)

        self._letters = letters
        logger.info("加载 %d 封历史信件", len(letters))
        return letters

    async def append(self, letter: Letter) -> None:
        """追加一封信到存储（内存 + 文件）"""
        self._letters.append(letter)

        # 确保目录存在
        self._file_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(self._file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(letter.to_jsonl_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error("写入信件文件失败: %s", e)
            raise

    async def get_all(self) -> list[Letter]:
        """获取全部信件"""
        return list(self._letters)

    async def get_by_direction(self, direction: LetterDirection) -> list[Letter]:
        """按方向筛选信件"""
        return [l for l in self._letters if l.direction == direction]

    async def get_by_npc(self, npc_id: str) -> list[Letter]:
        """获取与某 NPC 相关的所有信件（寄或收）"""
        return [
            l for l in self._letters
            if l.sender_id == npc_id or l.recipient_id == npc_id
        ]

    async def get_received_by(self, npc_id: str) -> list[Letter]:
        """获取某 NPC 收到的所有已送达信件"""
        return [
            l for l in self._letters
            if l.recipient_id == npc_id and l.status == LetterStatus.DELIVERED
        ]

    async def get_pending_outside(self) -> list[Letter]:
        """获取待外部取走的城外发件箱信件"""
        return [
            l for l in self._letters
            if l.direction == LetterDirection.NPC_TO_OUTSIDE
            and l.status == LetterStatus.PENDING
        ]

    async def get_rejected(self) -> list[Letter]:
        """获取所有被拒收的信件（审查留档）"""
        return [l for l in self._letters if l.status == LetterStatus.REJECTED]

    async def count(self) -> int:
        """信件总数"""
        return len(self._letters)
