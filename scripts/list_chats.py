"""Показать все группы/каналы аккаунта — чтобы выбрать, что класть в config/chats.txt.

Запуск:  python -m scripts.list_chats
"""
from __future__ import annotations

import asyncio

from telethon import TelegramClient
from telethon.tl.types import Channel, Chat

from src.settings import settings


async def main() -> None:
    client = TelegramClient(settings.session, settings.api_id, settings.api_hash)
    await client.start(phone=settings.phone or None)
    print(f"\n{'ID':>15}  {'username':<24} title")
    print("-" * 70)
    async for dialog in client.iter_dialogs():
        ent = dialog.entity
        if not isinstance(ent, (Channel, Chat)):
            continue  # пропускаем личку
        username = getattr(ent, "username", None) or ""
        kind = "канал" if getattr(ent, "broadcast", False) else "группа"
        print(f"{dialog.id:>15}  {('@' + username) if username else '':<24} [{kind}] {dialog.name}")
    print("\nСкопируй нужные id или @username в config/chats.txt (по одному на строку).")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
