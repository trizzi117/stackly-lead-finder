"""Автономный мульти-тенант оркестратор.

На каждого клиента с подключённым аккаунтом и активной подпиской поднимает
свой Telethon-монитор по его активным чатам и ключевым словам. Раз в минуту
сверяется с БД: добавляет новых, останавливает неактивных, пересобирает при
изменении настроек. Работает «само по себе».

Запуск:  python -m app.orchestrator
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import Channel, User as TgUser

from src.classifier import Classifier
from src.llm import LLM
from src.prefilter import PreFilter

from .billing import limits
from .db import SessionLocal, init_db
from .models import Lead, MonitoredChat, TgAccount, User, utcnow
from .platform_bot import send_html_async
from .tg_connect import API_HASH, API_ID

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s orchestrator: %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("orchestrator")

REFRESH = 60  # сек между сверками с БД

_llm = LLM(os.getenv("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/"),
           os.getenv("LLM_API_KEY", ""), os.getenv("LLM_MODEL", "deepseek-chat"))

_EMOJI = {"hot": "🔥", "warm": "🌤", "cold": "❄️"}


class Runner:
    def __init__(self, user_id: int) -> None:
        self.user_id = user_id
        self.client: TelegramClient | None = None
        self.sig: tuple = ()
        self.month_count = 0
        self.cap = 0


runners: dict[int, Runner] = {}


def _tenant_config(db, user: User):
    """Активные чаты + ключи + сигнатура для детекта изменений."""
    chats = db.query(MonitoredChat).filter(
        MonitoredChat.user_id == user.id, MonitoredChat.active.is_(True)).all()
    chat_ids = [c.chat_id for c in chats]
    kws = user.get_keywords()
    stops = user.get_stop_words()
    sig = (frozenset(chat_ids), tuple(kws), tuple(stops),
           user.business_context, user.hot_threshold,
           user.subscription.plan if user.subscription else "none")
    return chat_ids, kws, stops, sig


def _format_alert(lead: dict) -> str:
    cls = lead["classification"]
    who = lead.get("sender_name") or "—"
    if lead.get("username"):
        who += f" (@{lead['username']})"
    parts = [
        f"{_EMOJI.get(cls, '•')} <b>{cls.upper()} лид</b> · score {lead.get('score', 0)}",
        f"👤 {html.escape(who)}   💬 {html.escape(lead.get('chat_title') or '—')}",
        f"<blockquote>{html.escape((lead.get('text') or '')[:500])}</blockquote>",
    ]
    if lead.get("intent"):
        parts.append(f"🧠 {html.escape(lead['intent'])}")
    if lead.get("reply"):
        parts.append(f"\n✍️ <b>Ответ:</b>\n{html.escape(lead['reply'])}")
    if lead.get("link"):
        parts.append(f"\n🔗 <a href=\"{html.escape(lead['link'])}\">Открыть</a>")
    return "\n".join(parts)


async def build_runner(user_id: int) -> Runner | None:
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user or not user.tg_account or user.tg_account.status != "connected":
            return None
        if not (user.subscription and user.subscription.is_active()):
            return None
        chat_ids, kws, stops, sig = _tenant_config(db, user)
        if not chat_ids or not kws:
            return None
        business = user.business_context
        threshold = user.hot_threshold
        alert_chat = user.alert_chat_id
        cap = limits(user.subscription).get("leads_month", 0)
        session_string = user.tg_account.session_string
    finally:
        db.close()

    client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        log.warning("user %s: сессия не авторизована", user_id)
        await client.disconnect()
        _mark_account_error(user_id)
        return None

    prefilter = PreFilter(kws, stops)
    classifier = Classifier(_llm, business)
    runner = Runner(user_id)
    runner.client = client
    runner.sig = sig
    runner.cap = cap
    runner.month_count = _month_count(user_id)

    @client.on(events.NewMessage(incoming=True, chats=chat_ids))
    async def handler(event):  # noqa: ANN001
        try:
            if not (event.is_group or event.is_channel):
                return
            text = event.raw_text
            if not text or not text.strip():
                return
            kw = prefilter.match(text)
            if not kw:
                return
            if runner.cap and runner.month_count >= runner.cap:
                return
            chat = await event.get_chat()
            title = getattr(chat, "title", None) or getattr(chat, "username", None) or "—"
            result = await classifier.classify(text, title)
            sender = await event.get_sender()
            uname, sid, sname = None, None, "—"
            if isinstance(sender, TgUser):
                sid = sender.id
                uname = sender.username
                sname = " ".join(p for p in [sender.first_name, sender.last_name] if p) or (uname or "—")
            link = (f"https://t.me/{chat.username}/{event.id}"
                    if isinstance(chat, Channel) and getattr(chat, "username", None)
                    else (f"https://t.me/{uname}" if uname else None))
            lead = {
                "user_id": user_id, "chat_id": event.chat_id, "chat_title": title,
                "message_id": event.id, "sender_id": sid, "sender_name": sname, "username": uname,
                "text": text, "keyword": kw, "classification": result.classification,
                "score": result.score, "intent": result.intent, "reply": result.reply, "link": link,
            }
            if _save_lead(lead):
                runner.month_count += 1
                if (result.is_hot or result.score >= threshold) and alert_chat:
                    await send_html_async(alert_chat, _format_alert(lead))
        except Exception:  # noqa: BLE001
            log.exception("user %s: ошибка обработки", user_id)

    log.info("user %s: монитор поднят (%d чатов, %d ключей)", user_id, len(chat_ids), len(kws))
    return runner


def _save_lead(lead: dict) -> bool:
    db = SessionLocal()
    try:
        db.add(Lead(created_at=utcnow(), **lead))
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False
    finally:
        db.close()


def _month_count(user_id: int) -> int:
    from sqlalchemy import func
    db = SessionLocal()
    try:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        return db.query(func.count(Lead.id)).filter(
            Lead.user_id == user_id,
            func.strftime("%Y-%m", Lead.created_at) == month).scalar() or 0
    finally:
        db.close()


def _mark_account_error(user_id: int) -> None:
    db = SessionLocal()
    try:
        acc = db.query(TgAccount).filter(TgAccount.user_id == user_id).first()
        if acc:
            acc.status = "error"
            db.commit()
    finally:
        db.close()


async def stop_runner(runner: Runner) -> None:
    if runner.client:
        try:
            await runner.client.disconnect()
        except Exception:  # noqa: BLE001
            pass


async def reconcile() -> None:
    db = SessionLocal()
    try:
        users = db.query(User).join(TgAccount).filter(TgAccount.status == "connected").all()
        wanted = {}
        for u in users:
            if u.subscription and u.subscription.is_active():
                _, _, _, sig = _tenant_config(db, u)
                wanted[u.id] = sig
    finally:
        db.close()

    # остановить лишних / изменившихся
    for uid in list(runners):
        if uid not in wanted or runners[uid].sig != wanted[uid]:
            await stop_runner(runners.pop(uid))
            log.info("user %s: монитор остановлен/пересборка", uid)

    # поднять новых
    for uid in wanted:
        if uid not in runners:
            r = await build_runner(uid)
            if r:
                runners[uid] = r


async def main() -> None:
    init_db()
    if not API_ID or not API_HASH:
        log.error("TG_API_ID / TG_API_HASH не заданы — оркестратор не может подключать аккаунты.")
        return
    log.info("Оркестратор запущен. Сверка каждые %d с.", REFRESH)
    while True:
        try:
            await reconcile()
        except Exception:  # noqa: BLE001
            log.exception("ошибка reconcile")
        await asyncio.sleep(REFRESH)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Остановлено.")
