"""Подключение Telegram-аккаунта тенанта по QR (Telethon StringSession).

Веб-флоу:
  POST /app/connect/start  → создаём клиент, qr_login(), отдаём QR-URL
  фон: ждём скан (с авто-перевыпуском QR), при 2FA → password_required
  GET  /app/connect/status → фронт опрашивает; при connected сохраняем сессию в БД

In-memory менеджер рассчитан на 1 процесс uvicorn (MVP). Для прода — вынести в Redis.
"""
from __future__ import annotations

import asyncio
import os
import time

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

API_ID = int(os.getenv("TG_API_ID", "0") or 0)
API_HASH = os.getenv("TG_API_HASH", "")

QR_TOTAL_WAIT = 300  # сек на весь процесс скана


class ConnectSession:
    def __init__(self, user_id: int) -> None:
        self.user_id = user_id
        self.client: TelegramClient | None = None
        self.qr = None
        self.qr_url: str | None = None
        self.status = "idle"   # idle/waiting_qr/password_required/connected/error
        self.error = ""
        self.session_string: str | None = None
        self.tg_user_id: int | None = None
        self.username: str | None = None
        self.phone: str | None = None
        self.phone_code_hash: str | None = None
        self.entered_phone: str | None = None
        self._task: asyncio.Task | None = None


manager: dict[int, ConnectSession] = {}


async def _finish(cs: ConnectSession) -> None:
    me = await cs.client.get_me()
    cs.tg_user_id = me.id
    cs.username = me.username
    cs.phone = getattr(me, "phone", None)
    cs.session_string = cs.client.session.save()
    cs.status = "connected"
    try:
        await cs.client.disconnect()  # сессия в БД, клиента поднимет оркестратор
    except Exception:
        pass


async def start_qr(user_id: int) -> ConnectSession:
    # сбросить прошлую попытку
    old = manager.get(user_id)
    if old and old.client:
        try:
            await old.client.disconnect()
        except Exception:
            pass

    cs = ConnectSession(user_id)
    manager[user_id] = cs

    if not API_ID or not API_HASH:
        cs.status = "error"
        cs.error = "На сервере не заданы TG_API_ID / TG_API_HASH (.env)"
        return cs

    try:
        cs.client = TelegramClient(StringSession(), API_ID, API_HASH)
        await cs.client.connect()
        cs.qr = await cs.client.qr_login()
        cs.qr_url = cs.qr.url
        cs.status = "waiting_qr"
    except Exception as exc:  # noqa: BLE001
        cs.status = "error"
        cs.error = f"не удалось начать вход: {exc}"
        return cs

    cs._task = asyncio.create_task(_wait_loop(cs))
    return cs


async def _wait_loop(cs: ConnectSession) -> None:
    deadline = time.monotonic() + QR_TOTAL_WAIT
    try:
        while time.monotonic() < deadline:
            try:
                await cs.qr.wait(timeout=25)
                await _finish(cs)
                return
            except asyncio.TimeoutError:
                await cs.qr.recreate()       # QR-токен протух — перевыпускаем
                cs.qr_url = cs.qr.url
                continue
            except SessionPasswordNeededError:
                cs.status = "password_required"
                return
        cs.status = "error"
        cs.error = "QR не отсканирован вовремя — начните заново"
    except Exception as exc:  # noqa: BLE001
        cs.status = "error"
        cs.error = str(exc)


async def submit_password(user_id: int, password: str) -> ConnectSession | None:
    cs = manager.get(user_id)
    if not cs or cs.status != "password_required" or not cs.client:
        return cs
    try:
        await cs.client.sign_in(password=password)
        await _finish(cs)
    except Exception as exc:  # noqa: BLE001
        cs.status = "error"
        cs.error = f"неверный облачный пароль: {exc}"
    return cs


async def start_phone(user_id: int, phone: str) -> ConnectSession:
    old = manager.get(user_id)
    if old and old.client:
        try:
            await old.client.disconnect()
        except Exception:  # noqa: BLE001
            pass
    cs = ConnectSession(user_id)
    cs.entered_phone = phone
    manager[user_id] = cs
    if not API_ID or not API_HASH:
        cs.status = "error"
        cs.error = "На сервере не заданы TG_API_ID / TG_API_HASH (.env)"
        return cs
    try:
        cs.client = TelegramClient(StringSession(), API_ID, API_HASH)
        await cs.client.connect()
        sent = await cs.client.send_code_request(phone)
        cs.phone_code_hash = sent.phone_code_hash
        cs.status = "code_required"
    except Exception as exc:  # noqa: BLE001
        cs.status = "error"
        cs.error = f"не удалось отправить код: {exc}"
    return cs


async def submit_code(user_id: int, code: str) -> ConnectSession | None:
    cs = manager.get(user_id)
    if not cs or not cs.client or cs.status != "code_required":
        return cs
    try:
        await cs.client.sign_in(phone=cs.entered_phone, code=code, phone_code_hash=cs.phone_code_hash)
        await _finish(cs)
    except SessionPasswordNeededError:
        cs.status = "password_required"
    except Exception as exc:  # noqa: BLE001
        cs.status = "error"
        cs.error = f"неверный код: {exc}"
    return cs


def get(user_id: int) -> ConnectSession | None:
    return manager.get(user_id)


def pop_if_connected(user_id: int) -> ConnectSession | None:
    cs = manager.get(user_id)
    if cs and cs.status == "connected":
        return manager.pop(user_id)
    return None
