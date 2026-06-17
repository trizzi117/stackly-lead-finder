"""Запустить фоновые сервисы вместе: бот-поллер + оркестратор.

Запуск:  python -m app.run_all
(Веб-приложение запускается отдельно: uvicorn app.main:app)
"""
from __future__ import annotations

import asyncio

from . import bot, orchestrator


async def main() -> None:
    await asyncio.gather(bot.main(), orchestrator.main())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nОстановлено.")
