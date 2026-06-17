"""Платформенный Telegram-бот: привязка лички (/start <token>) и приём команд.

Запуск:  python -m app.bot
Через этого бота клиенту приходят лиды и сообщения о биллинге/аккаунте.
Привязка: кабинет → «Telegram-бот» → кнопка открывает t.me/<bot>?start=<token>.
"""
from __future__ import annotations

import asyncio
import os

import httpx

from .db import SessionLocal, init_db
from .models import User
from .platform_bot import send_html_async

TOKEN = os.getenv("PLATFORM_BOT_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}"

WELCOME = ("Привет! Это бот <b>Stackly Lead Finder</b>.\n"
           "Подключите его в кабинете → раздел «Telegram-бот», "
           "чтобы получать сюда лидов и уведомления о подписке.")
LINKED = ("✅ Бот подключён!\n"
          "Сюда будут приходить горячие лиды и сообщения о вашей подписке и аккаунте.")


async def handle_update(upd: dict) -> None:
    msg = upd.get("message") or upd.get("edited_message") or {}
    text = (msg.get("text") or "").strip()
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if not chat_id:
        return

    token = ""
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        token = parts[1].strip() if len(parts) > 1 else ""

    db = SessionLocal()
    try:
        if token:
            user = db.query(User).filter(User.link_token == token).first()
            if user:
                user.alert_chat_id = str(chat_id)
                user.bot_linked = True
                user.link_token = None
                db.commit()
                await send_html_async(chat_id, LINKED)
                return
        await send_html_async(chat_id, WELCOME)
    finally:
        db.close()


async def main() -> None:
    if not TOKEN:
        print("PLATFORM_BOT_TOKEN не задан в .env — бот не запущен.")
        return
    init_db()
    print("Бот запущен (long-polling). Ctrl+C для остановки.")
    offset = 0
    async with httpx.AsyncClient(timeout=40.0) as client:
        while True:
            try:
                r = await client.get(f"{API}/getUpdates", params={"offset": offset, "timeout": 30})
                for upd in r.json().get("result", []):
                    offset = upd["update_id"] + 1
                    await handle_update(upd)
            except httpx.HTTPError as exc:
                print("polling error:", exc)
                await asyncio.sleep(3)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nОстановлено.")
