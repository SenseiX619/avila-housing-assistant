"""
views/staff_dashboard.py - what Residential Life staff and GAs see.

This page refreshes itself every 5 seconds, so a ticket a student creates by
voice or chat shows up here live.
"""

from datetime import datetime

import pandas as pd
import streamlit as st

import config
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
    st.subheader("Demo tools")
    if st.button("Load sample tickets", width="stretch"):
        store.load_sample_tickets()
        st.rerun()
    confirm = st.checkbox("I want to delete ALL tickets")
    if st.button("Clear all tickets", disabled=not confirm, width="stretch"):
        store.clear()
        st.rerun()


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
        "Issue": t["issue"],
        "Building": t["building"],
        "Room": t["room"],
        "Priority": t["urgency"].title(),
        "Due by": datetime.fromisoformat(t["due_at"]).strftime("%b %d"),
        "Status": t["status"],
        "Via": t["channel"].title(),
        "Category": t["category"],
        "Language": t["language"],
        "Reported": datetime.fromisoformat(t["created_at"]).strftime("%b %d, %I:%M %p"),
    } for t in rows])
    st.dataframe(table, hide_index=True, width="stretch")

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
