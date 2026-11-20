"""Read-only proxy/credential check; deliberately never print URLs or exceptions."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from app.config import get_settings


async def main():
    s = get_settings()
    for proxy in [s.telegram_proxy, "socks5://127.0.0.1:2080"]:
        if not proxy:
            continue
        async with Bot(s.bot_token, session=AiohttpSession(proxy=proxy, timeout=12)) as bot:
            try:
                me = await bot.get_me()
                print(f"Telegram OK: @{me.username}; proxy={proxy}")
                return
            except Exception as exc:
                print(f"Proxy check failed: {proxy}; {type(exc).__name__}")
    raise SystemExit(1)


asyncio.run(main())
