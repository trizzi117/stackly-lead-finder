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

Оцени, является ли автор сообщения потенциальным КЛИЕНТОМ для этого бизнеса (человеком с потребностью купить/заказать), а не исполнителем, спамером или просто болтовнёй.

Классы:
- "hot"  — явное намерение купить/заказать ПРЯМО СЕЙЧАС («ищу», «нужен», «срочно», «готов оплатить», указан бюджет/сроки).
- "warm" — релевантная потребность, но без срочности или с сомнением.
- "cold" — тема рядом, но прямого запроса нет; или это исполнитель/реклама/оффтоп.

Верни СТРОГО JSON без пояснений:
{{
  "classification": "hot|warm|cold",
  "score": <0-100, уверенность что это горячий платящий клиент>,
  "intent": "<потребность клиента в 3-7 словах на русском>",
  "reply": "<готовый первый ответ на русском: по-человечески, по делу, без воды и спама, 1-3 предложения; помоги/уточни и мягко предложи помощь, не впаривай>"
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
