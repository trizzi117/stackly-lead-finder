"""Создать БД + демо-аккаунт с лидами, чтобы сразу посмотреть кабинет.

Запуск:  python -m app.seed
Вход:    demo@stackly.local / demo123
"""
from __future__ import annotations

from datetime import timedelta

from . import billing
from .auth import create_user
from .db import SessionLocal, init_db
from .models import Lead, MonitoredChat, TgAccount, User, utcnow

EMAIL, PW = "demo@stackly.local", "demo123"

KEYWORDS = ["ищу разработчика", "нужен бот", "нужна автоматизация", "настроить crm",
            "нужен сайт", "посоветуйте кто сделает", "n8n", "телеграм бот"]
STOPS = ["вакансия", "резюме", "ищу работу", "бесплатно", "курс"]

DEMO = [
    ("IT-стартапы", "Алексей М.", "alex_m", "Ищу разработчика для e-commerce, нужен опыт с платёжками, бюджет есть", "ищу разработчика", "hot", 88, "разработчик e-commerce", "Привет! Делаю интеграции платёжек и автоматизацию заказов. Какой стек и сроки?"),
    ("Бизнес-клуб", "Наталья С.", "natalies", "Нужна помощь с автоматизацией CRM, заявки теряются", "нужна автоматизация", "hot", 82, "автоматизация CRM", "Здравствуйте! Чиню «протекающие» заявки — связываю формы, CRM и Telegram. Какая CRM сейчас?"),
    ("Фриланс хаб", "Денис К.", "denisk", "Посоветуйте, кто сделает телеграм-бота для заказов?", "посоветуйте кто сделает", "warm", 64, "телеграм-бот для заказов", "Привет! Собираю таких ботов с выгрузкой в таблицу/CRM. Что должен уметь бот?"),
    ("Малый бизнес РФ", "Игорь П.", None, "Нужен сайт с формой заявки, чтобы заявки падали в телеграм", "нужен сайт", "hot", 79, "сайт с формой в Telegram", "Здравствуйте! Делаю ровно это: лендинг + форма → Telegram и CRM. Есть референс?"),
    ("Автоматизация", "Марина Т.", "marina_t", "Кто настраивал n8n для интеграции amoCRM и сайта?", "n8n", "warm", 70, "интеграция n8n + amoCRM", "Привет! Делаю связки n8n ↔ amoCRM ↔ сайт. Что синхронизируем — лиды, сделки, статусы?"),
    ("Стартап чат", "Сергей А.", "sergeya", "Ищем кто автоматизирует отчётность", "нужна автоматизация", "warm", 61, "автоматизация отчётности", "Привет! Собираю авто-отчёты (данные → PDF/дашборд → Telegram). Откуда данные сейчас?"),
    ("Реклама и SMM", "Ольга В.", "olgav", "Просто делюсь мыслями про рынок", "—", "cold", 12, "оффтоп", ""),
]

# (title, username, is_channel, active)
DEMO_CHATS = [
    ("IT-стартапы РФ", "it_startups_ru", False, True),
    ("Фриланс хаб", "freelance_hub", False, True),
    ("Бизнес-клуб", "biznes_club", False, True),
    ("Маркетинг и SMM", "smm_marketing", False, False),
    ("Автоматизация бизнеса", "auto_biz", False, False),
    ("Предприниматели Москва", "msk_business", False, False),
    ("Стартап-нетворкинг", "startup_net", False, False),
    ("Вакансии IT", "it_jobs_channel", True, False),
    ("Дайджест стартапов", "startup_digest", True, False),
    ("Малый бизнес РФ", "smallbiz_ru", False, False),
    ("Чат разработчиков", "devs_chat", False, False),
    ("Ремонт и дизайн", "remont_design", False, False),
]


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == EMAIL).first()
        if not user:
            user = create_user(db, EMAIL, PW)
            billing.start_trial(db, user)
        user.business_context = ("Я Stackly — внедряю автоматизацию (n8n, CRM, Telegram-боты, "
                                 "сайты с заявками) для малого бизнеса в РФ.")
        user.set_keywords(KEYWORDS)
        user.set_stop_words(STOPS)
        db.commit()

        # демо: «подключённый» аккаунт + чаты, чтобы страница «Чаты» была живой без Telegram
        if not user.tg_account:
            db.add(TgAccount(user_id=user.id, status="connected", username="demo_account",
                             session_string="", tg_user_id=0, connected_at=utcnow()))
        for i, (title, uname, is_ch, active) in enumerate(DEMO_CHATS):
            exists = db.query(MonitoredChat).filter(
                MonitoredChat.user_id == user.id, MonitoredChat.chat_id == -2000 - i).first()
            if not exists:
                db.add(MonitoredChat(user_id=user.id, chat_id=-2000 - i, title=title,
                                     username=uname, is_channel=is_ch, active=active))
        db.commit()

        now = utcnow()
        added = 0
        for i, (chat, name, uname, text, kw, cls, score, intent, reply) in enumerate(DEMO):
            exists = db.query(Lead).filter(Lead.user_id == user.id, Lead.chat_id == -1000 - i,
                                           Lead.message_id == 1000 + i).first()
            if exists:
                continue
            db.add(Lead(user_id=user.id, created_at=now - timedelta(hours=i * 5),
                        chat_id=-1000 - i, chat_title=chat, message_id=1000 + i,
                        sender_id=5000 + i, sender_name=name, username=uname, text=text,
                        keyword=kw, classification=cls, score=score, intent=intent,
                        reply=reply, status="new",
                        link=f"https://t.me/{uname}" if uname else None))
            added += 1
        db.commit()
        print(f"Готово. Вход: {EMAIL} / {PW}. Демо-лидов добавлено: {added}")
        print("Запусти сайт:  uvicorn app.main:app --reload  →  http://127.0.0.1:8000")
    finally:
        db.close()


if __name__ == "__main__":
    main()
