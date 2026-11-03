"""
tests/test_flows.py - offline checks for the 5 demo flows. No internet, no Groq key.

Run from the project folder:
    python -m unittest discover -s tests -v        (or double-click test.bat)

How it stays offline and safe:
- FAKE_LLM=1 swaps the real AI for fake_llm.py (a simple rule-based stand-in).
- Every test uses a temporary database and log, so your real tickets
  (data/tickets.db) and data/conversation_log.jsonl are never touched.
- Some tests replace the AI's analysis with a fixed one ("the AI said X") to check
  that Python makes the right decision anyway - that's how we test the safety nets.
"""

import io
import logging
import math
import os
import shutil
import struct
import sys
import tempfile
import unittest
import warnings
import wave
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["FAKE_LLM"] = "1"                     # set BEFORE config is imported

import config                                    # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="avila-tests-"))
config.DB_FILE = TMP / "tickets.db"              # set BEFORE tickets.py is imported

import agent                                     # noqa: E402
import voice                                     # noqa: E402
from knowledge import Retriever                  # noqa: E402
from streamlit.testing.v1 import AppTest         # noqa: E402
from tickets import TicketStore                  # noqa: E402

agent.LOG_FILE = TMP / "conversation_log.jsonl"


def setUpModule():
    # Hide two harmless notes from Streamlit so real failures stand out: a pandas deprecation
    # notice from its chart code, and "missing ScriptRunContext" from running pages in a test.
    # (Done here, not at the top, because the test runner resets warning filters when it starts.)
    warnings.filterwarnings("ignore", message="The inplace keyword")
    logging.getLogger("streamlit.runtime.scriptrunner_utils.script_run_context").addFilter(
        lambda record: "missing ScriptRunContext" not in record.getMessage())


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)       # delete the temporary database and log


def ai_said(intent, language="English", ticket_id=None, **ticket):
    """A fixed ANALYZE result, so we can check what Python does with it."""
    return {"language": language, "intent": intent, "search_query": "", "ticket": ticket,
            "ticket_id": ticket_id, "cancel": False}


