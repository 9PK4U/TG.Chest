import asyncio
import io
import logging
import os
import time

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, Message, WebAppInfo
from fastapi import HTTPException
from sqlalchemy import select

from .config import get_settings
from .db import SessionLocal
from .models import Attachment, Entry, Folder, User
from .schemas import EntryCreate
from .search import search
from .services import add_attachment, create_entry

dp = Dispatcher()
waiting_search = set()
last_search = {}


def app_keyboard(vault=False):
    url = get_settings().public_url + ("?view=vault" if vault else "")
    if not url.startswith("https://"):
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть Chest ↗", web_app=WebAppInfo(url=url))]])


async def find(message: Message, query: str):
    user_id = message.from_user.id
    if time.monotonic() - last_search.get(user_id, -100) < 5:
        await message.answer("Подожди несколько секунд перед следующим поиском.")
        return
    last_search[user_id] = time.monotonic()
    notice = await message.answer("Ищу в твоих записях…")
    try:
        with SessionLocal() as db:
            result = await search(db, user_id, query[:2000], ai_policy=db.get(User, user_id).bot_ai_policy)
        text = result["answer"] or ("Вот что нашлось:" if result["results"] else "Пока ничего не нашлось. Попробуй другие слова.")
        for i, row in enumerate(result["results"], 1):
            text += f"\n\n[{i}] {row['title']}\n{row['excerpt'][:250]}"
        if result["warnings"]:
            text += "\n\n" + "\n".join(result["warnings"])
        await notice.edit_text(text[:4000], reply_markup=app_keyboard())
    except Exception:
        await notice.edit_text("Поиск временно недоступен. Записи сохранены; попробуй позже.")


