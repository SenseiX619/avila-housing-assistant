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
                 "and loads 12 clearly-fake sample tickets with a history (some fixed, one reopened).")
        if st.button("Yes, reset the demo", type="primary", width="stretch"):
            store.reset_demo()
            st.session_state.reset_done = len(store.all())
            st.rerun()
        if st.button("Delete all tickets (no samples)", width="stretch"):
            store.reset_demo()
            store.clear()
            st.rerun()
    if reset_count := st.session_state.pop("reset_done", 0):
        st.success(f"Demo reset: {reset_count} sample tickets. The next new ticket is AV-{1001 + reset_count}.")


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


@st.fragment(run_every=5)
def live_board() -> None:
    tickets = store.all()
    open_tickets = [t for t in tickets if t["status"] != "Resolved"]

    emergencies = [t for t in open_tickets if t["urgency"] == "emergency"]
    for t in emergencies:
        st.error(f"🚨 **EMERGENCY {t['code']}** - {t['issue']} - {t['building']}, room {t['room']}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Open tickets", len(open_tickets))
    c2.metric("Urgent / emergency", sum(t["urgency"] in ("urgent", "emergency") for t in open_tickets))
    c3.metric("⚠️ Overdue", sum(t["overdue"] for t in open_tickets))
    c4.metric("✅ Resolved", len(tickets) - len(open_tickets))

    if not tickets:
        st.info("No tickets yet. Report a repair in the chat, or click 🔄 Reset demo in the sidebar for sample tickets.")
        return

    # How we're doing: the numbers leadership asks about. Stat tiles, not charts - each is one number.
    s = summary(tickets)
    st.caption("**How we're doing** · all tickets, timed from the moment the student reported it")
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
        "Status": t["status"] + (f" · {t['assigned_to']}" if t["assigned_to"] else ""),
        "Via": t["channel"].title(),
        "Language": t["language"],
    } for t in rows])
    # Most important columns first, sized to fit a 1280-wide projector. The flag already says
    # Urgent/Emergency, and the category is in the chart below.
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        "Ticket": st.column_config.TextColumn(width=75), "Flag": st.column_config.TextColumn(width=145),
        "Due by": st.column_config.TextColumn(width=65), "Issue": st.column_config.TextColumn(width=210),
        "Where": st.column_config.TextColumn(width=135)})

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
