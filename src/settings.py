"""Загрузка конфигурации из .env и config/niches.yaml."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"

load_dotenv(ROOT / ".env")


def _int(name: str, default: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    # Telegram MTProto
    api_id: int = field(default_factory=lambda: _int("TG_API_ID"))
    api_hash: str = field(default_factory=lambda: os.getenv("TG_API_HASH", "").strip())
    session: str = field(default_factory=lambda: os.getenv("TG_SESSION", "stackly_radar").strip())
    phone: str = field(default_factory=lambda: os.getenv("TG_PHONE", "").strip())

    # Alert bot
    alert_bot_token: str = field(default_factory=lambda: os.getenv("ALERT_BOT_TOKEN", "").strip())
    alert_chat_id: str = field(default_factory=lambda: os.getenv("ALERT_CHAT_ID", "").strip())

    # LLM
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.deepseek.com").strip().rstrip("/"))
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", "").strip())
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "deepseek-chat").strip())

    # Business / niche
    business_context: str = field(default_factory=lambda: os.getenv("BUSINESS_CONTEXT", "").strip())
    active_niche: str = field(default_factory=lambda: os.getenv("ACTIVE_NICHE", "automation_ru").strip())
    hot_threshold: int = field(default_factory=lambda: _int("HOT_THRESHOLD", 70))

    # Storage
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "data/stackly.db").strip())

    # Filled in __post_init__
    keywords: list[str] = field(default_factory=list)
    stop_words: list[str] = field(default_factory=list)
    niche_title: str = ""
    monitored_chats: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._load_niche()
        self._load_chats()

    def _load_niche(self) -> None:
        path = CONFIG_DIR / "niches.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        niche = data.get(self.active_niche)
        if not niche:
            raise ValueError(
                f"Ниша '{self.active_niche}' не найдена в niches.yaml. "
                f"Доступны: {', '.join(data.keys())}"
            )
        self.niche_title = niche.get("title", self.active_niche)
        self.keywords = [k.lower().strip() for k in niche.get("keywords", []) if k.strip()]
        self.stop_words = [s.lower().strip() for s in niche.get("stop_words", []) if s.strip()]

    def _load_chats(self) -> None:
        path = CONFIG_DIR / "chats.txt"
        if not path.exists():
            return
        out: list = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            token = line.lstrip("@")
            # числовой id → int (Telethon различает id и username по типу)
            if token.lstrip("-").isdigit():
                out.append(int(token))
            else:
                out.append(token)
        self.monitored_chats = out

    @property
    def db_abspath(self) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else (ROOT / p)

    def validate_worker(self) -> list[str]:
        """Что обязательно для запуска воркера."""
        problems = []
        if not self.api_id:
            problems.append("TG_API_ID не задан")
        if not self.api_hash:
            problems.append("TG_API_HASH не задан")
        if not self.llm_api_key:
            problems.append("LLM_API_KEY не задан")
        if not self.keywords:
            problems.append(f"в нише '{self.active_niche}' нет ключевых слов")
        return problems


settings = Settings()
