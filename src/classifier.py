"""AI-классификация лида (горячий/тёплый/холодный) + генерация первого ответа."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .llm import LLM, LLMError

VALID = {"hot", "warm", "cold"}

SYSTEM = """Ты — ассистент по квалификации лидов. Тебе дают одно сообщение из Telegram-чата.
Контекст бизнеса (от чьего лица квалифицируем):
{business}

Оценивай намерение заказать/купить ИМЕННО НАШУ услугу из профиля выше — не просто совпадение по теме «бизнес». Если запрос про ДРУГУЮ сферу/услугу, которой у нас нет, — это НЕ наш клиент, даже если есть слова «нужно», «ищу», «сколько стоит».

Ставь "cold" и низкий score (0–25), если это:
- запрос про ДРУГУЮ услугу/сферу, которой нет в нашем профиле (напр. у нас автоматизация, а спрашивают про крипту/блокчейн/юристов/доставку);
- реклама, анонс, приглашение, новость, расписание, пост от имени канала, вакансия;
- человек рассказывает о СВОЁМ бизнесе/продукте или предлагает СВОИ услуги (это не клиент);
- общий трёп, мнение, оффтоп.
"warm" (40–70) — человек выражает потребность, близкую к услуге, но без срочности и прямого запроса.
"hot" (75–100) — ТОЛЬКО если живой человек прямо ищет такую услугу сейчас: «ищу», «нужен», «посоветуйте кто», «кто делает», есть бюджет/сроки.

Тематический пост без личного запроса — всегда cold. Сомневаешься между warm и hot — ставь warm.

Верни СТРОГО JSON без пояснений:
{{
  "classification": "hot|warm|cold",
  "score": <0-100 — насколько это реальный платящий клиент именно для нас>,
  "intent": "<что человек хочет, 3-7 слов; для cold — почему это не лид>",
  "reply": "<для hot/warm: готовый первый ответ на русском, по делу, без спама, 1-3 предложения; для cold: пустая строка>"
}}"""

USER = 'Сообщение из чата «{chat}»:\n"""\n{text}\n"""'


@dataclass
class Classification:
    classification: str
    score: int
    intent: str
    reply: str

    @property
    def is_hot(self) -> bool:
        return self.classification == "hot"


def _extract_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def _coerce(data: dict) -> Classification:
    cls = str(data.get("classification", "cold")).lower().strip()
    if cls not in VALID:
        cls = "cold"
    try:
        score = int(float(data.get("score", 0)))
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(100, score))
    return Classification(
        classification=cls,
        score=score,
        intent=str(data.get("intent", "")).strip()[:200],
        reply=str(data.get("reply", "")).strip()[:1000],
    )


class Classifier:
    def __init__(self, llm: LLM, business_context: str) -> None:
        self.llm = llm
        self.business_context = business_context or "Малый бизнес, оказывающий услуги."

    async def classify(self, text: str, chat_title: str = "") -> Classification:
        system = SYSTEM.format(business=self.business_context)
        user = USER.format(chat=chat_title or "—", text=text[:2000])
        try:
            raw = await self.llm.complete(system, user, json_mode=True)
            return _coerce(_extract_json(raw))
        except (LLMError, json.JSONDecodeError, ValueError):
            # деградируем безопасно: помечаем как warm на ручную проверку, без выдуманного ответа
            return Classification("warm", 50, "не удалось классифицировать (проверь вручную)", "")
