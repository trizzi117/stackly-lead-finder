"""Наполнить базу демо-лидами — посмотреть дашборд без подключения Telegram.

Запуск:  python -m scripts.seed_demo
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.settings import settings
from src.storage import Storage

DEMO = [
    ("IT-стартапы", "Алексей М.", "alex_m", "Ищу разработчика для e-commerce, нужен опыт с платёжными интеграциями, бюджет есть", "ищу разработчика", "hot", 88, "нужен разработчик e-commerce", "Привет! Делаю интеграции платёжек и автоматизацию заказов под ключ. Какой стек на проекте и сроки?"),
    ("Бизнес-клуб", "Наталья С.", "natalies", "Нужна помощь с автоматизацией CRM, заявки теряются, готова обсудить", "нужна автоматизация", "hot", 82, "автоматизация CRM, теряются заявки", "Здравствуйте! Как раз чиню «протекающие» заявки — связываю формы, CRM и Telegram. В какой CRM сейчас работаете?"),
    ("Фриланс хаб", "Денис К.", "denisk", "Посоветуйте, кто сделает телеграм-бота для приёма заказов?", "посоветуйте кто сделает", "warm", 64, "телеграм-бот для заказов", "Привет! Собираю таких ботов с приёмом заявок и выгрузкой в таблицу/CRM. Расскажете, что должен уметь бот?"),
    ("Малый бизнес РФ", "Игорь П.", None, "Нужен сайт с формой заявки и чтобы заявки сразу падали в телеграм", "нужен сайт", "hot", 79, "сайт с формой заявок в Telegram", "Здравствуйте! Делаю ровно это: лендинг + форма → мгновенно в Telegram и CRM. Есть пример ниши/референс?"),
    ("Автоматизация", "Марина Т.", "marina_t", "Кто-нибудь настраивал n8n для интеграции amoCRM и сайта?", "n8n", "warm", 70, "интеграция n8n + amoCRM", "Привет! Да, делаю связки n8n ↔ amoCRM ↔ сайт. Что нужно синхронизировать — лиды, сделки, статусы?"),
    ("Стартап чат", "Сергей А.", "sergeya", "Ищем кто автоматизирует отчётность, руками собирать задолбались", "автоматизировать", "warm", 61, "автоматизация отчётности", "Привет! Собираю авто-отчёты (данные → PDF/дашборд → Telegram по расписанию). Откуда берутся данные сейчас?"),
    ("Реклама и SMM", "Ольга В.", "olgav", "Просто делюсь мыслями про рынок, ничего не ищу", "—", "cold", 12, "оффтоп", ""),
]


def main() -> None:
    storage = Storage(settings.db_abspath)
    now = datetime.now(timezone.utc).astimezone()
    inserted = 0
    for i, (chat, name, uname, text, kw, cls, score, intent, reply) in enumerate(DEMO):
        lead = {
            "created_at": (now - timedelta(hours=i * 5)).isoformat(timespec="seconds"),
            "chat_id": -1000000000000 - i,
            "chat_title": chat,
            "message_id": 1000 + i,
            "sender_id": 5000 + i,
            "sender_name": name,
            "username": uname,
            "text": text,
            "keyword": kw,
            "classification": cls,
            "score": score,
            "intent": intent,
            "reply": reply,
            "status": "new",
            "link": f"https://t.me/{uname}" if uname else None,
        }
        if storage.add_lead(lead) is not None:
            inserted += 1
    print(f"Готово: добавлено {inserted} демо-лидов в {settings.db_abspath}")
    print("Запусти дашборд:  uvicorn dashboard.app:app --reload  → http://127.0.0.1:8000")


if __name__ == "__main__":
    main()
