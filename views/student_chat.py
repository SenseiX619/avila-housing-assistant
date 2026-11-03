"""
views/student_chat.py - the student-facing chat screen (voice + text).
"""

import streamlit as st

import config
import voice
from agent import GREETING, MAIN_MENU, HousingAgent, write_log
from knowledge import Retriever
from phone_link import show_qr
from tickets import TicketStore

# Avila colours: purple and gold. (Styles and the banner go in separate calls -
# Streamlit hides a markdown block that starts with a <style> tag.)
st.markdown("""<style>
  .avila-banner {background: linear-gradient(90deg, #4B2A7B, #6B3FA0); color: #fff;
                 padding: 18px 22px; border-radius: 14px; margin-bottom: 12px;}
  .avila-banner h1 {color: #fff; font-size: 1.6rem; margin: 0; padding: 0;}
  .avila-banner p {color: #F2C14E; margin: 4px 0 0 0; font-weight: 600;}
  .sources {font-size: 0.8rem; color: #888;}
  [data-testid="stMainBlockContainer"] {max-width: 880px;}   /* keep the chat narrow and readable */
</style>""", unsafe_allow_html=True)
st.markdown("""<div class="avila-banner">
  <h1>🦅 Avila Housing Assistant</h1>
  <p>Ask about campus living, report a repair, or check a ticket - 24/7.</p>
</div>
""", unsafe_allow_html=True)


@st.cache_resource
def load_agent(code_version: float) -> HousingAgent:
    # cache_resource = load the handbook index once, not on every click. code_version is when the newest
    # .py file was saved, so after a code change the next message uses the new code - no restart needed.
    return HousingAgent(Retriever(config.KNOWLEDGE_FILE), TicketStore())


agent = load_agent(max(path.stat().st_mtime for path in config.ROOT.glob("*.py")))
if not agent.retriever.ready:
    st.warning("The handbook isn't loaded yet. In the terminal run:  python ingest.py")

# Each browser tab gets its own memory (session_state survives between messages)
if "chat" not in st.session_state:
    st.session_state.chat = [{"role": "assistant", "content": GREETING, "options": MAIN_MENU}]
    st.session_state.agent_state = {}

with st.sidebar:
    st.subheader("Try asking")
    examples = ["Can my friend stay over this weekend?",
                "I got locked out of my room. Is there a fee?",
                "The heater in my room isn't working",
                "What's the status of my ticket?"]
    for ex in examples:
        if st.button(ex, width="stretch"):
            st.session_state.pending = ex
    st.divider()
    st.error(f"**Emergency?** Call 911 or Campus Safety at **{config.CAMPUS_SAFETY_PHONE}**")
    show_qr(width=150)
    if st.button("Start a new conversation"):
        st.session_state.clear()
        st.rerun()

def ticket_card(t: dict) -> None:
    colour = {"emergency": "#C0392B", "urgent": "#E67E22"}.get(t["urgency"], "#4B2A7B")
    st.markdown(f"""<div style="border-left:6px solid {colour};background:rgba(75,42,123,.07);
        padding:10px 14px;border-radius:8px;margin-top:6px">
        <b>🎫 {t['code']}</b> &nbsp;·&nbsp; {t['status']} &nbsp;·&nbsp;
        <span style="color:{colour};font-weight:700">{t['urgency'].upper()}</span><br>
        {t['issue']}<br><small>{t['building']}, room {t['room']} · {t['category']}</small></div>""",
                unsafe_allow_html=True)


# Show the conversation so far
last = len(st.session_state.chat) - 1
for i, msg in enumerate(st.session_state.chat):
    with st.chat_message(msg["role"], avatar="🦅" if msg["role"] == "assistant" else None):
        st.markdown(msg["content"])
        if msg.get("ticket"):
            ticket_card(msg["ticket"])
        if msg.get("sources"):
            st.markdown(f"<div class='sources'>Sources: {config.HANDBOOK_NAME}, page "
                        f"{', '.join(map(str, msg['sources']))}</div>", unsafe_allow_html=True)
        if msg.get("calls"):                     # on a phone, these dial the number
            with st.container(horizontal=True):
                for label, link in msg["calls"]:
                    st.link_button(label, link, type="primary")
        if i == last and msg.get("options"):
            # Quick replies under the latest answer: tapping one is the same as typing it
            choice = st.pills("Quick replies", msg["options"], key=f"options_{i}", label_visibility="collapsed")
            if choice:
                st.session_state.pending = choice

# A mic problem from the last attempt (shown once, above the recorder)
if mic_problem := st.session_state.pop("mic_problem", None):
    st.warning(f"🎤 {mic_problem}")

# ---------- Two doors into the same agent: voice and text ----------
# The student talks; the assistant always answers in writing.
st.session_state.setdefault("mic_round", 0)
recording = st.audio_input(
    "🎤 Tap the mic, talk, then tap stop", key=f"mic_{st.session_state.mic_round}",
    help="First time: click Allow when your browser asks to use the microphone. If it says permission "
         "denied, click the icon left of the web address, set Microphone to Allow, then reload the page.")
typed = st.chat_input("...or type your question here")

user_text, channel = None, "text"
if recording is not None:
    st.session_state.mic_round += 1          # new key = fresh, empty recorder next time
    audio, problem = recording.getvalue(), None
    with st.spinner("Listening..."):
        try:
            user_text = voice.transcribe(audio)
            channel = "voice"
        except voice.NoSpeechHeard as err:
            problem = str(err)
        except Exception as err:
            problem = f"Couldn't process the recording ({err}). Try again or type instead."
    # Log every recording: a failed one never reaches the agent, so otherwise it leaves no trace.
    write_log({"channel": "voice", "heard": user_text, "mic_problem": problem,
               "audio_kb": round(len(audio) / 1024), "loudness": round(voice.loudness(audio), 4)})
    if problem:
        st.session_state.mic_problem = problem
        st.rerun()
elif typed or st.session_state.get("pending"):
    user_text = typed or st.session_state.pop("pending")

if user_text:
    st.session_state.chat.append({"role": "user", "content": ("🎤 " if channel == "voice" else "") + user_text})
    with st.spinner("Thinking..."):
        reply = agent.respond(user_text, st.session_state.agent_state, channel=channel)
    st.session_state.chat.append({"role": "assistant", "content": reply.text, "sources": reply.sources,
                                  "ticket": reply.ticket, "options": reply.options, "calls": reply.calls})
    st.rerun()
