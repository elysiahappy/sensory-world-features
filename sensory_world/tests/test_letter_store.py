"""
信件存储测试 —— JSONL 持久化与查询。
"""

from datetime import datetime
from pathlib import Path

import pytest

from sensory_world.postal.letter_store import LetterStore
from sensory_world.postal.models import Letter, LetterDirection, LetterStatus


def _make_letter(letter_id="letter_test1", sender="robin", recipient="eden",
                 direction=LetterDirection.NPC_TO_NPC) -> Letter:
    return Letter(
        letter_id=letter_id,
        direction=direction,
        sender_id=sender,
        recipient_id=recipient,
        postman_id="violet",
        subject="测试信",
        body="见字如面。",
        reason="想念",
        status=LetterStatus.DELIVERED,
        created_at=datetime(2026, 7, 17, 20, 0),
        delivered_at=datetime(2026, 7, 17, 20, 5),
    )


@pytest.mark.asyncio
async def test_append_and_load(tmp_path: Path):
    store = LetterStore(tmp_path / "letters.jsonl")
    await store.load()
    assert await store.count() == 0

    letter = _make_letter()
    await store.append(letter)
    assert await store.count() == 1
    assert (tmp_path / "letters.jsonl").exists()

    # 重新加载
    store2 = LetterStore(tmp_path / "letters.jsonl")
    loaded = await store2.load()
    assert len(loaded) == 1
    assert loaded[0].letter_id == "letter_test1"
    assert loaded[0].sender_id == "robin"
    assert loaded[0].body == "见字如面。"


@pytest.mark.asyncio
async def test_get_by_npc(tmp_path: Path):
    store = LetterStore(tmp_path / "letters.jsonl")
    await store.append(_make_letter("l1", "robin", "eden"))
    await store.append(_make_letter("l2", "eden", "sparkle"))
    await store.append(_make_letter("l3", "sparkle", "robin"))

    robin_letters = await store.get_by_npc("robin")
    # robin 参与 l1（发）和 l3（收）
    assert len(robin_letters) == 2

    eden_letters = await store.get_by_npc("eden")
    assert len(eden_letters) == 2


@pytest.mark.asyncio
async def test_pending_outbox(tmp_path: Path):
    store = LetterStore(tmp_path / "letters.jsonl")
    pending = _make_letter("l1", "sparkle", "outside_friend",
                           direction=LetterDirection.NPC_TO_OUTSIDE)
    pending.status = LetterStatus.PENDING
    await store.append(pending)

    delivered = _make_letter("l2", "robin", "eden")
    await store.append(delivered)

    pending_out = await store.get_pending_outside()
    assert len(pending_out) == 1
    assert pending_out[0].letter_id == "l1"