@dp.message(F.chat.type == "private")
async def handle(message: Message, bot: Bot):
    s = get_settings()
    if not message.from_user:
        return
    user_id = message.from_user.id
    raw = message.text or message.caption or ""
    command = raw.split(maxsplit=1)[0].split("@")[0].lower() if raw.startswith("/") else ""
    argument = raw.split(maxsplit=1)[1] if command and len(raw.split(maxsplit=1)) > 1 else ""
    if command == "/id":
        await message.answer(f"Твой Telegram ID: {user_id}")
        return
    if user_id not in s.allowed_ids:
        await message.answer("Это личный Chest. Доступ ограничен владельцем. /id — узнать свой ID.")
        return
    with SessionLocal() as db:
        if not db.get(User, user_id):
            db.add(User(id=user_id, name=message.from_user.first_name))
            db.commit()
        policy = db.get(User, user_id).bot_ai_policy
    if command in {"/private", "/public", "/local"}:
        waiting_search.discard(user_id)
        policy = {"/private": "trusted", "/public": "external", "/local": "local"}[command]
        has_media = any(getattr(message, field, None) for field in ["document", "photo", "audio", "video", "voice", "animation", "video_note"])
        if not argument and not has_media:
            with SessionLocal() as db:
                db.get(User, user_id).bot_ai_policy = policy
                db.commit()
            await message.answer({"trusted": "Чувствительный режим включён: сообщения получает только доверенная модель. Пока она не подключена, сохраняю без разбора.",
                "external": "Обычный режим включён: следующие сообщения и текст файлов отправляются обычной модели для разбора.",
                "local": "Локальный режим: сохраняю сообщения без отправки каким-либо моделям."}[policy])
            return
        raw = argument
        command = ""
    if command in {"/start", "/help"}:
        await message.answer("Chest — всё нужное под рукой.\n\n"
            "Просто пришли или перешли сообщение, ссылку, фото или файл — я сохраню.\n\n"
            "/add текст — добавить запись\n/find вопрос — найти и получить ответ по записям\n"
            "/folder Работа/Проекты — создать раздел\n/app — открыть хранилище\n"
            "/password — открыть сейф\n/cancel — отменить поиск\n\n"
            "/private — чувствительный режим (доверенная модель)\n/public — обычная LLM\n/local — без моделей\n"
            "С текстом после команды режим применяется только к этому сообщению.\n\n"
            "Пароли добавляй через сейф в Mini App: там они шифруются мастер-паролем.", reply_markup=app_keyboard())
        return
    if command in {"/app", "/password"}:
        await message.answer("Сейф и все записи — в приложении." if command == "/password" else "Твой Chest:", reply_markup=app_keyboard(vault=command == "/password"))
        return
    if command == "/cancel":
        waiting_search.discard(user_id)
        await message.answer("Готово, следующее сообщение сохраню как запись.")
        return
    if command == "/find":
        if argument:
            waiting_search.discard(user_id)
            await find(message, argument)
        else:
            waiting_search.add(user_id)
            await message.answer("Что найти? Напиши вопрос следующим сообщением. /cancel — отмена.")
        return
    if user_id in waiting_search and not command:
        waiting_search.discard(user_id)
        await find(message, raw)
        return
    if command == "/folder":
        parts = [p.strip() for p in argument.split("/") if p.strip()]
        if not parts or len(parts) > 10 or any(len(p) > 100 for p in parts):
            await message.answer("Например: /folder Работа/Проекты (до 10 уровней)")
            return
        with SessionLocal() as db:
            parent = None
            for name in parts:
                folder = db.scalar(select(Folder).where(Folder.user_id == user_id, Folder.parent_id == parent, Folder.name == name))
                if not folder:
                    folder = Folder(user_id=user_id, parent_id=parent, name=name)
                    db.add(folder)
                    db.flush()
                parent = folder.id
            db.commit()
        await message.answer(f"Раздел готов: {' / '.join(parts)}. Перемещать записи удобно в приложении.", reply_markup=app_keyboard())
        return
    if command == "/add":
        if not argument:
            await message.answer("Пришли текст следующим сообщением — он сразу сохранится во «Входящие».")
            return
        raw = argument
    elif command:
        await message.answer("Неизвестная команда. /help — список команд.")
        return
    media = message.document or message.video or message.audio or message.voice or message.animation or message.video_note
    if not media and message.photo:
        media = message.photo[-1]
    contact = message.contact
    location = message.location
    if contact:
        raw += f"\n{contact.first_name} {contact.last_name or ''}\n{contact.phone_number}"
    if location:
        raw += f"\nhttps://maps.google.com/?q={location.latitude},{location.longitude}"
    if not raw.strip() and not media:
        await message.answer("Пришли текст, ссылку, фото или файл. Этот тип сообщения пока не поддерживается.")
        return
    notice = None
    try:
        content = None
        filename = ""
        mime = "application/octet-stream"
        if media:
            if (media.file_size or 0) > s.max_file_mb * 1024 * 1024:
                await message.answer(f"Максимальный размер файла — {s.max_file_mb} МБ.")
                return
            notice = await message.answer("Сохраняю файл…")
            suffix = ".jpg" if message.photo else ".ogg" if message.voice else ".mp4" if (message.video or message.video_note) else ""
            filename = getattr(media, "file_name", None) or f"telegram-{message.message_id}{suffix}"
            mime = getattr(media, "mime_type", None) or ("image/jpeg" if message.photo else "application/octet-stream")
            remote = await bot.get_file(media.file_id)
            buffer = io.BytesIO()
            await bot.download_file(remote.file_path, buffer)
            content = buffer.getvalue()
            if len(content) > s.max_file_mb * 1024 * 1024:
                raise HTTPException(413, "Файл слишком большой")
        with SessionLocal() as db:
            entry = db.scalar(select(Entry).where(Entry.user_id == user_id, Entry.telegram_message_id == message.message_id))
            if not entry:
                title = (raw.strip().split("\n")[0] if raw.strip() else filename)[:200]
                kind = "link" if re_is_link(raw.strip()) else "note"
                entry = create_entry(db, user_id, EntryCreate(title=title, body=raw[:100000], kind=kind, ai_policy=policy), "telegram", message.message_id)
            if content is not None and not db.scalar(select(Attachment).where(Attachment.entry_id == entry.id)):
                add_attachment(db, entry, filename, mime, content)
            if not entry.capture_id:
                from .ingestion import capture_input
                capture_input(db, entry)
        text = "Сохранено во «Входящие» ✓" + (" Файл в очереди на загрузку." if media else "")
        text += {"external": "\nLLM разберёт информацию на заметки. Исходник сохранён.",
                 "trusted": "\nТолько доверенная модель. Если она не подключена, разбор будет ждать.",
                 "local": "\nБез отправки моделям."}[policy]
        if notice:
            await notice.edit_text(text, reply_markup=app_keyboard())
        else:
            await message.answer(text, reply_markup=app_keyboard())
    except Exception:
        text = "Не удалось сохранить сообщение целиком. Попробуй отправить его ещё раз."
        if notice:
            await notice.edit_text(text)
        else:
            await message.answer(text)


def re_is_link(value):
    return value.startswith(("https://", "http://"))


async def main():
    os.environ["CHEST_PROCESS_ROLE"] = "bot"
    s = get_settings()
    s.validate_runtime()
    if not s.bot_token or not s.allowed_ids:
        raise RuntimeError("Set BOT_TOKEN and ALLOWED_TELEGRAM_IDS")
    session = AiohttpSession(proxy=s.telegram_proxy or None)
    async with Bot(s.bot_token, session=session) as bot:
        await bot.delete_webhook(drop_pending_updates=False)
        await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in [
            ("add", "Добавить запись"), ("find", "Найти в записях"), ("app", "Открыть Chest"),
            ("password", "Открыть сейф"), ("folder", "Создать раздел"), ("private", "Чувствительный режим"),
            ("public", "Обычная LLM"), ("local", "Без моделей"), ("help", "Помощь"), ("id", "Мой Telegram ID")]])
        if s.public_url.startswith("https://"):
            await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Chest", web_app=WebAppInfo(url=s.public_url)))
        await dp.start_polling(bot, allowed_updates=["message"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
