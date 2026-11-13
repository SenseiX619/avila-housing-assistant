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
           "urgency", "status", "language", "channel", "student_message",
           "assigned_to", "assigned_at", "resolved_at", "student_feedback"]
# A ticket's life: Submitted (the student reported it) -> Assigned (staff gave it to someone) -> In Progress
# -> Resolved (staff say it's fixed). The student then confirms: student_feedback = "fixed", or "not fixed",
# which reopens it.
STATUSES = ["Submitted", "Assigned", "In Progress", "Resolved"]
NEW_COLUMNS = ["assigned_to", "assigned_at", "resolved_at", "student_feedback"]     # added in v1.18


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
                    status          TEXT DEFAULT 'Submitted',
                    language        TEXT,
                    channel         TEXT,
                    student_message TEXT
                )""")
            # Databases made before v1.18 lack the new columns and say "Open": bring them up to date.
            have = {row["name"] for row in db.execute("PRAGMA table_info(tickets)")}
            for column in NEW_COLUMNS:
                if column not in have:
                    db.execute(f"ALTER TABLE tickets ADD COLUMN {column} TEXT")
            db.execute("UPDATE tickets SET status = 'Submitted' WHERE status = 'Open'")

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
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'Submitted', ?, ?, ?)""",
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

    def update_status(self, code: str, status: str, assigned_to: str | None = None) -> dict | None:
        """Staff move a ticket along. Also records WHEN: assigned_at the first time someone has it, resolved_at
        when it's fixed - that's what the dashboard's response times are measured from.
        assigned_to=None leaves the assignee as it is; "" removes it."""
        ticket = self.get(code)
        if not ticket:
            return None
        now = datetime.now().isoformat(timespec="seconds")
        changes = {"status": status, "updated_at": now}
        if assigned_to is not None:
            changes["assigned_to"] = assigned_to.strip() or None
        if status == "Submitted":
            changes["assigned_at"] = None                   # back in the queue: nobody has it yet
        elif not ticket["assigned_at"]:
            changes["assigned_at"] = now                    # Assigned, In Progress or Resolved: someone has it
        changes["resolved_at"] = now if status == "Resolved" else None
        return self._update(code, changes)

    def record_feedback(self, code: str, fixed: bool) -> dict | None:
        """The student answers "Did that fix it?". No -> the ticket is reopened: back to whoever had it
        (Assigned), or to the queue (Submitted)."""
        ticket = self.get(code)
        if not ticket:
            return None
        changes = {"student_feedback": "fixed" if fixed else "not fixed",
                   "updated_at": datetime.now().isoformat(timespec="seconds")}
        if not fixed:
            changes.update(status="Assigned" if ticket["assigned_to"] else "Submitted", resolved_at=None)
        return self._update(code, changes)

    def _update(self, code: str, changes: dict) -> dict:
        names = ", ".join(f"{column} = ?" for column in changes)        # column names are ours, never typed in
        with self._connect() as db:
            db.execute(f"UPDATE tickets SET {names} WHERE code = ?", (*changes.values(), code.upper()))
        return self.get(code)

    def update_room(self, code: str, room: str) -> dict:
        """Fix the room on a ticket (e.g. speech misheard "322" as "3-2")."""
        with self._connect() as db:
            db.execute("UPDATE tickets SET room = ?, updated_at = ? WHERE code = ?",
                       (room, datetime.now().isoformat(timespec="seconds"), code))
        return self.get(code)

    def update_location(self, code: str, building: str, room: str) -> dict:
        """Add the place to an emergency ticket that was saved before the student said where it is."""
        with self._connect() as db:
            db.execute("UPDATE tickets SET building = ?, room = ?, updated_at = ? WHERE code = ?",
                       (building, room, datetime.now().isoformat(timespec="seconds"), code))
        return self.get(code)

    def load_sample_tickets(self) -> None:
        """Clearly-fake tickets so the staff dashboard has something to show in a demo."""
        now = datetime.now()
        samples = [
            ("Thompson Hall", "1214", "Heater not turning on", "Heating/AC", "urgent", 3),
            ("Wylie Hall", "1408", "Bathroom sink leaking under the cabinet", "Plumbing", "normal", 16),
            ("Buchanan Hall", "502", "Desk lamp outlet not working", "Electrical", "normal", 5),
            ("Villa Ventura", "212", "Front door lock sticking", "Door/Lock", "urgent", 1),
            ("Thompson Hall", "1118", "Shower drain clogged", "Plumbing", "normal", 9),
            ("Buchanan Hall", "405", "Mini fridge making loud noise", "Appliance", "normal", 2),
        ]
        for building, room, issue, category, urgency, days_ago in samples:
            self.create(building, room, issue, category, urgency, channel="sample",
                        student_message="[SAMPLE DATA]", created_at=now - timedelta(days=days_ago))

    def clear(self) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM tickets")

    def reset_demo(self) -> None:
        """Start a demo fresh: delete every ticket, number from AV-1001 again, load the sample tickets.
        (Deleting tickets alone doesn't reset the numbering - SQLite remembers the last ID.)"""
        with self._connect() as db:
            db.execute("DELETE FROM tickets")
            db.execute("DELETE FROM sqlite_sequence WHERE name = 'tickets'")
        self.load_sample_tickets()


def with_deadline(ticket: dict) -> dict:
    """Add the handbook repair deadline: 2 days for emergencies/urgent, 2 weeks for regular repairs."""
    created = datetime.fromisoformat(ticket["created_at"])
    days = config.SLA_DAYS.get(ticket.get("urgency") or "normal", 14)
    due = created + timedelta(days=days)
    ticket["due_at"] = due.isoformat(timespec="seconds")
    ticket["overdue"] = ticket.get("status") != "Resolved" and datetime.now() > due
    return ticket
