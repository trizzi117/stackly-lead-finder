"""Конвейер обработки одного сообщения: фильтр → AI → хранилище → алерт."""
from __future__ import annotations

import logging

from .classifier import Classifier
from .notifier import Notifier
from .prefilter import PreFilter
from .storage import Storage, now_iso

log = logging.getLogger("stackly.pipeline")


class Pipeline:
    def __init__(
        self,
        prefilter: PreFilter,
        classifier: Classifier,
        storage: Storage,
        notifier: Notifier,
        hot_threshold: int = 70,
    ) -> None:
        self.prefilter = prefilter
        self.classifier = classifier
        self.storage = storage
        self.notifier = notifier
        self.hot_threshold = hot_threshold

    async def process(self, msg: dict) -> dict | None:
        """msg: chat_id, chat_title, message_id, sender_id, sender_name, username, text, link."""
        text = msg.get("text") or ""

        # 1) дешёвый пред-фильтр — отсекаем 95%+ без затрат
        keyword = self.prefilter.match(text)
        if not keyword:
            return None

        # 2) дорогая AI-классификация — только для прошедших фильтр
        result = await self.classifier.classify(text, msg.get("chat_title", ""))

        # 3) запись (дубликаты по chat_id+message_id игнорируются)
        lead = {
            "created_at": now_iso(),
            "chat_id": msg.get("chat_id"),
            "chat_title": msg.get("chat_title"),
            "message_id": msg.get("message_id"),
            "sender_id": msg.get("sender_id"),
            "sender_name": msg.get("sender_name"),
            "username": msg.get("username"),
            "text": text,
            "keyword": keyword,
            "classification": result.classification,
            "score": result.score,
            "intent": result.intent,
            "reply": result.reply,
            "status": "new",
            "link": msg.get("link"),
        }
        lead_id = self.storage.add_lead(lead)
        if lead_id is None:
            return None  # дубликат — уже видели
        lead["id"] = lead_id

        log.info("ЛИД #%s [%s %s] %s: %.60s",
                 lead_id, result.classification, result.score,
                 msg.get("chat_title", ""), text)

        # 4) пуш только для горячих (по классу ИЛИ порогу score)
        if result.is_hot or result.score >= self.hot_threshold:
            await self.notifier.send(lead)

        return lead
