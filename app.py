"""
app.py - the student-facing chat screen.

Run it with:   streamlit run app.py
"""

import streamlit as st

import config
from agent import GREETING, HousingAgent
from knowledge import Retriever

st.set_page_config(page_title="Avila Housing Assistant", page_icon="🦅", layout="centered")

# Avila colours: purple and gold
st.markdown("""
<style>
  .avila-banner {background: linear-gradient(90deg, #4B2A7B, #6B3FA0); color: #fff;
                 padding: 18px 22px; border-radius: 14px; margin-bottom: 12px;}
  .avila-banner h1 {color: #fff; font-size: 1.6rem; margin: 0;}
  .avila-banner p {color: #F2C14E; margin: 4px 0 0 0; font-weight: 600;}
  .sources {font-size: 0.8rem; color: #888;}
</style>
<div class="avila-banner">
  <h1>🦅 Avila Housing Assistant</h1>
  <p>Ask about campus living, report a repair, or check a ticket - 24/7.</p>
</div>
""", unsafe_allow_html=True)


@st.cache_resource
def load_agent() -> HousingAgent:
    # cache_resource = load the handbook index once, not on every click
    return HousingAgent(Retriever(config.KNOWLEDGE_FILE))


agent = load_agent()
if not agent.retriever.ready:
    st.warning("The handbook isn't loaded yet. In the terminal run:  python ingest.py")

# Each browser tab gets its own memory (session_state survives between messages)
if "chat" not in st.session_state:
    st.session_state.chat = [{"role": "assistant", "content": GREETING}]
    st.session_state.agent_state = {}

with st.sidebar:
    st.subheader("Try asking")
    examples = ["Can my friend stay over this weekend?",
                "I got locked out of my room. Is there a fee?",
                "What are quiet hours on Friday?"]
    for ex in examples:
        if st.button(ex, use_container_width=True):
            st.session_state.pending = ex
    st.divider()
    st.error(f"**Emergency?** Call 911 or Campus Safety at **{config.CAMPUS_SAFETY_PHONE}**")
    if st.button("Start a new conversation"):
        st.session_state.clear()
        st.rerun()

# Show the conversation so far
for msg in st.session_state.chat:
    with st.chat_message(msg["role"], avatar="🦅" if msg["role"] == "assistant" else None):
        st.markdown(msg["content"])
        if msg.get("sources"):
            st.markdown(f"<div class='sources'>Sources: {config.HANDBOOK_NAME}, page "
                        f"{', '.join(map(str, msg['sources']))}</div>", unsafe_allow_html=True)

typed = st.chat_input("Type your question...")
user_text = typed or st.session_state.pop("pending", None)

if user_text:
    st.session_state.chat.append({"role": "user", "content": user_text})
    with st.spinner("Thinking..."):
        reply = agent.respond(user_text, st.session_state.agent_state)
    st.session_state.chat.append({"role": "assistant", "content": reply.text, "sources": reply.sources})
    st.rerun()
