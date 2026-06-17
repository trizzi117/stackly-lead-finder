"""Отправка сообщений через платформенный Telegram-бот (лиды, биллинг, аккаунт)."""
from __future__ import annotations

import os

import httpx

TOKEN = os.getenv("PLATFORM_BOT_TOKEN", "").strip()
USERNAME = os.getenv("PLATFORM_BOT_USERNAME", "").strip().lstrip("@")  # @ срезаем — в t.me его быть не должно


def _url(method: str) -> str:
    return f"https://api.telegram.org/bot{TOKEN}/{method}"


def deep_link(token: str) -> str:
    """Ссылка для привязки бота: t.me/<bot>?start=<token>."""
    if USERNAME:
        return f"https://t.me/{USERNAME}?start={token}"
    return ""


def send_html(chat_id: str | int, text: str) -> bool:
    if not TOKEN or not chat_id:
        return False
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.post(_url("sendMessage"), json={
                "chat_id": chat_id, "text": text,
                "parse_mode": "HTML", "disable_web_page_preview": True,
            })
            return r.status_code < 400
    except httpx.HTTPError:
        return False


async def send_html_async(chat_id: str | int, text: str) -> bool:
    if not TOKEN or not chat_id:
        return False
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(_url("sendMessage"), json={
                "chat_id": chat_id, "text": text,
                "parse_mode": "HTML", "disable_web_page_preview": True,
            })
            return r.status_code < 400
    except httpx.HTTPError:
        return False
