"""
views/staff_dashboard.py - what Residential Life staff and GAs see.

This page refreshes itself every 5 seconds, so a ticket a student creates by
voice or chat shows up here live.
"""

import hmac
from datetime import datetime

import pandas as pd
import streamlit as st

import config
import notify
from phone_link import show_qr
from tickets import STATUSES, TicketStore, nice_hours, summary

store = TicketStore()
PURPLE = "#4B2A7B"
# Chart colours: Avila purple (open) and a darker Avila gold (resolved). Checked with the dataviz skill's
# validate_palette.py: colour-blind safe, distinct, and at least 3:1 against the white page.
OPEN_COLOR, RESOLVED_COLOR = "#6B3FA0", "#B8860B"

st.markdown(f"""
<div style="background:linear-gradient(90deg,{PURPLE},#6B3FA0);color:#fff;padding:16px 22px;
            border-radius:14px;margin-bottom:10px">
  <h2 style="color:#fff;margin:0">🛠️ Housing Staff Dashboard</h2>
  <p style="color:#F2C14E;margin:4px 0 0 0;font-weight:600">
     Live maintenance tickets - deadlines from the Housing Handbook (urgent: 2 days, regular: 2 weeks)</p>
</div>""", unsafe_allow_html=True)


def staff_signed_in() -> bool:
    """Staff only: this page can change or wipe every ticket, and the QR code puts the app on the
    audience's phones. Asks for STAFF_PASSWORD once per browser tab. No password set -> open, with a warning."""
    if not config.STAFF_PASSWORD:
        st.sidebar.caption("🔓 No staff password set: anyone with the link can open this page. "
                           "Add STAFF_PASSWORD to .env.")
        return True
    if st.session_state.get("staff_ok"):
        return True
    with st.form("staff_login"):
        typed = st.text_input("Staff password", type="password")
        if st.form_submit_button("Open the dashboard", type="primary"):
            # compare_digest: checks the whole password, so timing can't give away how much was right
            if hmac.compare_digest(typed.encode(), config.STAFF_PASSWORD.encode()):
                st.session_state.staff_ok = True
                st.rerun()
            st.error("Wrong password.")
    return False


if not staff_signed_in():
    st.stop()                                # nothing below (tickets, Reset demo, status changes) is shown

with st.sidebar:
    show_qr(width=210)                       # the audience scans this on the projector
    st.divider()
    st.subheader("Demo tools")
    # Two clicks on purpose (open, then confirm), so a stray click on stage can't wipe the board.
    with st.popover("🔄 Reset demo", type="primary", width="stretch"):
        st.write("Deletes **all** tickets, numbers them from AV-1001 again, "
                 "and loads 12 clearly-fake sample tickets with a history (some fixed, one reopened). "
                 "A backup copy of everything is saved first, in data/backups.")
        if st.button("Yes, reset the demo", type="primary", width="stretch"):
            saved = store.reset_demo()
            st.session_state.reset_done = (len(store.all()), saved.name)
            st.rerun()
        if st.button("Delete all tickets (no samples)", width="stretch"):
            store.reset_demo()
            store.clear()
            st.rerun()
    if reset := st.session_state.pop("reset_done", None):
        count, backup_name = reset
        st.success(f"Demo reset: {count} sample tickets. The next new ticket is AV-{1001 + count}. "
                   f"Backup saved: data/backups/{backup_name}")
    if not notify.is_configured():
        st.caption("📧 Emails to staff are off (no email settings in .env). Tickets still save and show here.")


def badge(t: dict) -> str:
    """Status shown as icon + words (never colour alone)."""
    if t["status"] == "Resolved":
        return "✅ Confirmed fixed" if t["student_feedback"] == "fixed" else "✅ Resolved"
    if t["overdue"]:
        return "⚠️ OVERDUE"
    if t["student_feedback"] == "not fixed":
        return "🔁 Reopened"                    # staff said fixed, the student said it isn't
    if t["urgency"] == "emergency":
        return "🚨 Emergency"
    return "🟠 Urgent" if t["urgency"] == "urgent" else "🟣 On track"


def plain_reason(status: str) -> str:
    """'failed: gaierror: [Errno 11001] getaddrinfo failed' -> 'no internet connection'."""
    text = status.lower()
    if "getaddrinfo" in text or "network is unreachable" in text or "no internet" in text:
        return "no internet connection"
    if "authentication" in text or "username and password" in text:
        return "Gmail rejected the email login - check SMTP_APP_PASSWORD in .env"
    if "timed out" in text or "timeout" in text:
        return "the email server didn't answer in time"
    return status.removeprefix("failed: ")[:120]


