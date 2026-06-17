"""Модели мульти-тенант SaaS."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .db import Base


def utcnow() -> datetime:
    # наивный UTC: SQLite отдаёт даты без tzinfo, поэтому держим всё наивным,
    # иначе сравнение trial_ends_at > utcnow() падает (naive vs aware).
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=utcnow)
    is_admin = Column(Boolean, default=False)

    # настройки лидогенерации (заполняются в кабинете)
    business_context = Column(Text, default="")
    alert_chat_id = Column(String(64), default="")     # chat_id в личке с ботом (пуши+биллинг)
    bot_linked = Column(Boolean, default=False)        # подключён ли наш Telegram-бот
    link_token = Column(String(64), nullable=True)     # одноразовый токен привязки бота
    hot_threshold = Column(Integer, default=70)
    keywords = Column(Text, default="[]")               # JSON-список
    stop_words = Column(Text, default="[]")             # JSON-список
    niche_label = Column(String(120), default="")

    subscription = relationship("Subscription", uselist=False, back_populates="user",
                                cascade="all, delete-orphan")
    tg_account = relationship("TgAccount", uselist=False, back_populates="user",
                              cascade="all, delete-orphan")
    chats = relationship("MonitoredChat", back_populates="user",
                         cascade="all, delete-orphan")
    leads = relationship("Lead", back_populates="user", cascade="all, delete-orphan")

    # helpers для JSON-полей
    def get_keywords(self) -> list[str]:
        try:
            return json.loads(self.keywords or "[]")
        except json.JSONDecodeError:
            return []

    def set_keywords(self, items: list[str]) -> None:
        self.keywords = json.dumps(items, ensure_ascii=False)

    def get_stop_words(self) -> list[str]:
        try:
            return json.loads(self.stop_words or "[]")
        except json.JSONDecodeError:
            return []

    def set_stop_words(self, items: list[str]) -> None:
        self.stop_words = json.dumps(items, ensure_ascii=False)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    plan = Column(String(32), default="none")          # none/starter/pro/business/agency
    status = Column(String(32), default="inactive")    # inactive/trialing/active/canceled/expired
    trial_ends_at = Column(DateTime, nullable=True)
    current_period_end = Column(DateTime, nullable=True)
    provider = Column(String(32), default="manual")    # manual/lava/cryptomus/yookassa
    provider_ref = Column(String(128), default="")     # id платежа/подписки у провайдера

    user = relationship("User", back_populates="subscription")

    def is_active(self) -> bool:
        now = utcnow()
        if self.status == "trialing" and self.trial_ends_at:
            return self.trial_ends_at > now
        if self.status == "active" and self.current_period_end:
            return self.current_period_end > now
        return self.status == "active" and self.current_period_end is None


class TgAccount(Base):
    __tablename__ = "tg_accounts"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    session_string = Column(Text, default="")          # Telethon StringSession
    tg_user_id = Column(Integer, nullable=True)
    username = Column(String(64), nullable=True)
    phone = Column(String(32), nullable=True)
    status = Column(String(32), default="none")        # none/connecting/connected/error
    connected_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="tg_account")


class MonitoredChat(Base):
    __tablename__ = "monitored_chats"
    __table_args__ = (UniqueConstraint("user_id", "chat_id", name="uq_user_chat"),)
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    chat_id = Column(Integer, nullable=False)
    title = Column(String(255), default="")
    username = Column(String(64), nullable=True)
    is_channel = Column(Boolean, default=False)        # True=канал, False=группа
    active = Column(Boolean, default=True)

    user = relationship("User", back_populates="chats")


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (UniqueConstraint("user_id", "chat_id", "message_id", name="uq_user_msg"),)
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=utcnow, index=True)
    chat_id = Column(Integer)
    chat_title = Column(String(255))
    message_id = Column(Integer)
    sender_id = Column(Integer)
    sender_name = Column(String(255))
    username = Column(String(64), nullable=True)
    text = Column(Text, nullable=False)
    keyword = Column(String(120))
    classification = Column(String(16))                # hot/warm/cold
    score = Column(Integer, default=0)
    intent = Column(String(255))
    reply = Column(Text)
    status = Column(String(32), default="new")         # new/contacted/converted/dismissed
    note = Column(Text, default="")
    link = Column(String(255), nullable=True)

    user = relationship("User", back_populates="leads")
