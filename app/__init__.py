"""Stackly Lead Finder — SaaS-приложение (мульти-тенант)."""
from pathlib import Path

from dotenv import load_dotenv

# Грузим .env ДО импорта подмодулей, которые читают os.getenv на уровне модуля
# (platform_bot, tg_connect, orchestrator). Иначе ключи из .env игнорируются.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
