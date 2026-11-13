"""
tickets.py - stores maintenance tickets in a SQLite database (a single file: data/tickets.db).

WHY SQLite: it's built into Python, needs no server, and is a real SQL database -
the same CREATE TABLE / INSERT / SELECT you've been learning. Moving to Postgres
later only changes this file.
"""

import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import config

COLUMNS = ["id", "code", "created_at", "updated_at", "building", "room", "issue", "category",
           "urgency", "status", "language", "channel", "student_message",
           "assigned_to", "assigned_at", "resolved_at", "student_feedback", "reports"]
# A ticket's life: Submitted (the student reported it) -> Assigned (staff gave it to someone) -> In Progress
# -> Resolved (staff say it's fixed). The student then confirms: student_feedback = "fixed", or "not fixed",
# which reopens it.
STATUSES = ["Submitted", "Assigned", "In Progress", "Resolved"]
# Added later (v1.18, v1.20): older databases get them automatically. "reports" = how many students reported it.
NEW_COLUMNS = {"assigned_to": "TEXT", "assigned_at": "TEXT", "resolved_at": "TEXT", "student_feedback": "TEXT",
               "reports": "INTEGER DEFAULT 1"}
URGENCY_RANK = {"normal": 0, "urgent": 1, "emergency": 2}


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
            for column, kind in NEW_COLUMNS.items():
                if column not in have:
                    db.execute(f"ALTER TABLE tickets ADD COLUMN {column} {kind}")
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

    def find_open_duplicate(self, building: str, room: str, issue: str, category: str | None) -> dict | None:
        """An open ticket for the same problem in the same room (a roommate, or the same student again)."""
        new = {"issue": issue, "category": category}
        for ticket in self.all():
            if (ticket["status"] != "Resolved" and ticket["building"] == building
                    and str(ticket["room"]).upper() == str(room).upper() and same_problem(ticket, new)):
                return ticket
        return None

    def add_report(self, code: str, urgency: str, student_message: str) -> dict:
        """Someone else reported the same problem: count it, keep their words for staff, and raise the
        urgency if the new report is more serious (an outlet that's now smoking). Never lowers it."""
        ticket = self.get(code)
        higher = max(ticket["urgency"] or "normal", urgency or "normal", key=lambda u: URGENCY_RANK.get(u, 0))
        words = f"{ticket['student_message'] or ''} | Also reported: {student_message}".strip(" |")
        return self._update(code, {"reports": (ticket["reports"] or 1) + 1, "urgency": higher,
                                   "student_message": words,
                                   "updated_at": datetime.now().isoformat(timespec="seconds")})

    def load_sample_tickets(self) -> None:
        """Clearly-fake tickets so the staff dashboard has something to show in a demo, WITH a history:
        some waiting, some assigned or in progress, some fixed (one late), one reopened by the student.
        Times are hours/days after the report, so the response-time numbers are believable."""
        now = datetime.now()
        # (building, room, issue, category, urgency, days ago, status, team, hours to assign, days to fix, student)
        samples = [
            ("Thompson Hall", "1214", "Heater not turning on", "Heating/AC", "urgent", 3,
             "Assigned", "Heating & AC", 3, None, None),
            ("Wylie Hall", "1408", "Bathroom sink leaking under the cabinet", "Plumbing", "normal", 16,
             "Submitted", None, None, None, None),
            ("Buchanan Hall", "502", "Desk lamp outlet not working", "Electrical", "normal", 5,
             "In Progress", "Electrical", 20, None, None),
            ("Villa Ventura", "212", "Front door lock sticking", "Door/Lock", "urgent", 1,
             "Assigned", "Locksmith", 2, None, None),
            ("Thompson Hall", "1118", "Shower drain clogged", "Plumbing", "normal", 9,
             "Submitted", None, None, None, None),
            ("Buchanan Hall", "405", "Mini fridge making loud noise", "Appliance", "normal", 2,
             "Submitted", None, None, None, None),
            ("Wylie Hall", "1520", "Toilet keeps running", "Plumbing", "normal", 12,
             "Resolved", "Plumbing", 6, 3, "fixed"),
            ("Thompson Hall", "1250", "Wi-Fi keeps dropping", "Internet", "normal", 8,
             "Resolved", "IT / Wi-Fi", 1, 1, "fixed"),
            ("Villa Ventura", "240", "No hot water in the shower", "Plumbing", "urgent", 6,
             "Resolved", "Plumbing", 2, 1.5, "fixed"),
            ("Buchanan Hall", "560", "Ceiling light fixture out", "Electrical", "normal", 20,
             "Resolved", "Electrical", 30, 16, "fixed"),            # fixed, but after the 2-week deadline
            ("Wylie Hall", "1433", "Ants near the window", "Pest", "normal", 10,
             "Resolved", "Custodial", 10, 4, None),                 # the student hasn't answered yet
            ("Thompson Hall", "1190", "Bathroom fan not working", "Electrical", "normal", 11,
             "Assigned", "Electrical", 8, None, "not fixed"),       # reopened: the student said it wasn't fixed
        ]
        for (building, room, issue, category, urgency, days_ago,
             status, team, hours_to_assign, days_to_fix, student) in samples:
            created = now - timedelta(days=days_ago)
            ticket = self.create(building, room, issue, category, urgency, channel="sample",
                                 student_message="[SAMPLE DATA]", created_at=created)
            at = lambda delta: (created + delta).isoformat(timespec="seconds")
            self._update(ticket["code"], {
                "status": status, "assigned_to": team, "student_feedback": student,
                "assigned_at": at(timedelta(hours=hours_to_assign)) if hours_to_assign else None,
                "resolved_at": at(timedelta(days=days_to_fix)) if days_to_fix else None,
                "updated_at": at(timedelta(days=days_to_fix or 0, hours=hours_to_assign or 0))})

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


