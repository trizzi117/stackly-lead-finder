"""Пуш-алерты горячих лидов в Telegram через Bot API."""
from __future__ import annotations

import html

import httpx

_EMOJI = {"hot": "🔥", "warm": "🌤", "cold": "❄️"}
_LABEL = {"hot": "ГОРЯЧИЙ", "warm": "ТЁПЛЫЙ", "cold": "ХОЛОДНЫЙ"}


class Notifier:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)

    def _format(self, lead: dict) -> str:
        cls = lead.get("classification", "warm")
        e = _EMOJI.get(cls, "•")
        label = _LABEL.get(cls, cls.upper())
        score = lead.get("score", 0)
        sender = html.escape(lead.get("sender_name") or "—")
        username = lead.get("username")
        who = f"{sender} (@{username})" if username else sender
        chat = html.escape(lead.get("chat_title") or "—")
        text = html.escape((lead.get("text") or "")[:500])
        intent = html.escape(lead.get("intent") or "")
        reply = html.escape(lead.get("reply") or "")
        link = lead.get("link")

        parts = [
            f"{e} <b>{label} ЛИД</b>  ·  score {score}",
            f"👤 {who}   💬 {chat}",
            "",
            f"<blockquote>{text}</blockquote>",
        ]
        if intent:
            parts.append(f"🧠 <b>Потребность:</b> {intent}")
        if reply:
            parts.append(f"\n✍️ <b>Ответ:</b>\n{reply}")
        if link:
            parts.append(f"\n🔗 <a href=\"{html.escape(link)}\">Открыть в Telegram</a>")
        return "\n".join(parts)

    async def send(self, lead: dict) -> bool:
        if not self.enabled:
            return False
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": self._format(lead),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload)
                return resp.status_code < 400
        except httpx.HTTPError:
            return False
