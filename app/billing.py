"""Биллинг: тарифы, лимиты, триал, активация. Провайдер-агностик.

Режимы провайдера:
  - manual    — активирует админ вручную (работает сразу, для первых клиентов «руками»);
  - lava      — Lava.ru/top (физлицо/самозанятый), вебхук подтверждает оплату;
  - cryptomus — крипто-оплата (без юрлица);
  - yookassa  — нужна регистрация бизнеса (ИП/самозанятый).
Деньги поедут после вставки ключей провайдера в .env. Сейчас всё работает в manual.
"""
from __future__ import annotations

import os
from datetime import timedelta

from sqlalchemy.orm import Session

from .models import Subscription, User, utcnow

# Тарифы. price_rub — ориентир для РФ-аудитории.
PLANS: dict[str, dict] = {
    "starter":  {"title": "Старт",     "price_rub": 990,  "chats": 9,   "leads_month": 500,    "ai_replies": False, "channel_search": False},
    "pro":      {"title": "Про",       "price_rub": 2900, "chats": 100, "leads_month": 5000,   "ai_replies": False, "channel_search": True},
    "business": {"title": "Бизнес",    "price_rub": 5900, "chats": 150, "leads_month": 15000,  "ai_replies": True,  "channel_search": True},
    "agency":   {"title": "Агентство", "price_rub": 9900, "chats": 200, "leads_month": 10**9,  "ai_replies": True,  "channel_search": True},
}
PLAN_ORDER = ["starter", "pro", "business", "agency"]

TRIAL_DAYS = 5
TRIAL_PLAN = "business"   # триал даёт полный функционал

DEFAULT_PROVIDER = os.getenv("BILLING_PROVIDER", "manual")


def start_trial(db: Session, user: User) -> Subscription:
    sub = user.subscription
    if sub is None:
        sub = Subscription(user_id=user.id)
        db.add(sub)
    sub.plan = TRIAL_PLAN
    sub.status = "trialing"
    sub.trial_ends_at = utcnow() + timedelta(days=TRIAL_DAYS)
    sub.provider = "manual"
    db.commit()
    db.refresh(sub)
    return sub


def activate(db: Session, user: User, plan: str, *, provider: str = "manual",
             ref: str = "", months: int = 1) -> Subscription:
    if plan not in PLANS:
        raise ValueError(f"неизвестный тариф: {plan}")
    sub = user.subscription or Subscription(user_id=user.id)
    if sub.id is None:
        db.add(sub)
    sub.plan = plan
    sub.status = "active"
    base = sub.current_period_end if (sub.current_period_end and sub.current_period_end > utcnow()) else utcnow()
    sub.current_period_end = base + timedelta(days=30 * months)
    sub.provider = provider
    sub.provider_ref = ref
    db.commit()
    db.refresh(sub)
    return sub


def cancel(db: Session, user: User) -> None:
    sub = user.subscription
    if sub:
        sub.status = "canceled"
        db.commit()


def limits(sub: Subscription | None) -> dict:
    if sub and sub.is_active() and sub.plan in PLANS:
        return PLANS[sub.plan]
    return {"title": "—", "price_rub": 0, "chats": 0, "leads_month": 0, "ai_replies": False}


def can_add_chat(db: Session, user: User) -> bool:
    from .models import MonitoredChat
    cap = limits(user.subscription).get("chats", 0)
    used = db.query(MonitoredChat).filter(MonitoredChat.user_id == user.id).count()
    return used < cap


# ── Платёжные ссылки / вебхуки (адаптеры) ───────────────────────────────
def create_payment_link(plan: str, user: User) -> str | None:
    """Вернуть ссылку на оплату у активного провайдера.

    Заглушка: реальную интеграцию (Lava/Cryptomus) подключаем ключами в .env.
    Пока возвращает None → фронт показывает «оплата вручную / напишите в Telegram».
    """
    provider = DEFAULT_PROVIDER
    if provider == "manual":
        return None
    # TODO(lava/cryptomus): здесь формируется invoice через API провайдера и
    # возвращается checkout-url. См. README §Биллинг.
    return None


def handle_webhook(db: Session, provider: str, payload: dict) -> bool:
    """Обработать вебхук провайдера: проверить подпись и активировать подписку.

    Заглушка-каркас: парсит user_id/plan/ref и активирует. Подпись проверяется
    секретом из .env (BILLING_WEBHOOK_SECRET) — добавить под конкретного провайдера.
    """
    try:
        user_id = int(payload.get("user_id"))
        plan = str(payload.get("plan"))
        ref = str(payload.get("ref", ""))
    except (TypeError, ValueError):
        return False
    user = db.get(User, user_id)
    if not user or plan not in PLANS:
        return False
    activate(db, user, plan, provider=provider, ref=ref)
    return True
