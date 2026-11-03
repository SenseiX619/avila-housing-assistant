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
import llm                                       # noqa: E402
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


def wav(volume: float, seconds: float = 1.0, silence_before: float = 0.0) -> bytes:
    """A 16-bit test recording like the mic sends: optional silence, then a tone (volume 0 = silence)."""
    rate = 16000
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * int(silence_before * rate) + b"".join(
            struct.pack("<h", int(volume * 32767 * math.sin(i / 8))) for i in range(int(seconds * rate))))
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

    def test_a_citations_use_the_printed_page_number(self):
        """(a) the Lockouts section is printed as p. 14 (PDF page 15): we cite 14, what students see"""
        lockouts = [c for c in self.agent.retriever.chunks if "LOCKOUTS" in c["text"]]
        self.assertTrue(lockouts, "Handbook not loaded - run: python ingest.py")
        self.assertEqual((lockouts[0]["page"], lockouts[0]["pdf_page"]), (14, 15))

    def test_a_sources_list_only_the_pages_the_answer_cites(self):
        """(a) the 'Sources' line shows the pages the answer cites, not every page we searched"""
        with mock.patch("llm.chat_json", return_value=ai_said("question")), \
             mock.patch("llm.chat", return_value="A lockout costs $25 after the third time (Handbook p. 14)."):
            reply = self.say("I got locked out of my room. Is there a fee?")
        self.assertEqual(reply.sources, [14])

    def test_b_voice_repair_asks_for_details_then_creates_ticket(self):
        """(b) mic: 'My heater isn't working' -> asks hall + room -> ticket saved as Voice"""
        first = self.say("My heater isn't working", channel="voice")
        self.assertEqual(first.kind, "ask_details")
        self.assertIn("Tap your hall", first.text)
        self.assertEqual(first.options, config.BUILDINGS)

        second = self.say("Thompson 214", channel="voice")
        self.assertEqual(second.kind, "ticket")
        self.assertEqual((second.ticket["building"], second.ticket["room"]), ("Thompson Hall", "214"))
        self.assertEqual(second.ticket["channel"], "voice")
        self.assertEqual([t["code"] for t in self.store.all()], [second.ticket["code"]])

    def test_b_tap_through_a_repair_without_any_ai_call(self):
        """(b) tap 'Report a repair' -> 'Heat / AC' -> 'Thompson Hall' -> type 214: ticket, zero AI calls"""
        no_ai = mock.Mock(side_effect=AssertionError("a tapped button must not call the AI"))
        with mock.patch("llm.chat_json", no_ai), mock.patch("llm.chat", no_ai):
            self.assertIn("🔥 Heat / AC", self.say(agent.REPAIR).options)
            self.assertEqual(self.say("🔥 Heat / AC").options, config.BUILDINGS)
            self.assertIn("room number", self.say("Thompson Hall").text)
            done = self.say("214")
        self.assertEqual(done.kind, "ticket")
        t = done.ticket
        self.assertEqual((t["category"], t["urgency"], t["building"], t["room"]),
                         ("Heating/AC", "urgent", "Thompson Hall", "214"))
        self.assertIn(agent.MY_TICKET, done.options)

    def test_main_menu_buttons(self):
        """the main buttons: question -> example questions, my ticket -> asks the number, emergency -> call buttons"""
        self.assertEqual(self.say(agent.QUESTION).options, agent.EXAMPLE_QUESTIONS)
        self.assertIn("ticket number", self.say(agent.MY_TICKET).text)
        emergency = self.say(agent.EMERGENCY)
        self.assertEqual(emergency.calls, [agent.CALL_911, agent.CALL_SAFETY])
        self.assertEqual(agent.CALL_SAFETY[1], "tel:+18169856079")

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
        self.assertEqual(reply.calls, [agent.CALL_SAFETY, agent.CALL_911])

    def test_d_real_fire_calls_911_first(self):
        """(d) a real fire -> 911 first, then Campus Safety, then the emergency ticket"""
        reply = self.say("There's a fire in Buchanan 302!")
        self.assertEqual(reply.kind, "emergency")
        self.assertLess(reply.text.index("911"), reply.text.index(config.CAMPUS_SAFETY_PHONE))
        self.assertLess(reply.text.index(config.CAMPUS_SAFETY_PHONE), reply.text.index(reply.ticket["code"]))
        self.assertEqual(reply.calls, [agent.CALL_911, agent.CALL_SAFETY])

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

    # ---------- task 4: when the AI misbehaves ----------
    def test_out_of_tokens_switches_model_without_waiting(self):
        """task 5: the main model is out of free tokens -> the backup answers at once, and the main
        model rests (is skipped) until Groq says it's ready again"""
        class OutOfTokens(Exception):
            status_code = 429
            response = mock.Mock(headers={"retry-after": "7"})

        def create(model, **kwargs):
            calls.append(model)
            if model == "main":
                raise OutOfTokens()
            return mock.Mock(choices=[mock.Mock(message=mock.Mock(content="hi"))])

        calls = []
        client = mock.Mock()
        client.chat.completions.create = create
        with mock.patch.object(config, "FAKE_LLM", False), mock.patch.object(llm, "_groq", return_value=client), \
             mock.patch.object(llm, "_available_models", ["main", "backup"]), \
             mock.patch.object(llm, "_resting_until", {}):
            self.assertEqual(llm.chat([{"role": "user", "content": "x"}]), "hi")
            self.assertEqual(llm.chat([{"role": "user", "content": "y"}]), "hi")
        self.assertEqual(calls, ["main", "backup", "backup"], "the 2nd message skips the resting model")

    def test_broken_json_is_retried_once(self):
        """the AI's first answer isn't JSON, the retry is -> the retry is used, the problem noted"""
        problems = []
        with mock.patch("llm.chat", side_effect=["Sure! Here you go:", '{"intent": "question"}']):
            self.assertEqual(llm.chat_json([], problems), {"intent": "question"})
        self.assertEqual(len(problems), 1)

    def test_broken_json_twice_raises(self):
        with mock.patch("llm.chat", return_value="I can't do JSON today"), self.assertRaises(llm.BadJSON):
            llm.chat_json([])

    def last_log_rows(self, n=3):
        return agent.LOG_FILE.read_text(encoding="utf-8").splitlines()[-n:]

    def test_ai_down_emergency_still_handled(self):
        """the AI is unreachable -> rule-based fallback: smoke still gets the safety message and a ticket"""
        with mock.patch("llm.chat", side_effect=RuntimeError("The AI service is unavailable right now")):
            reply = self.say("There's smoke coming from an outlet in Buchanan 302")
        self.assertEqual(reply.kind, "emergency")
        self.assertEqual((reply.ticket["building"], reply.ticket["room"]), ("Buchanan Hall", "302"))
        self.assertTrue(any('"used_fallback": true' in row for row in self.last_log_rows()))

    def test_ai_sends_garbage_repair_still_works(self):
        """the AI sends non-JSON twice on every message -> a repair still becomes a ticket"""
        with mock.patch("llm.chat", return_value="hmm"):
            self.assertEqual(self.say("My heater isn't working").kind, "ask_details")
            reply = self.say("Thompson 214")
        self.assertEqual(reply.kind, "ticket")
        self.assertEqual((reply.ticket["building"], reply.ticket["room"]), ("Thompson Hall", "214"))

    def test_sloppy_json_shape_does_not_crash(self):
        """valid JSON with the wrong shape ('ticket': 'none', odd intent) -> handled, no error"""
        sloppy = {"intent": ["?"], "ticket": "none", "language": None, "cancel": "yes"}
        with mock.patch("llm.chat_json", return_value=sloppy):
            reply = self.say("Can my friend stay over this weekend?")
        self.assertEqual(reply.kind, "answer")

    def test_errors_are_logged_not_shown(self):
        """an unexpected error -> a friendly message with buttons; the technical detail goes to the log"""
        with mock.patch.object(self.agent.retriever, "search", side_effect=ValueError("index exploded")):
            reply = self.say("Can my friend stay over this weekend?")
        self.assertEqual(reply.kind, "error")
        self.assertNotIn("index exploded", reply.text)
        self.assertEqual(reply.options, agent.MAIN_MENU)
        self.assertTrue(any("index exploded" in row for row in self.last_log_rows()))

    def test_every_turn_is_logged(self):
        """every turn goes into the conversation log (here: the temporary test log)"""
        self.say("Can my friend stay over this weekend?")
        self.assertIn("Can my friend stay over this weekend?",
                      agent.LOG_FILE.read_text(encoding="utf-8").splitlines()[-1])