def email_problems() -> None:
    """A ticket whose email to staff failed is still saved and on this board - but nobody got the email.
    Say so, and offer to send them again."""
    failed_tickets, failed_questions = store.failed_emails()
    codes = [t["code"] for t in failed_tickets] + [h["code"] for h in failed_questions]
    if not codes:
        return
    reason = plain_reason((failed_tickets or failed_questions)[0]["email_status"])
    left, right = st.columns([4, 1])
    left.warning(f"📧 **The email to staff failed for {', '.join(codes)}.** They're saved here, but nobody was "
                 f"emailed. ({reason}) Check the internet and the email settings in .env, then retry.")
    if right.button("📧 Retry emails", key="retry_emails", width="stretch"):
        for ticket in failed_tickets:
            store.record_email(ticket["code"], "retrying")          # off the warning until the result comes back
            notify.send_ticket_email(ticket, update="Resent after a failed email",
                                     on_done=lambda result, code=ticket["code"]: store.record_email(code, result))
        for question in failed_questions:
            store.record_email(question["code"], "retrying")
            notify.send_handoff_email(question,
                                      on_done=lambda result, code=question["code"]: store.record_email(code, result))
        st.toast(f"Sending {len(codes)} email{'s' if len(codes) > 1 else ''} again...")


@st.fragment(run_every=5)
def live_board() -> None:
    tickets = store.all()
    open_tickets = [t for t in tickets if t["status"] != "Resolved"]

    emergencies = [t for t in open_tickets if t["urgency"] == "emergency"]
    for t in emergencies:
        st.error(f"🚨 **EMERGENCY {t['code']}** - {t['issue']} - {t['building']}, room {t['room']}")
    waiting = store.handoffs(waiting_only=True)
    if waiting:
        st.warning(f"🙋 **{len(waiting)} student{'s' if len(waiting) > 1 else ''} waiting for a person** - "
                   f"answer below, under Questions for a person.")
    email_problems()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Open tickets", len(open_tickets))
    c2.metric("Urgent / emergency", sum(t["urgency"] in ("urgent", "emergency") for t in open_tickets))
    c3.metric("⚠️ Overdue", sum(t["overdue"] for t in open_tickets))
    c4.metric("✅ Resolved", len(tickets) - len(open_tickets))

    # Honesty on the projector: sample tickets (Reset demo) are made up, and so are the numbers they make.
    samples = sum(t["channel"] == "sample" for t in tickets)
    if samples:
        st.info(f"🧪 **Sample data:** {samples} of these tickets are made up for this demo (\"Via: Sample\"), "
                f"and so are the numbers they produce. A pilot would give real ones.")

    if not tickets:
        st.info("No tickets yet. Report a repair in the chat, or click 🔄 Reset demo in the sidebar for sample tickets.")
        return

    # How we're doing: the numbers leadership asks about. Stat tiles, not charts - each is one number.
    s = summary(tickets)
    st.caption("**How we're doing** · all tickets, timed from the moment the student reported it"
               + (" · includes sample tickets" if samples else ""))
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("⏱️ Avg response time", nice_hours(s["avg_assign_hours"]),
              help="From the student's report until a team was assigned")
    d2.metric("🔧 Avg time to fix", nice_hours(s["avg_fix_hours"]),
              help="From the student's report until staff marked it resolved")
    d3.metric("📅 Fixed on time", f"{s['on_time']} of {s['resolved']}" if s["resolved"] else "-",
              help="Resolved within the Housing Handbook deadline: 2 days urgent/emergency, 2 weeks regular")
    d4.metric("👍 Confirmed by students", f"{s['confirmed']} of {s['resolved']}" if s["resolved"] else "-",
              help="Resolved tickets where the student answered 'Yes, it's fixed' in the chat")

    buildings = st.multiselect("Filter by building", config.BUILDINGS, key="bfilter")
    show_resolved = st.toggle("Show resolved tickets", value=False, key="resolved")
    rows = [t for t in tickets
            if (not buildings or t["building"] in buildings) and (show_resolved or t["status"] != "Resolved")]

    table = pd.DataFrame([{
        "Ticket": t["code"],
        "Flag": badge(t),
        "Due by": datetime.fromisoformat(t["due_at"]).strftime("%b %d"),
        "Issue": t["issue"] + (f" · {t['reports']} reports" if (t["reports"] or 1) > 1 else ""),
        "Where": f"{t['building']} {t['room']}",
        "Student": t["student_name"] or ("Sample" if t["channel"] == "sample" else "Guest"),
        "Status": t["status"] + (f" · {t['assigned_to']}" if t["assigned_to"] else ""),
        "Via": t["channel"].title(),
        "Language": t["language"],
    } for t in rows])
    # Most important columns first, sized to fit a 1280-wide projector. The flag already says
    # Urgent/Emergency, and the category is in the chart below.
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        "Ticket": st.column_config.TextColumn(width=75), "Flag": st.column_config.TextColumn(width=145),
        "Due by": st.column_config.TextColumn(width=65), "Issue": st.column_config.TextColumn(width=210),
        "Where": st.column_config.TextColumn(width=135), "Student": st.column_config.TextColumn(width=110)})

    left, right = st.columns(2)
    with left:
        # Every request, open and resolved, per hall: which building keeps having problems.
        st.caption("Requests by building · open and resolved")
        places = config.BUILDINGS + sorted({t["building"] for t in tickets} - set(config.BUILDINGS))
        by_building = pd.DataFrame({
            "Open": [sum(t["building"] == b and t["status"] != "Resolved" for t in tickets) for b in places],
            "Resolved": [sum(t["building"] == b and t["status"] == "Resolved" for t in tickets) for b in places],
        }, index=places)
        st.bar_chart(by_building, color=[OPEN_COLOR, RESOLVED_COLOR], horizontal=True, stack=True,
                     x_label="", y_label="Requests")  # horizontal: Streamlit swaps the two labels
    if open_tickets:
        with right:
            st.caption("Open tickets by category")
            by_category = pd.Series([t["category"] for t in open_tickets]).value_counts()
            st.bar_chart(by_category, color=OPEN_COLOR, horizontal=True, x_label="", y_label="Open tickets")
    st.caption(f"Auto-refreshes every 5 seconds · last update {datetime.now().strftime('%I:%M:%S %p')}")


