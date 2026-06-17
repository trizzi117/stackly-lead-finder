"""Воркер-слушатель Telegram (MTProto / Telethon).

Запуск:  python -m src.worker
Первый запуск спросит код подтверждения из Telegram (и пароль 2FA, если включён).

БЕЗОПАСНОСТЬ АККАУНТА (см. README §Ban-playbook):
- только ЧТЕНИЕ, никакой авто-отправки;
- НЕ вступай в сотни чатов разом — добавляй по 10–20 в день руками;
- используй ВТОРИЧНЫЙ прогретый аккаунт, не основной.
"""
from __future__ import annotations

import asyncio
import logging

from telethon import TelegramClient, events
from telethon.tl.types import Channel, Chat, User

from .classifier import Classifier
from .llm import LLM
from .notifier import Notifier
from .pipeline import Pipeline
from .prefilter import PreFilter
from .settings import settings
from .storage import Storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("stackly.worker")


def _build_link(chat, message_id: int, username: str | None) -> str | None:
    """Ссылка на сообщение (для публичных) или на автора."""
    if isinstance(chat, Channel) and getattr(chat, "username", None):
        return f"https://t.me/{chat.username}/{message_id}"
    if username:
        return f"https://t.me/{username}"
    return None


async def main() -> None:
    problems = settings.validate_worker()
    if problems:
        log.error("Не запустить воркер:\n - %s", "\n - ".join(problems))
        log.error("Заполни .env (см. .env.example). Готово? Повтори запуск.")
        return

    prefilter = PreFilter(settings.keywords, settings.stop_words)
    llm = LLM(settings.llm_base_url, settings.llm_api_key, settings.llm_model)
    classifier = Classifier(llm, settings.business_context)
    storage = Storage(settings.db_abspath)
    notifier = Notifier(settings.alert_bot_token, settings.alert_chat_id)
    pipeline = Pipeline(prefilter, classifier, storage, notifier, settings.hot_threshold)

    client = TelegramClient(settings.session, settings.api_id, settings.api_hash)

    monitored = settings.monitored_chats or None
    if monitored:
        log.info("Мониторю %d заданных чатов из config/chats.txt", len(monitored))
    else:
        log.info("config/chats.txt пуст — слушаю ВСЕ группы и каналы аккаунта")

    @client.on(events.NewMessage(incoming=True, chats=monitored))
    async def handler(event: events.NewMessage.Event) -> None:
        try:
            # только группы и каналы, не личка
            if not (event.is_group or event.is_channel):
                return
            text = event.raw_text
            if not text or not text.strip():
                return

            chat = await event.get_chat()
            chat_title = getattr(chat, "title", None) or getattr(chat, "username", None) or "—"

            sender = await event.get_sender()
            sender_name, username, sender_id = "—", None, None
            if isinstance(sender, User):
                sender_id = sender.id
                username = sender.username
                sender_name = " ".join(
                    p for p in [sender.first_name, sender.last_name] if p
                ) or (username or "—")

            msg = {
                "chat_id": event.chat_id,
                "chat_title": chat_title,
                "message_id": event.id,
                "sender_id": sender_id,
                "sender_name": sender_name,
                "username": username,
                "text": text,
                "link": _build_link(chat, event.id, username),
            }
            await pipeline.process(msg)
        except Exception:  # один битый месседж не должен ронять цикл
            log.exception("ошибка обработки сообщения")

    log.info("Ниша: %s | ключей: %d | стоп-слов: %d | порог hot: %d",
             settings.niche_title, len(settings.keywords),
             len(settings.stop_words), settings.hot_threshold)
    log.info("Подключаюсь к Telegram...")
    await client.start(phone=settings.phone or None)
    me = await client.get_me()
    log.info("Готово. Вошёл как %s. Слушаю чаты. Ctrl+C для остановки.",
             getattr(me, "username", None) or me.id)
    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Остановлено.")
