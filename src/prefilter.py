"""Дешёвый пред-фильтр: keyword/stop-word матчинг ДО обращения к LLM.

Это условие выживания юнит-экономики: AI трогает только 1–5% сообщений,
прошедших этот фильтр. Без него каждый мониторинг жжёт деньги.
"""
from __future__ import annotations

import re


def _normalize(text: str) -> str:
    text = text.lower()
    text = text.replace("ё", "е")
    # схлопываем всё, что не буква/цифра, в пробелы — устойчиво к пунктуации/эмодзи
    text = re.sub(r"[^0-9a-zа-я]+", " ", text)
    return f" {text.strip()} "


class PreFilter:
    def __init__(self, keywords: list[str], stop_words: list[str]) -> None:
        self.keywords = [_normalize(k).strip() for k in keywords if k.strip()]
        self.stop_words = [_normalize(s).strip() for s in stop_words if s.strip()]

    def match(self, text: str | None) -> str | None:
        """Возвращает сработавшее ключевое слово или None.

        None если: пусто, есть стоп-слово, или нет ни одного ключевого.
        """
        if not text:
            return None
        norm = _normalize(text)

        # стоп-слова: подстрочный матч (напр. «крипт» отсекает «криптовалюту»)
        for stop in self.stop_words:
            if stop and stop in norm:
                return None

        # ключевые: первое совпадение
        for kw in self.keywords:
            if kw and kw in norm:
                return kw
        return None