live_board()

st.divider()
st.subheader("🙋 Questions for a person")
# Students who tapped "Talk to a person". The answer typed here appears in that student's chat by itself.
waiting = store.handoffs(waiting_only=True)
if not waiting:
    st.caption("No students are waiting for a person right now.")
else:
    by_question = {f"{h['code']} - {h['question'][:70]}": h for h in waiting}
    handoff = by_question[st.selectbox("Question", list(by_question))]
    asked = datetime.fromisoformat(handoff["created_at"]).strftime("%b %d, %I:%M %p")
    st.info(f"**{handoff['code']}** · asked {asked} · {handoff['language']}\n\n{handoff['question']}")
    answer = st.text_area("Your answer (it appears in the student's chat)", key=f"answer_{handoff['code']}")
    if st.button("Send answer", type="primary", key="send_answer"):
        if answer.strip():
            store.answer_handoff(handoff["code"], answer)
            st.session_state.answer_sent = (f"Answer sent for {handoff['code']}. If the student's chat is open, "
                                            f"they see it there.")
            st.rerun()
        st.warning("Type an answer first.")
if sent := st.session_state.pop("answer_sent", None):
    st.success(sent)

st.divider()
st.subheader("Update a ticket")
all_tickets = store.all()
NOBODY = "(nobody yet)"
if all_tickets:
    col1, col2, col3, col4 = st.columns([2, 1, 1, 0.6])
    by_label = {f"{t['code']} - {t['issue']} ({t['building']} {t['room']})": t for t in all_tickets}
    ticket = by_label[col1.selectbox("Ticket", list(by_label))]
    # The status and assignee boxes start at the chosen ticket's current values.
    new_status = col2.selectbox("New status", STATUSES, index=STATUSES.index(ticket["status"])
                                if ticket["status"] in STATUSES else 0)
    people = [NOBODY, *config.ASSIGNEES]
    assignee = col3.selectbox("Assigned to", people, index=people.index(ticket["assigned_to"])
                              if ticket["assigned_to"] in people else 0)
    col4.write("")
    if col4.button("Save", type="primary", width="stretch", key="save_status"):
        store.update_status(ticket["code"], new_status, assigned_to="" if assignee == NOBODY else assignee)
        st.session_state.saved = f"{ticket['code']} is now {new_status}" + (
            f" ({assignee})." if assignee != NOBODY else ".") + " If the student's chat is open, they see it there."
        st.rerun()
    if saved := st.session_state.pop("saved", None):
        st.success(saved)