class Voice(unittest.TestCase):
    """Mic checks that don't need Whisper."""

    def test_silent_recording_is_detected(self):
        self.assertLess(voice.loudness(wav(0.0)), voice.SILENT)

    def test_normal_recording_passes(self):
        self.assertGreater(voice.loudness(wav(0.2)), voice.SILENT)

    def test_pause_before_speaking_still_counts(self):
        """3 seconds of silence, then quiet speech: still heard (the old average-loudness check said 'silent')"""
        self.assertGreater(voice.loudness(wav(0.01, seconds=0.3, silence_before=3)), voice.SILENT)

    def test_whisper_phantom_phrases_are_caught(self):
        for ghost in ["", "you", "Thank you.", "Thank you. Thank you.", "¡Gracias!",
                      "Subtítulos realizados por la comunidad de Amara.org"]:
            self.assertTrue(voice.is_ghost(ghost), ghost)
        for real in ["My heater isn't working", "Thank you so much for fixing my heater"]:
            self.assertFalse(voice.is_ghost(real), real)


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
        self.assertIn("Thompson Hall 214", board.dataframe[0].value["Where"].tolist())

    def test_reset_demo_restarts_numbering_and_loads_samples(self):
        """task 6: Reset demo -> only the 6 sample tickets, numbered AV-1001..AV-1006, next is AV-1007"""
        store = TicketStore()
        store.create("Wylie Hall", "108", "Old test ticket")
        board = self.dashboard()
        next(b for b in board.button if b.label == "Yes, reset the demo").click().run()
        self.assertFalse(board.exception, board.exception)
        self.assertEqual(sorted(t["code"] for t in store.all()), [f"AV-{1001 + i}" for i in range(6)])
        self.assertEqual(store.create("Thompson Hall", "214", "New")["code"], "AV-1007")
        self.assertTrue(any("next new ticket is AV-1007" in s.value for s in board.success))

    def test_emergency_alert_on_the_dashboard(self):
        """(d) on screen: an emergency shows as a red alert at the top of the dashboard"""
        self.chat("There's smoke coming from an outlet in Buchanan 302")
        alerts = [e.value for e in self.dashboard().error]
        self.assertTrue(any("EMERGENCY" in a and "Buchanan Hall" in a for a in alerts), alerts)

    def test_tap_through_a_repair_on_screen(self):
        """(b) on screen: tap the buttons, type the room, and the ticket card appears"""
        at = self.chat()
        for tap in [agent.REPAIR, "💧 Leak / plumbing", "Wylie Hall"]:
            at.pills[0].set_value(tap).run()
            self.assertFalse(at.exception, at.exception)
        self.assertIn("room number in Wylie Hall", self.last_reply(at))
        at.chat_input[0].set_value("108").run()
        self.assertIn("🎫 AV-", self.last_reply(at))
        self.assertEqual(len(at.pills), 1, "buttons only under the latest answer")

    def record(self, audio: bytes):
        at = self.chat()
        at.audio_input[0].set_value(("clip.wav", audio, "audio/wav")).run()
        self.assertFalse(at.exception, at.exception)
        return at

    def test_mic_has_a_permission_tip(self):
        """(b) on screen: the mic's ⓘ tip explains how to allow the microphone"""
        self.assertIn("set Microphone to Allow", self.chat().audio_input[0].help)

    def test_voice_recording_reaches_the_assistant(self):
        """(b) on screen: a recording is transcribed, shown with 🎤, and answered"""
        at = self.record(wav(0.2))
        said = [m.markdown[0].value for m in at.chat_message if m.name == "user"]
        self.assertEqual(said, ["🎤 The heater in my room is not working"])
        self.assertIn("hall", self.last_reply(at).lower())

    def test_silent_recording_shows_a_mic_warning_and_is_logged(self):
        """(b) on screen: a silent recording gets a mic warning, and the attempt is in the log"""
        at = self.record(wav(0.0))
        self.assertIn("couldn't hear anything", " ".join(w.value for w in at.warning))
        self.assertEqual(len(at.chat_message), 1, "only the greeting - nothing was sent to the assistant")
        logged = agent.LOG_FILE.read_text(encoding="utf-8").splitlines()[-1]
        self.assertIn('"mic_problem": "I couldn\'t hear anything', logged)


if __name__ == "__main__":
    unittest.main(verbosity=2)
