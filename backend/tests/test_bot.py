import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import func, select

from app import bot as module
from app.models import Entry, Folder


def message(text, identifier=1, user=1):
    notice = SimpleNamespace(edit_text=AsyncMock())
    return SimpleNamespace(text=text, caption=None, from_user=SimpleNamespace(id=user, first_name="Owner"),
        message_id=identifier, document=None, video=None, audio=None, voice=None, animation=None, video_note=None,
        photo=None, contact=None, location=None, answer=AsyncMock(return_value=notice))


def test_bot_saves_forwards_idempotently_and_creates_nested_folders(database, monkeypatch):
    monkeypatch.setattr(module, "SessionLocal", database)
    msg = message("Пересланная полезная инструкция", 42)
    asyncio.run(module.handle(msg, AsyncMock()))
    asyncio.run(module.handle(msg, AsyncMock()))
    with database() as db:
        assert db.scalar(select(func.count()).select_from(Entry)) == 1
        entry = db.scalar(select(Entry))
        assert entry.body == msg.text and entry.source == "telegram"
    asyncio.run(module.handle(message("/folder Работа/Проекты"), AsyncMock()))
    with database() as db:
        rows = db.scalars(select(Folder)).all()
        assert len(rows) == 2
        child = next(f for f in rows if f.name == "Проекты")
        assert child.parent_id == next(f.id for f in rows if f.name == "Работа")


def test_bot_refuses_unauthorized_users_and_never_saves_search_commands(database, monkeypatch):
    monkeypatch.setattr(module, "SessionLocal", database)
    denied = message("Секрет чужого пользователя", user=2)
    asyncio.run(module.handle(denied, AsyncMock()))
    assert "Доступ ограничен" in denied.answer.call_args.args[0]
    lookup = AsyncMock()
    monkeypatch.setattr(module, "find", lookup)
    asyncio.run(module.handle(message("/find Где билеты?"), AsyncMock()))
    assert lookup.call_args.args[1] == "Где билеты?"
    asyncio.run(module.handle(message("/password"), AsyncMock()))
    with database() as db:
        assert db.scalar(select(func.count()).select_from(Entry)) == 0


def test_bot_persistent_and_one_off_privacy_modes(database, monkeypatch):
    from app.models import Capture, User
    monkeypatch.setattr(module, "SessionLocal", database)
    module.waiting_search.add(1)
    lookup = AsyncMock()
    monkeypatch.setattr(module, "find", lookup)
    asyncio.run(module.handle(message("/private SECRET_DURING_SEARCH", 100), AsyncMock()))
    assert not lookup.called
    asyncio.run(module.handle(message("/private"), AsyncMock()))
    asyncio.run(module.handle(message("PRIVATE", 101), AsyncMock()))
    asyncio.run(module.handle(message("/public PUBLIC", 102), AsyncMock()))
    asyncio.run(module.handle(message("STILL_PRIVATE", 103), AsyncMock()))
    with database() as db:
        assert db.get(User, 1).bot_ai_policy == "trusted"
        rows = {e.telegram_message_id: e for e in db.scalars(select(Entry))}
        assert rows[101].ai_policy == rows[103].ai_policy == "trusted"
        assert rows[102].ai_policy == "external"
        assert rows[100].ai_policy == "trusted"
        assert len(db.scalars(select(Capture)).all()) == 4
