"""
notify.py - email every new ticket to housing staff, so a real person hears about it.

WHY: saving a ticket on this laptop isn't enough - someone has to get it. Each new ticket is emailed
to config.TICKET_EMAIL_TO (for now the team's own inbox; after Avila approves, HOUSING@avila.edu).
The email is sent in the background, so the student never waits for it, and a failed email never
breaks the chat - the ticket is still saved and on the dashboard either way.
"""

import smtplib
import threading
from datetime import datetime
from email.message import EmailMessage

import config


def is_configured() -> bool:
    return bool(config.TICKET_EMAIL_TO and config.SMTP_USER and config.SMTP_APP_PASSWORD)


def ticket_email(ticket: dict) -> EmailMessage:
    """The email staff receive for one ticket."""
    urgency = ticket["urgency"].upper()
    flag = "🚨 EMERGENCY" if urgency == "EMERGENCY" else urgency
    where = f"{ticket['building']}, room {ticket['room']}"
    due = datetime.fromisoformat(ticket["due_at"]).strftime("%b %d")
    reported = datetime.fromisoformat(ticket["created_at"]).strftime("%b %d, %I:%M %p")
    days = config.SLA_DAYS.get(ticket["urgency"], 14)

    msg = EmailMessage()
    msg["Subject"] = f"[Avila Housing] {ticket['code']} · {flag} · {ticket['issue']} · {where}"
    msg["From"] = f"Avila Housing Assistant <{config.SMTP_USER}>"
    msg["To"] = config.TICKET_EMAIL_TO
    msg.set_content(f"""New maintenance ticket from the Avila Housing Assistant.

Ticket:    {ticket['code']}
Priority:  {flag} - Housing Handbook deadline: {days} days (due {due})
Issue:     {ticket['issue']}
Where:     {where}
Category:  {ticket['category']}
Reported:  {reported}, by {ticket['channel']}, in {ticket['language']}

The student's own words:
"{ticket['student_message']}"

See and update all tickets on the Staff Dashboard.
(Prototype by Avila AI students - test ticket.)
""")
    return msg


def _send(msg: EmailMessage, on_done) -> None:
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(config.SMTP_USER, config.SMTP_APP_PASSWORD)
            smtp.send_message(msg)
        result = "sent"
    except Exception as err:                    # wrong password, no internet... the ticket is still saved
        result = f"failed: {type(err).__name__}: {err}"[:300]
    if on_done:
        on_done(result)


def send_ticket_email(ticket: dict, on_done=None, background: bool = True) -> bool:
    """Email a new ticket. Returns False (and sends nothing) when email isn't set up in .env.
    on_done(result) is called afterwards with "sent" or "failed: ..." - used for the log."""
    if not is_configured():
        return False
    msg = ticket_email(ticket)
    if background:
        threading.Thread(target=_send, args=(msg, on_done), daemon=True).start()
    else:
        _send(msg, on_done)
    return True


if __name__ == "__main__":
    # python notify.py  ->  sends ONE test email right now, to check the .env settings.
    if not is_configured():
        raise SystemExit("Email is off: add TICKET_EMAIL_TO, SMTP_USER and SMTP_APP_PASSWORD to .env")
    now = datetime.now().isoformat(timespec="seconds")
    test = {"code": "AV-TEST", "urgency": "normal", "issue": "Test email from the Avila Housing Assistant",
            "building": "Wylie Hall", "room": "000", "category": "Other", "due_at": now, "created_at": now,
            "channel": "test", "language": "English", "student_message": "This is only a test."}
    send_ticket_email(test, on_done=lambda result: print(f"Test email to {config.TICKET_EMAIL_TO}: {result}"),
                      background=False)