# Words that don't tell two problems apart ("not working", "my room"...). The rest are compared by their first
# 4 letters, so "heater", "heating" and "heat" match, and so do "leak" and "leaking".
GENERIC_WORDS = set("""the and for not isn doesn won don can are was with from into under near very really just still
again keeps keep help please need have has there here some been being get got getting make making stopped turn
turning working work works broken problem issue room hall wylie thompson buchanan villa ventura our its this that
any all""".split())


def problem_words(text: str | None) -> set[str]:
    text = re.sub(r"\bwi-?fi\b|\binternet\b", "wifi", (text or "").lower())          # one word for Wi-Fi problems
    words = re.findall(r"[a-z]+", re.sub(r"\ba/?c\b|\bair condition\w*", "ac", text))  # and one for the AC
    return {w[:4] for w in words if (len(w) > 2 or w == "ac") and w not in GENERIC_WORDS}


# The things that break. A new report that names one the open ticket doesn't ("toilet clogged and sink
# leaking" vs an open "leaking sink") is a different problem, even though both say "sink".
FIXTURE_WORDS = {w[:4] for w in """sink toilet shower tub drain faucet tap heater heat light lamp outlet switch door
lock key window blinds fridge refrigerator freezer microwave oven stove washer dryer wifi fan ceiling wall floor
carpet bed mattress desk chair dresser closet mirror elevator""".split()} | {"ac"}


def same_problem(old: dict, new: dict) -> bool:
    """Is a new report the same problem as an open ticket? They share a key word ("sink" / "Bathroom sink
    leaking"), have the same category (or one has none), and the new one names no fixture the old one doesn't.
    "Sink leaking" vs "Toilet clogged" -> different, even in the same room."""
    old_words, new_words = problem_words(old["issue"]), problem_words(new["issue"])
    categories = {old.get("category") or "Other", new.get("category") or "Other"}
    new_things = (new_words & FIXTURE_WORDS) - (old_words & FIXTURE_WORDS)
    return bool(old_words & new_words) and (len(categories) == 1 or "Other" in categories) and not new_things


def with_deadline(ticket: dict) -> dict:
    """Add the handbook repair deadline: 2 days for emergencies/urgent, 2 weeks for regular repairs."""
    created = datetime.fromisoformat(ticket["created_at"])
    days = config.SLA_DAYS.get(ticket.get("urgency") or "normal", 14)
    due = created + timedelta(days=days)
    ticket["due_at"] = due.isoformat(timespec="seconds")
    ticket["overdue"] = ticket.get("status") != "Resolved" and datetime.now() > due
    return ticket


def _hours(start: str | None, end: str | None) -> float | None:
    if not (start and end):
        return None
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 3600


def summary(tickets: list[dict]) -> dict:
    """The numbers for leadership (staff dashboard):
    - avg_assign_hours: report -> someone has it (assigned_at). This is the "response time".
    - avg_fix_hours:    report -> resolved (resolved tickets only).
    - on_time / resolved: resolved within the handbook deadline (2 days urgent/emergency, 2 weeks regular).
    - confirmed: resolved tickets the student confirmed fixed.
    None means "no tickets to measure yet"."""
    def average(values):
        values = [v for v in values if v is not None]
        return sum(values) / len(values) if values else None

    resolved = [t for t in tickets if t["status"] == "Resolved"]
    return {
        "total": len(tickets),
        "open": len(tickets) - len(resolved),
        "resolved": len(resolved),
        "avg_assign_hours": average(_hours(t["created_at"], t["assigned_at"]) for t in tickets),
        "avg_fix_hours": average(_hours(t["created_at"], t["resolved_at"]) for t in resolved),
        "on_time": sum(t["resolved_at"] <= t["due_at"] for t in resolved if t["resolved_at"]),
        "confirmed": sum(t["student_feedback"] == "fixed" for t in resolved),
    }


def nice_hours(hours: float | None) -> str:
    """3.5 -> '3.5 hours', 0.4 -> '24 min', 50 -> '2.1 days', None -> '-'."""
    if hours is None:
        return "-"
    if hours < 1:
        return f"{round(hours * 60)} min"
    if hours < 48:
        return f"{hours:.1f} hours"
    return f"{hours / 24:.1f} days"
