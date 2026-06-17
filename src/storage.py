"""Хранилище лидов на SQLite (WAL — параллельное чтение дашбордом во время записи воркером)."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT    NOT NULL,
    chat_id       INTEGER,
    chat_title    TEXT,
    message_id    INTEGER,
    sender_id     INTEGER,
    sender_name   TEXT,
    username      TEXT,
    text          TEXT    NOT NULL,
    keyword       TEXT,
    classification TEXT,
    score         INTEGER DEFAULT 0,
    intent        TEXT,
    reply         TEXT,
    status        TEXT    DEFAULT 'new',
    note          TEXT    DEFAULT '',
    link          TEXT,
    UNIQUE(chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_leads_created   ON leads(created_at);
CREATE INDEX IF NOT EXISTS idx_leads_class      ON leads(classification);
CREATE INDEX IF NOT EXISTS idx_leads_status     ON leads(status);
"""


class Storage:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ── запись ──────────────────────────────────────────────
    def add_lead(self, lead: dict[str, Any]) -> int | None:
        """Вставляет лид. Возвращает id или None, если дубликат (chat_id+message_id)."""
        cols = (
            "created_at", "chat_id", "chat_title", "message_id", "sender_id",
            "sender_name", "username", "text", "keyword", "classification",
            "score", "intent", "reply", "status", "link",
        )
        values = [lead.get(c) for c in cols]
        placeholders = ", ".join("?" for _ in cols)
        sql = f"INSERT OR IGNORE INTO leads ({', '.join(cols)}) VALUES ({placeholders})"
        cur = self._conn.execute(sql, values)
        self._conn.commit()
        return cur.lastrowid if cur.rowcount else None

    def update_status(self, lead_id: int, status: str) -> None:
        self._conn.execute("UPDATE leads SET status=? WHERE id=?", (status, lead_id))
        self._conn.commit()

    def update_note(self, lead_id: int, note: str) -> None:
        self._conn.execute("UPDATE leads SET note=? WHERE id=?", (note, lead_id))
        self._conn.commit()

    # ── чтение ──────────────────────────────────────────────
    def list_leads(
        self,
        classification: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        sql = "SELECT * FROM leads WHERE 1=1"
        params: list[Any] = []
        if classification and classification != "all":
            sql += " AND classification=?"
            params.append(classification)
        if status and status != "all":
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY datetime(created_at) DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def get_lead(self, lead_id: int) -> dict | None:
        row = self._conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        return dict(row) if row else None

    def stats(self) -> dict:
        c = self._conn
        total = c.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        today_iso = date.today().isoformat()
        today = c.execute(
            "SELECT COUNT(*) FROM leads WHERE substr(created_at,1,10)=?", (today_iso,)
        ).fetchone()[0]
        by_class = {
            r["classification"] or "—": r["n"]
            for r in c.execute(
                "SELECT classification, COUNT(*) n FROM leads GROUP BY classification"
            ).fetchall()
        }
        by_status = {
            r["status"] or "—": r["n"]
            for r in c.execute(
                "SELECT status, COUNT(*) n FROM leads GROUP BY status"
            ).fetchall()
        }
        top_keywords = [
            {"keyword": r["keyword"], "n": r["n"]}
            for r in c.execute(
                "SELECT keyword, COUNT(*) n FROM leads WHERE keyword IS NOT NULL "
                "GROUP BY keyword ORDER BY n DESC LIMIT 10"
            ).fetchall()
        ]
        by_day = [
            {"day": r["d"], "n": r["n"]}
            for r in c.execute(
                "SELECT substr(created_at,1,10) d, COUNT(*) n FROM leads "
                "GROUP BY d ORDER BY d DESC LIMIT 14"
            ).fetchall()
        ]
        return {
            "total": total,
            "today": today,
            "by_class": by_class,
            "by_status": by_status,
            "top_keywords": top_keywords,
            "by_day": list(reversed(by_day)),
        }

    def close(self) -> None:
        self._conn.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
