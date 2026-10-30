"""
app.py - the front door. Run the whole app with:

    streamlit run app.py

It sets up two pages and the sidebar menu between them:
  - Student Assistant  (views/student_chat.py)    - voice + text chat for students
  - Staff Dashboard    (views/staff_dashboard.py) - live tickets for housing staff
"""

import streamlit as st

st.set_page_config(page_title="Avila Housing Assistant", page_icon="🦅", layout="wide")

page = st.navigation([
    st.Page("views/student_chat.py", title="Student Assistant", icon="🦅", default=True),
    st.Page("views/staff_dashboard.py", title="Staff Dashboard", icon="🛠️"),
])
page.run()
