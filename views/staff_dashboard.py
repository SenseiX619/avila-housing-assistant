"""
views/staff_dashboard.py - what Residential Life staff and GAs see.

This page refreshes itself every 5 seconds, so a ticket a student creates by
voice or chat shows up here live.
"""

from datetime import datetime

import pandas as pd
import streamlit as st

import config
from phone_link import show_qr
from tickets import STATUSES, TicketStore

store = TicketStore()
PURPLE = "#4B2A7B"

st.markdown(f"""
<div style="background:linear-gradient(90deg,{PURPLE},#6B3FA0);color:#fff;padding:16px 22px;
            border-radius:14px;margin-bottom:10px">
  <h2 style="color:#fff;margin:0">🛠️ Housing Staff Dashboard</h2>
  <p style="color:#F2C14E;margin:4px 0 0 0;font-weight:600">
     Live maintenance tickets - deadlines from the Housing Handbook (urgent: 2 days, regular: 2 weeks)</p>
</div>""", unsafe_allow_html=True)

with st.sidebar:
    show_qr(width=210)                       # the audience scans this on the projector
    st.divider()
    st.subheader("Demo tools")
    # Two clicks on purpose (open, then confirm), so a stray click on stage can't wipe the board.
    with st.popover("🔄 Reset demo", type="primary", width="stretch"):
        st.write("Deletes **all** tickets, numbers them from AV-1001 again, "
                 "and loads 6 clearly-fake sample tickets.")
        if st.button("Yes, reset the demo", type="primary", width="stretch"):
            store.reset_demo()
            st.session_state.reset_done = True
            st.rerun()
        if st.button("Delete all tickets (no samples)", width="stretch"):
            store.reset_demo()
            store.clear()
            st.rerun()
    if st.session_state.pop("reset_done", False):
        st.success("Demo reset: 6 sample tickets. The next new ticket is AV-1007.")


def badge(t: dict) -> str:
    """Status shown as icon + words (never colour alone)."""
    if t["status"] == "Resolved":
        return "✅ Resolved"
    if t["overdue"]:
        return "⚠️ OVERDUE"
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
        st.info("No tickets yet. Report a repair in the chat (or click 'Load sample tickets').")
        return

    buildings = st.multiselect("Filter by building", config.BUILDINGS, key="bfilter")
    show_resolved = st.toggle("Show resolved tickets", value=False, key="resolved")
    rows = [t for t in tickets
            if (not buildings or t["building"] in buildings) and (show_resolved or t["status"] != "Resolved")]

    table = pd.DataFrame([{
        "Ticket": t["code"],
        "Flag": badge(t),
        "Due by": datetime.fromisoformat(t["due_at"]).strftime("%b %d"),
        "Issue": t["issue"],
        "Where": f"{t['building']} {t['room']}",
        "Status": t["status"],
        "Via": t["channel"].title(),
        "Language": t["language"],
    } for t in rows])
    # Most important columns first, sized to fit a 1280-wide projector. The flag already says
    # Urgent/Emergency, and the category is in the chart below.
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        "Ticket": st.column_config.TextColumn(width=75), "Flag": st.column_config.TextColumn(width=115),
        "Due by": st.column_config.TextColumn(width=65), "Issue": st.column_config.TextColumn(width=240),
        "Where": st.column_config.TextColumn(width=135)})

    if open_tickets:
        left, right = st.columns(2)
        by_building = pd.Series([t["building"] for t in open_tickets]).value_counts()
        by_category = pd.Series([t["category"] for t in open_tickets]).value_counts()
        with left:
            st.caption("Open tickets by building")
            st.bar_chart(by_building, color=PURPLE, horizontal=True, x_label="Open tickets", y_label="")
        with right:
            st.caption("Open tickets by category")
            st.bar_chart(by_category, color=PURPLE, horizontal=True, x_label="Open tickets", y_label="")
    st.caption(f"Auto-refreshes every 5 seconds · last update {datetime.now().strftime('%I:%M:%S %p')}")


live_board()

st.divider()
st.subheader("Update a ticket")
all_tickets = store.all()
if all_tickets:
    col1, col2, col3 = st.columns([2, 1, 1])
    choice = col1.selectbox("Ticket", [f"{t['code']} - {t['issue']} ({t['building']} {t['room']})"
                                       for t in all_tickets])
    new_status = col2.selectbox("New status", STATUSES)
    col3.write("")
    if col3.button("Save", type="primary", width="stretch", key="save_status"):
        store.update_status(choice.split(" - ")[0], new_status)
        st.success(f"{choice.split(' - ')[0]} is now {new_status}.")
        st.rerun()