def wav(volume: float) -> bytes:
    """One second of a 16-bit test tone (volume 0 = silence), like the mic recorder sends."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", int(volume * 32767 * math.sin(i / 8)))
                               for i in range(16000)))
    return buf.getvalue()


class AgentFlows(unittest.TestCase):
    """The brain on its own: message in, reply (and maybe a ticket) out."""

    def setUp(self):
        self.store = TicketStore()
        self.store.clear()
        self.agent = agent.HousingAgent(Retriever(config.KNOWLEDGE_FILE), self.store)
        self.state = {}                          # one student's conversation memory

    def say(self, message, channel="text"):
        return self.agent.respond(message, self.state, channel=channel)

    def test_a_handbook_question_cites_a_page(self):
        """(a) 'Can my friend stay over this weekend?' -> handbook answer with a page citation"""
        self.assertTrue(self.agent.retriever.ready, "Handbook not loaded - run: python ingest.py")
        reply = self.say("Can my friend stay over this weekend?")
        self.assertEqual(reply.kind, "answer")
        self.assertIn("(Handbook p.", reply.text)
        self.assertTrue(reply.sources, "the answer should list its handbook pages")

    def test_b_voice_repair_asks_for_details_then_creates_ticket(self):
        """(b) mic: 'My heater isn't working' -> asks hall + room -> ticket saved as Voice"""
        first = self.say("My heater isn't working", channel="voice")
        self.assertEqual(first.kind, "ask_details")
        self.assertIn("hall", first.text.lower())
        self.assertIn("room", first.text.lower())

        second = self.say("Thompson 214", channel="voice")
        self.assertEqual(second.kind, "ticket")
        self.assertEqual((second.ticket["building"], second.ticket["room"]), ("Thompson Hall", "214"))
        self.assertEqual(second.ticket["channel"], "voice")
        self.assertEqual([t["code"] for t in self.store.all()], [second.ticket["code"]])

    def test_b_repair_net_catches_ai_calling_it_smalltalk(self):
        """(b) safety net: the AI says 'smalltalk' for a repair -> still becomes a repair"""
        with mock.patch("llm.chat_json", return_value=ai_said("smalltalk")):
            reply = self.say("hey, my heater isn't working", channel="voice")
        self.assertEqual(reply.kind, "ask_details")
        self.assertNotIn(agent.GREETING, reply.text)

    def test_c_spanish_reply_english_ticket(self):
        """(c) Spanish repair -> reply translated to Spanish, ticket saved in English"""
        analysis = ai_said("maintenance", language="Spanish", building="Wylie Hall", room="108",
                           issue="Bathroom leak", category="Plumbing", urgency="urgent")
        with mock.patch("llm.chat_json", return_value=analysis):
            reply = self.say("Mi baño tiene una fuga en Wylie 108")
        self.assertEqual(reply.kind, "ticket")
        self.assertTrue(reply.text.startswith("[FAKE TRANSLATION]"), "reply should be translated")
        self.assertEqual(reply.ticket["issue"], "Bathroom leak")
        self.assertEqual(reply.ticket["language"], "Spanish")
        self.assertEqual(reply.ticket["urgency"], "urgent")

    def test_d_smoke_calls_campus_safety_first(self):
        """(d) smoke from an outlet -> Campus Safety first, 911 as the backup, then the emergency ticket"""
        reply = self.say("There's smoke coming from an outlet in Buchanan 302")
        self.assertEqual(reply.kind, "emergency")
        self.assertEqual(reply.ticket["urgency"], "emergency")
        self.assertEqual((reply.ticket["building"], reply.ticket["room"]), ("Buchanan Hall", "302"))
        self.assertLess(reply.text.index(config.CAMPUS_SAFETY_PHONE), reply.text.index("911"))
        self.assertLess(reply.text.index("911"), reply.text.index(reply.ticket["code"]))

    def test_d_real_fire_calls_911_first(self):
        """(d) a real fire -> 911 first, then Campus Safety, then the emergency ticket"""
        reply = self.say("There's a fire in Buchanan 302!")
        self.assertEqual(reply.kind, "emergency")
        self.assertLess(reply.text.index("911"), reply.text.index(config.CAMPUS_SAFETY_PHONE))
        self.assertLess(reply.text.index(config.CAMPUS_SAFETY_PHONE), reply.text.index(reply.ticket["code"]))

    def test_d_fire_alarm_says_leave_the_building_first(self):
        """(d) fire alarm going off -> leave the building first, then Campus Safety, 911 as the backup"""
        reply = self.say("The fire alarm is going off in Wylie")
        self.assertEqual(reply.kind, "emergency")
        self.assertLess(reply.text.index("leave the building"), reply.text.index(config.CAMPUS_SAFETY_PHONE))
        self.assertLess(reply.text.index(config.CAMPUS_SAFETY_PHONE), reply.text.index("911"))

    def test_d_spanish_fire_calls_911_first(self):
        """(d) a fire reported in Spanish -> 911 first (read from the AI's English summary)"""
        analysis = ai_said("emergency", language="Spanish", building="Wylie Hall", room="108",
                           issue="Fire in the room", urgency="emergency")
        with mock.patch("llm.chat_json", return_value=analysis):
            reply = self.say("¡Hay un incendio en mi cuarto! Wylie 108")
        self.assertLess(reply.text.index("911"), reply.text.index(config.CAMPUS_SAFETY_PHONE))

    def test_d_rules_question_about_smoking_is_not_an_emergency(self):
        """(d) 'Can I smoke in my room?' -> safety reminder first, then a normal handbook answer"""
        with mock.patch("llm.chat_json", return_value=ai_said("question")):
            reply = self.say("Can I smoke in my room?")
        self.assertEqual(reply.kind, "answer")
        self.assertTrue(reply.text.startswith(agent.SAFETY_REMINDER))
        self.assertFalse(self.state.get("draft"), "a rules question must not start an emergency ticket")

    def test_d_beeping_smoke_detector_is_an_urgent_repair(self):
        """(d) beeping smoke detector -> safety reminder first, then an URGENT repair (not an emergency)"""
        analysis = ai_said("maintenance", building="Wylie Hall", room="210",
                           issue="Smoke detector beeping", urgency="normal")
        with mock.patch("llm.chat_json", return_value=analysis):
            reply = self.say("My smoke detector keeps beeping, I'm in Wylie 210")
        self.assertEqual(reply.kind, "ticket")
        self.assertEqual(reply.ticket["urgency"], "urgent")
        self.assertTrue(reply.text.startswith(agent.SAFETY_REMINDER))

    def test_d_ai_alone_cannot_call_off_danger_words(self):
        """(d) safety net: the AI says 'question' for real smoke -> still an emergency"""
        with mock.patch("llm.chat_json", return_value=ai_said("question")):
            reply = self.say("There's smoke coming out of my outlet")
        self.assertEqual(reply.kind, "emergency")

    def test_d_danger_net_reads_the_ai_english_summary(self):
        """(d) safety net: smoke reported in Spanish, AI files it as a repair -> still an emergency"""
        analysis = ai_said("maintenance", language="Spanish", building="Buchanan Hall", room="302",
                           issue="Smoke coming from an outlet", urgency="normal")
        with mock.patch("llm.chat_json", return_value=analysis):
            reply = self.say("Sale humo de un enchufe en Buchanan 302")
        self.assertEqual(reply.kind, "emergency")
        self.assertEqual(reply.ticket["urgency"], "emergency")

    def test_d_emergency_net_catches_ai_calling_it_a_repair(self):
        """(d) safety net: the AI says 'maintenance' for smoke -> still an emergency"""
        analysis = ai_said("maintenance", building="Buchanan Hall", room="302",
                           issue="Outlet smoking", urgency="normal")
        with mock.patch("llm.chat_json", return_value=analysis):
            reply = self.say("There's smoke coming from an outlet in Buchanan 302")
        self.assertEqual(reply.kind, "emergency")
        self.assertEqual(reply.ticket["urgency"], "emergency")

    def test_d_emergency_is_never_downgraded(self):
        """(d) an emergency stays an emergency when the room comes in a later message"""
        self.say("There's smoke in the hallway!")
        analysis = ai_said("maintenance", building="Buchanan Hall", room="302", urgency="normal")
        with mock.patch("llm.chat_json", return_value=analysis):
            reply = self.say("Buchanan 302")
        self.assertEqual(reply.ticket["urgency"], "emergency")

    def test_e_status_of_my_ticket(self):
        """(e) 'What's the status of my ticket?' -> this conversation's latest ticket, live status"""
        self.say("My heater isn't working")
        code = self.say("Thompson 214").ticket["code"]

        reply = self.say("What's the status of my ticket?")
        self.assertEqual(reply.kind, "status")
        self.assertIn(code, reply.text)
        self.assertIn("Open", reply.text)

        self.store.update_status(code, "In Progress")          # staff update it on the dashboard
        self.assertIn("In Progress", self.say("What's the status of my ticket?").text)

    def test_e_status_in_a_new_conversation_asks_for_the_number(self):
        """(e) new conversation: asks for the ticket number, then looks it up"""
        code = self.store.create("Wylie Hall", "108", "Sink leaking")["code"]
        self.assertEqual(self.say("What's the status of my ticket?").kind, "ask_details")
        reply = self.say(f"It's {code}")
        self.assertEqual(reply.kind, "status")
        self.assertIn(code, reply.text)

    def test_e_unknown_ticket_number(self):
        """(e) a ticket number that doesn't exist -> says so instead of guessing"""
        self.assertIn("couldn't find", self.say("What's the status of AV-9999?").text)

    def test_every_turn_is_logged(self):
        """every turn goes into the conversation log (here: the temporary test log)"""
        self.say("Can my friend stay over this weekend?")
        self.assertIn("Can my friend stay over this weekend?",
                      agent.LOG_FILE.read_text(encoding="utf-8").splitlines()[-1])


