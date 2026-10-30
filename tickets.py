"""
tickets.py - stores maintenance tickets in a SQLite database (a single file: data/tickets.db).

WHY SQLite: it's built into Python, needs no server, and is a real SQL database -
the same CREATE TABLE / INSERT / SELECT you've been learning. Moving to Postgres
later only changes this file.
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import config

COLUMNS = ["id", "code", "created_at", "updated_at", "building", "room", "issue", "category",
           "urgency", "status", "language", "channel", "student_message"]
STATUSES = ["Open", "In Progress", "Resolved"]


class TicketStore:
    def __init__(self, db_path: Path = config.DB_FILE):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS tickets (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    code            TEXT UNIQUE,
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL,
                    building        TEXT,
                    room            TEXT,
                    issue           TEXT NOT NULL,
                    category        TEXT,
                    urgency         TEXT,
                    status          TEXT DEFAULT 'Open',
                    language        TEXT,
                    channel         TEXT,
                    student_message TEXT
                )""")

    def _connect(self):
        # a fresh connection per call keeps it safe with Streamlit's threads
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        return db

    def create(self, building, room, issue, category="Other", urgency="normal",
               language="English", channel="text", student_message="", created_at=None) -> dict:
        now = (created_at or datetime.now()).isoformat(timespec="seconds")
        with self._connect() as db:
            cur = db.execute(
                """INSERT INTO tickets (created_at, updated_at, building, room, issue, category,
                                        urgency, status, language, channel, student_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'Open', ?, ?, ?)""",
                (now, now, building, room, issue, category, urgency, language, channel, student_message))
            code = f"AV-{1000 + cur.lastrowid}"          # friendly ID students can say out loud
            db.execute("UPDATE tickets SET code = ? WHERE id = ?", (code, cur.lastrowid))
        return self.get(code)

    def get(self, code: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM tickets WHERE code = ?", (code.upper(),)).fetchone()
        return with_deadline(dict(row)) if row else None

    def all(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM tickets ORDER BY id DESC").fetchall()
        return [with_deadline(dict(r)) for r in rows]

    def update_status(self, code: str, status: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE tickets SET status = ?, updated_at = ? WHERE code = ?",
                       (status, datetime.now().isoformat(timespec="seconds"), code))

    def load_sample_tickets(self) -> None:
        """Clearly-fake tickets so the staff dashboard has something to show in a demo."""
        now = datetime.now()
        samples = [
            ("Thompson Hall", "214", "Heater not turning on", "Heating/AC", "urgent", 3),
            ("Wylie Hall", "108", "Bathroom sink leaking under the cabinet", "Plumbing", "normal", 16),
            ("Buchanan Hall", "302", "Desk lamp outlet not working", "Electrical", "normal", 5),
            ("Villa Ventura", "B12", "Front door lock sticking", "Door/Lock", "urgent", 1),
            ("Thompson Hall", "118", "Shower drain clogged", "Plumbing", "normal", 9),
            ("Buchanan Hall", "205", "Mini fridge making loud noise", "Appliance", "normal", 2),
        ]
        for building, room, issue, category, urgency, days_ago in samples:
            self.create(building, room, issue, category, urgency, channel="sample",
                        student_message="[SAMPLE DATA]", created_at=now - timedelta(days=days_ago))

    def clear(self) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM tickets")


def with_deadline(ticket: dict) -> dict:
    """Add the handbook repair deadline: 2 days for emergencies/urgent, 2 weeks for regular repairs."""
    created = datetime.fromisoformat(ticket["created_at"])
    days = config.SLA_DAYS.get(ticket.get("urgency") or "normal", 14)
    due = created + timedelta(days=days)
    ticket["due_at"] = due.isoformat(timespec="seconds")
    ticket["overdue"] = ticket.get("status") != "Resolved" and datetime.now() > due
    return ticket