class Voice(unittest.TestCase):
    """Mic checks that don't need Whisper."""

    def test_silent_recording_is_detected(self):
        self.assertLess(voice.loudness(wav(0.0)), 0.003)

    def test_normal_recording_passes(self):
        self.assertGreater(voice.loudness(wav(0.2)), 0.003)


class Screens(unittest.TestCase):
    """The real Streamlit pages, run by AppTest (a headless browser stand-in)."""

    def setUp(self):
        TicketStore().clear()

    def chat(self, *messages):
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
        at.run()
        for message in messages:
            at.chat_input[0].set_value(message).run()
        self.assertFalse(at.exception, at.exception)
        return at

    @staticmethod
    def last_reply(at) -> str:
        return "\n".join(m.value for m in at.chat_message[-1].markdown)

    def dashboard(self):
        at = AppTest.from_file(str(ROOT / "views" / "staff_dashboard.py"), default_timeout=60)
        at.run()
        self.assertFalse(at.exception, at.exception)
        return at

    def test_chat_answers_with_sources(self):
        """(a) on screen: the answer shows a 'Sources: ... page' line"""
        reply = self.last_reply(self.chat("Can my friend stay over this weekend?"))
        self.assertIn("(Handbook p.", reply)
        self.assertIn("Sources:", reply)

    def test_repair_ticket_appears_on_the_dashboard(self):
        """(b) on screen: a repair made in the chat shows up on the staff dashboard"""
        reply = self.last_reply(self.chat("My heater isn't working", "Thompson 214"))
        self.assertIn("🎫 AV-", reply)
        board = self.dashboard()
        self.assertEqual(next(m.value for m in board.metric if m.label == "Open tickets"), "1")
        self.assertIn("Thompson Hall", board.dataframe[0].value["Building"].tolist())

    def test_emergency_alert_on_the_dashboard(self):
        """(d) on screen: an emergency shows as a red alert at the top of the dashboard"""
        self.chat("There's smoke coming from an outlet in Buchanan 302")
        alerts = [e.value for e in self.dashboard().error]
        self.assertTrue(any("EMERGENCY" in a and "Buchanan Hall" in a for a in alerts), alerts)


if __name__ == "__main__":
    unittest.main(verbosity=2)
