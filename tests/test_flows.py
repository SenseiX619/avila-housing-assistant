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
config.TICKET_EMAIL_TO = config.SMTP_USER = config.SMTP_APP_PASSWORD = ""   # tests never send real email
config.STAFF_PASSWORD = ""                       # dashboard open in tests; the lock tests set their own password

import agent                                     # noqa: E402
import llm                                       # noqa: E402
import notify                                    # noqa: E402
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

        second = self.say("Thompson 1214", channel="voice")
        self.assertEqual(second.kind, "ticket")
        self.assertEqual((second.ticket["building"], second.ticket["room"]), ("Thompson Hall", "1214"))
        self.assertEqual(second.ticket["channel"], "voice")
        self.assertEqual([t["code"] for t in self.store.all()], [second.ticket["code"]])

    def test_b_tap_through_a_repair_without_any_ai_call(self):
        """(b) tap 'Report a repair' -> 'No heat' -> 'Thompson Hall' -> type 1214: EMERGENCY repair, zero AI calls"""
        no_ai = mock.Mock(side_effect=AssertionError("a tapped button must not call the AI"))
        with mock.patch("llm.chat_json", no_ai), mock.patch("llm.chat", no_ai):
            self.assertIn("🔥 No heat", self.say(agent.REPAIR).options)
            self.assertEqual(self.say("🔥 No heat").options, config.BUILDINGS)
            self.assertIn("room number", self.say("Thompson Hall").text)
            done = self.say("1214")
        self.assertEqual(done.kind, "ticket")
        t = done.ticket
        self.assertEqual((t["category"], t["urgency"], t["building"], t["room"]),
                         ("Heating/AC", "emergency", "Thompson Hall", "1214"))
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

    def test_b_repair_net_understands_phone_apostrophes(self):
        """(b) phones type a curly apostrophe ("isn’t"): the repair net still recognises it"""
        self.assertTrue(agent.REPAIR_PATTERN.search("My heater isn’t working"))
        self.assertTrue(agent.REPAIR_PATTERN.search("The door won’t lock"))

    def test_c_spanish_reply_english_ticket(self):
        """(c) Spanish repair -> reply translated to Spanish, ticket saved in English"""
        analysis = ai_said("maintenance", language="Spanish", building="Wylie Hall", room="1408",
                           issue="Bathroom leak", category="Plumbing", urgency="urgent")
        with mock.patch("llm.chat_json", return_value=analysis):
            reply = self.say("Mi baño tiene una fuga en Wylie 1408")
        self.assertEqual(reply.kind, "ticket")
        self.assertTrue(reply.text.startswith("[FAKE TRANSLATION]"), "reply should be translated")
        self.assertEqual(reply.ticket["issue"], "Bathroom leak")
        self.assertEqual(reply.ticket["language"], "Spanish")
        self.assertEqual(reply.ticket["urgency"], "urgent")

    def test_d_smoke_calls_campus_safety_first(self):
        """(d) smoke from an outlet -> Campus Safety first, 911 as the backup, then the emergency ticket"""
        reply = self.say("There's smoke coming from an outlet in Buchanan 502")
        self.assertEqual(reply.kind, "emergency")
        self.assertEqual(reply.ticket["urgency"], "emergency")
        self.assertEqual((reply.ticket["building"], reply.ticket["room"]), ("Buchanan Hall", "502"))
        self.assertLess(reply.text.index(config.CAMPUS_SAFETY_PHONE), reply.text.index("911"))
        self.assertLess(reply.text.index("911"), reply.text.index(reply.ticket["code"]))
        self.assertEqual(reply.calls, [agent.CALL_SAFETY, agent.CALL_911])

    def test_d_real_fire_calls_911_first(self):
        """(d) a real fire -> 911 first, then Campus Safety, then the emergency ticket"""
        reply = self.say("There's a fire in Buchanan 502!")
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
        analysis = ai_said("emergency", language="Spanish", building="Wylie Hall", room="1408",
                           issue="Fire in the room", urgency="emergency")
        with mock.patch("llm.chat_json", return_value=analysis):
            reply = self.say("¡Hay un incendio en mi cuarto! Wylie 1408")
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
        analysis = ai_said("maintenance", building="Wylie Hall", room="1410",
                           issue="Smoke detector beeping", urgency="normal")
        with mock.patch("llm.chat_json", return_value=analysis):
            reply = self.say("My smoke detector keeps beeping, I'm in Wylie 1410")
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
        analysis = ai_said("maintenance", language="Spanish", building="Buchanan Hall", room="502",
                           issue="Smoke coming from an outlet", urgency="normal")
        with mock.patch("llm.chat_json", return_value=analysis):
            reply = self.say("Sale humo de un enchufe en Buchanan 502")
        self.assertEqual(reply.kind, "emergency")
        self.assertEqual(reply.ticket["urgency"], "emergency")

    def test_d_emergency_net_catches_ai_calling_it_a_repair(self):
        """(d) safety net: the AI says 'maintenance' for smoke -> still an emergency"""
        analysis = ai_said("maintenance", building="Buchanan Hall", room="502",
                           issue="Outlet smoking", urgency="normal")
        with mock.patch("llm.chat_json", return_value=analysis):
            reply = self.say("There's smoke coming from an outlet in Buchanan 502")
        self.assertEqual(reply.kind, "emergency")
        self.assertEqual(reply.ticket["urgency"], "emergency")

    def test_d_emergency_is_never_downgraded(self):
        """(d) an emergency stays an emergency when the room comes in a later message"""
        self.say("There's smoke in the hallway!")
        analysis = ai_said("maintenance", building="Buchanan Hall", room="502", urgency="normal")
        with mock.patch("llm.chat_json", return_value=analysis):
            reply = self.say("Buchanan 502")
        self.assertEqual(reply.ticket["urgency"], "emergency")
        self.assertEqual(len(self.store.all()), 1, "the room goes on the alert staff already have")

    def test_e_status_of_my_ticket(self):
        """(e) 'What's the status of my ticket?' -> this conversation's latest ticket, live status"""
        self.say("My heater isn't working")
        code = self.say("Thompson 1214").ticket["code"]

        reply = self.say("What's the status of my ticket?")
        self.assertEqual(reply.kind, "status")
        self.assertIn(code, reply.text)
        self.assertIn("Submitted", reply.text)

        self.store.update_status(code, "In Progress")          # staff update it on the dashboard
        self.assertIn("In Progress", self.say("What's the status of my ticket?").text)

    def test_e_status_in_a_new_conversation_asks_for_the_number(self):
        """(e) new conversation: asks for the ticket number, then looks it up"""
        code = self.store.create("Wylie Hall", "1408", "Sink leaking")["code"]
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
            reply = self.say("There's smoke coming from an outlet in Buchanan 502")
        self.assertEqual(reply.kind, "emergency")
        self.assertEqual((reply.ticket["building"], reply.ticket["room"]), ("Buchanan Hall", "502"))
        self.assertTrue(any('"used_fallback": true' in row for row in self.last_log_rows()))

    def test_ai_sends_garbage_repair_still_works(self):
        """the AI sends non-JSON twice on every message -> a repair still becomes a ticket"""
        with mock.patch("llm.chat", return_value="hmm"):
            self.assertEqual(self.say("My heater isn't working").kind, "ask_details")
            reply = self.say("Thompson 1214")
        self.assertEqual(reply.kind, "ticket")
        self.assertEqual((reply.ticket["building"], reply.ticket["room"]), ("Thompson Hall", "1214"))

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

    # ---------- ticket emails (all faked - nothing is really sent) ----------
    def email_settings(self):
        return mock.patch.multiple(config, TICKET_EMAIL_TO="staff@example.edu", SMTP_USER="team@gmail.com",
                                   SMTP_APP_PASSWORD="fake app password")

    def test_every_new_ticket_is_emailed(self):
        """a finished repair report hands the new ticket to the emailer"""
        with mock.patch.object(notify, "send_ticket_email") as send:
            for message in (agent.REPAIR, "🔥 No heat", "Thompson Hall", "1214"):
                ticket = self.say(message).ticket
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0]["code"], ticket["code"])

    def test_repeating_the_room_corrects_the_ticket(self):
        """speech misheard the room ("two twenty" for 222); saying it again fixes that ticket - no duplicate,
        an UPDATED email"""
        heater = dict(issue="Heater not working", building="Villa Ventura", category="Heating/AC", urgency="urgent")
        with mock.patch.object(notify, "send_ticket_email") as send:
            with mock.patch("llm.chat_json", return_value=ai_said("maintenance", room="220", **heater)):
                first = self.say("Villa Ventura, room two twenty", channel="voice").ticket
            with mock.patch("llm.chat_json", return_value=ai_said("maintenance", room="222", **heater)):
                fixed = self.say("Villa Ventura, room two twenty-two", channel="voice")
        self.assertEqual(fixed.ticket["code"], first["code"])
        self.assertEqual(fixed.ticket["room"], "222")
        self.assertIn("Fixed!", fixed.text)
        self.assertEqual(len(self.store.all()), 1, "no duplicate ticket")
        self.assertEqual(send.call_args.kwargs["update"], "Room corrected")

    def test_a_different_problem_is_a_new_ticket(self):
        """a new problem in the same hall is a new ticket, not a correction"""
        with mock.patch("llm.chat_json", return_value=ai_said("maintenance", issue="Heater not working",
                                                               building="Wylie Hall", room="1408")):
            first = self.say("My heater is broken, Wylie 1408").ticket
        with mock.patch("llm.chat_json", return_value=ai_said("maintenance", issue="Sink leaking",
                                                               building="Wylie Hall", room="1410")):
            second = self.say("Also the sink in Wylie 1410 is leaking").ticket
        self.assertNotEqual(first["code"], second["code"])

    # ---------- routing: the room must belong to the hall (config.ROOM_RANGES) ----------
    def no_ai(self):
        no_ai = mock.Mock(side_effect=AssertionError("a tapped button must not call the AI"))
        return mock.patch("llm.chat_json", no_ai), mock.patch("llm.chat", no_ai)

    def test_room_ranges(self):
        """Thompson 1100-1300, Wylie 1400-1600, Buchanan 400-600, Villa Ventura 200-300 (both ends included)"""
        for room, hall in [("1100", "Thompson Hall"), ("1300", "Thompson Hall"), ("1400", "Wylie Hall"),
                           ("1600", "Wylie Hall"), ("400", "Buchanan Hall"), ("600", "Buchanan Hall"),
                           ("200", "Villa Ventura"), ("300", "Villa Ventura"), ("Room 1450", "Wylie Hall"),
                           ("14-50", "Wylie Hall"), ("1210b", "Thompson Hall")]:
            self.assertEqual(agent.hall_for_room(room), hall, room)
        for room in ["1099", "1301", "1350", "700", "199", "3-2", "B12", "99999", "", None]:
            self.assertIsNone(agent.hall_for_room(room), room)

    def test_invalid_room_is_asked_again(self):
        """'Wylie 99999' -> no ticket, says Wylie's rooms are 1400-1600 and asks again; '1450' -> the ticket"""
        with mock.patch("llm.chat_json", return_value=ai_said("maintenance", building="Wylie Hall", room="99999",
                                                               issue="Sink leaking")):
            reply = self.say("My sink is leaking, Wylie 99999")
        self.assertEqual(reply.kind, "ask_details")
        self.assertIn("isn't an Avila housing room", reply.text)
        self.assertIn("1400-1600", reply.text)
        self.assertEqual(self.store.all(), [], "no ticket for a room that doesn't exist")
        done = self.say("1450")
        self.assertEqual((done.kind, done.ticket["building"], done.ticket["room"]), ("ticket", "Wylie Hall", "1450"))

    def test_spoken_dashes_are_not_a_room(self):
        """speech wrote "3-2" for three twenty-two -> not a Villa Ventura room, so it's asked again (no ticket)"""
        with mock.patch("llm.chat_json", return_value=ai_said("maintenance", building="Villa Ventura", room="3-2",
                                                               issue="Heater not working")):
            reply = self.say("Villa Ventura, room three to two", channel="voice")
        self.assertEqual(reply.kind, "ask_details")
        self.assertIn("200-300", reply.text)
        self.assertEqual(self.store.all(), [])

    def test_room_in_the_wrong_hall_offers_the_right_one(self):
        """'Thompson 1450' (a Wylie number) -> no ticket; one tap on 'Wylie Hall, room 1450' fixes it, no AI"""
        with mock.patch("llm.chat_json", return_value=ai_said("maintenance", building="Thompson Hall", room="1450",
                                                               issue="Heater not working")):
            reply = self.say("My heater isn't working, Thompson 1450")
        self.assertEqual(reply.kind, "ask_details")
        self.assertIn("1100-1300", reply.text)
        self.assertEqual(reply.options, ["Wylie Hall, room 1450"])
        self.assertEqual(self.store.all(), [])
        json_off, chat_off = self.no_ai()
        with json_off, chat_off:
            done = self.say("Wylie Hall, room 1450")
        self.assertEqual((done.kind, done.ticket["building"], done.ticket["room"]), ("ticket", "Wylie Hall", "1450"))

    def test_room_number_alone_fills_in_the_hall(self):
        """tap Report a repair -> No heat -> type '1450' without a hall: a Wylie Hall ticket, zero AI calls"""
        json_off, chat_off = self.no_ai()
        with json_off, chat_off:
            self.say(agent.REPAIR)
            self.say("🔥 No heat")
            done = self.say("1450")
        self.assertEqual((done.kind, done.ticket["building"], done.ticket["room"]), ("ticket", "Wylie Hall", "1450"))

    def test_unknown_hall_is_asked_again(self):
        """a hall that isn't one of the four -> says so and shows the four hall buttons"""
        with mock.patch("llm.chat_json", return_value=ai_said("maintenance", building="Marian Center",
                                                               issue="Sink leaking")):
            reply = self.say("My sink is leaking in the Marian Center")
        self.assertIn("I don't know a hall called Marian Center", reply.text)
        self.assertEqual(reply.options, config.BUILDINGS)
        self.assertEqual(self.store.all(), [])

    def test_emergency_room_alone_fills_in_the_hall(self):
        """'smoke in room 1450' -> emergency ticket for Wylie Hall 1450 (emergencies are never held back)"""
        reply = self.say("There's smoke in room 1450!")
        self.assertEqual(reply.kind, "emergency")
        self.assertEqual((reply.ticket["building"], reply.ticket["room"]), ("Wylie Hall", "1450"))

    def test_sample_tickets_use_real_rooms(self):
        """the demo's sample tickets all fit their hall's room range"""
        self.store.load_sample_tickets()
        for t in self.store.all():
            self.assertEqual(agent.hall_for_room(t["room"]), t["building"], t["code"])

    # ---------- step 3: emergencies are never queued ----------
    def test_danger_net_catches_gas_and_ceiling_leaks(self):
        """the backup danger-word list (used when the AI misses it or is down) knows gas and ceiling leaks"""
        for message in ["It smells like gas in my room", "I think there's a gas leak", "the stove is leaking gas",
                        "Water is coming through the ceiling", "My ceiling is leaking"]:
            self.assertTrue(agent.EMERGENCY_PATTERN.search(message), message)
        for message in ["My ceiling fan is broken", "Can I have a gas grill?", "The sink is leaking"]:
            self.assertFalse(agent.EMERGENCY_PATTERN.search(message), message)

    def test_ai_down_gas_smell_calls_911_first(self):
        """the AI is unreachable and someone smells gas -> 911 first, emergency ticket"""
        with mock.patch("llm.chat", side_effect=RuntimeError("The AI service is unavailable right now")):
            reply = self.say("It smells like gas in Wylie 1450")
        self.assertEqual(reply.kind, "emergency")
        self.assertLess(reply.text.index("911"), reply.text.index(config.CAMPUS_SAFETY_PHONE))
        self.assertEqual((reply.ticket["building"], reply.ticket["room"], reply.ticket["urgency"]),
                         ("Wylie Hall", "1450", "emergency"))

    def test_emergency_alerts_staff_before_the_room_is_known(self):
        """'smoke in the hallway!' -> staff get the emergency ticket AT ONCE; the hall and room are added later
        to that same ticket (one ticket, a 'Location added' email)"""
        with mock.patch.object(notify, "send_ticket_email") as send:
            first = self.say("There's smoke in the hallway!")
            self.assertEqual(first.kind, "emergency")
            self.assertEqual((first.ticket["building"], first.ticket["urgency"]), (agent.NO_LOCATION, "emergency"))
            self.assertIn(first.ticket["code"], first.text)
            send.assert_called_once()                                  # emailed before we know where
            self.assertEqual(self.say("The smoke is getting worse!").ticket["code"], first.ticket["code"], "no second alert")
            self.say("Buchanan Hall")
            done = self.say("502")
        self.assertEqual(done.ticket["code"], first.ticket["code"])
        self.assertEqual((done.ticket["building"], done.ticket["room"]), ("Buchanan Hall", "502"))
        self.assertEqual(len(self.store.all()), 1)
        self.assertEqual(send.call_args.kwargs["update"], "Location added")

    def test_no_heat_is_an_emergency_repair(self):
        """(b) 'My heater isn't working' -> an EMERGENCY repair (not the 2-day queue), with who to contact now"""
        self.say("My heater isn't working", channel="voice")
        reply = self.say("Thompson 1214", channel="voice")
        self.assertEqual(reply.kind, "ticket")
        self.assertEqual(reply.ticket["urgency"], "emergency")
        self.assertIn("RA on duty", reply.text)
        self.assertIn(config.CAMPUS_SAFETY_PHONE, reply.text)
        self.assertEqual(reply.calls, [agent.CALL_SAFETY])

    def test_cant_wait_repairs(self):
        """no heat, a door that won't lock, an overflow can't wait; ordinary repairs keep their deadline"""
        for message in ["There's no heat in my room", "The heating isn't working", "My heater won't turn on",
                        "Our door won’t lock", "The toilet is overflowing", "My lock is broken"]:
            self.assertTrue(agent.CANT_WAIT.search(message), message)
        for message in ["The sink is leaking", "My AC is not working", "The light in my closet is out"]:
            self.assertFalse(agent.CANT_WAIT.search(message), message)
        with mock.patch("llm.chat_json", return_value=ai_said("maintenance", building="Wylie Hall", room="1450",
                                                               issue="Door won't lock", urgency="urgent")):
            self.assertEqual(self.say("My door won't lock, Wylie 1450").ticket["urgency"], "emergency")
        with mock.patch("llm.chat_json", return_value=ai_said("maintenance", building="Wylie Hall", room="1452",
                                                               issue="Sink leaking", urgency="normal")):
            self.assertEqual(self.say("My sink is leaking, Wylie 1452").ticket["urgency"], "normal")

    def test_lockout_says_who_can_let_you_in(self):
        """'I'm locked out' -> who can let them in comes first, with a call button; no repair ticket"""
        with mock.patch("llm.chat_json", return_value=ai_said("maintenance")):
            reply = self.say("I'm locked out of my room")
        self.assertTrue(reply.text.startswith(agent.LOCKOUT_HELP))
        self.assertEqual(reply.calls, [agent.CALL_SAFETY])
        self.assertEqual(self.store.all(), [])
        self.assertFalse(self.state.get("draft"))

    def test_lockout_fee_question_still_answered(self):
        """'I got locked out... Is there a fee?' -> lockout help first, then the handbook answer and its page"""
        with mock.patch("llm.chat_json", return_value=ai_said("question")),              mock.patch("llm.chat", return_value="A lockout costs $25 after the third time (Handbook p. 14)."):
            reply = self.say("I got locked out of my room. Is there a fee?")
        self.assertTrue(reply.text.startswith(agent.LOCKOUT_HELP))
        self.assertIn("$25", reply.text)
        self.assertEqual(reply.sources, [14])

    def test_ai_down_lockout_still_gets_help(self):
        with mock.patch("llm.chat", side_effect=RuntimeError("The AI service is unavailable right now")):
            reply = self.say("I'm locked out of my room")
        self.assertEqual(reply.text, agent.LOCKOUT_HELP)
        self.assertEqual(reply.calls, [agent.CALL_SAFETY])

    def test_emergency_email_is_high_priority(self):
        ticket = self.store.create("Wylie Hall", "1450", "Smoke from outlet", "Electrical", "emergency")
        self.assertEqual(notify.ticket_email(ticket)["Importance"], "High")
        normal = self.store.create("Wylie Hall", "1450", "Sink leaking")
        self.assertIsNone(notify.ticket_email(normal)["Importance"])

    # ---------- step 4: the full life of a ticket, and back to the student ----------
    def test_ticket_lifecycle_records_who_and_when(self):
        """Submitted -> Assigned (who + when) -> In Progress -> Resolved (when); reopening clears 'resolved'"""
        t = self.store.create("Wylie Hall", "1450", "Sink leaking")
        self.assertEqual((t["status"], t["assigned_to"], t["assigned_at"], t["resolved_at"]),
                         ("Submitted", None, None, None))
        t = self.store.update_status(t["code"], "Assigned", assigned_to="Plumbing")
        self.assertEqual((t["status"], t["assigned_to"]), ("Assigned", "Plumbing"))
        assigned_at = t["assigned_at"]
        self.assertTrue(assigned_at)
        t = self.store.update_status(t["code"], "In Progress")
        self.assertEqual((t["assigned_to"], t["assigned_at"]), ("Plumbing", assigned_at), "assignee and time kept")
        t = self.store.update_status(t["code"], "Resolved")
        self.assertTrue(t["resolved_at"])
        self.assertFalse(t["overdue"])
        t = self.store.update_status(t["code"], "In Progress")
        self.assertIsNone(t["resolved_at"])

    def test_old_database_is_upgraded(self):
        """a database from before v1.18 (no assignee columns, status 'Open') still opens, as 'Submitted'"""
        import sqlite3
        path = TMP / "old.db"
        with sqlite3.connect(path) as db:
            db.execute("""CREATE TABLE tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, building TEXT, room TEXT, issue TEXT NOT NULL,
                category TEXT, urgency TEXT, status TEXT DEFAULT 'Open', language TEXT, channel TEXT,
                student_message TEXT)""")
            db.execute("""INSERT INTO tickets (code, created_at, updated_at, building, room, issue, urgency, status)
                          VALUES ('AV-1001', '2026-11-01T10:00:00', '2026-11-01T10:00:00', 'Wylie Hall', '1450',
                                  'Sink leaking', 'normal', 'Open')""")
        t = TicketStore(path).get("AV-1001")
        self.assertEqual((t["status"], t["assigned_to"]), ("Submitted", None))

    def test_status_says_who_has_it(self):
        """(e) status -> 'Assigned to Plumbing', then 'In Progress (Plumbing is on it)'"""
        code = self.store.create("Wylie Hall", "1450", "Sink leaking")["code"]
        self.store.update_status(code, "Assigned", assigned_to="Plumbing")
        self.assertIn("Assigned to Plumbing", self.say(f"What's the status of {code}?").text)
        self.store.update_status(code, "In Progress")
        self.assertIn("In Progress (Plumbing is on it)", self.say(f"What's the status of {code}?").text)

    def test_staff_updates_reach_the_student_chat(self):
        """staff move the ticket on the dashboard -> an 'Update' message in that student's chat, once"""
        for message in (agent.REPAIR, "💧 Leak / plumbing", "Wylie Hall", "1450"):
            code = (self.say(message).ticket or {}).get("code")
        self.assertEqual(self.agent.ticket_updates(self.state), [], "nothing changed yet")
        self.store.update_status(code, "Assigned", assigned_to="Plumbing")
        updates = self.agent.ticket_updates(self.state)
        self.assertEqual(len(updates), 1)
        self.assertIn(f"Update: ticket {code}", updates[0].text)
        self.assertIn("Assigned to Plumbing", updates[0].text)
        self.assertEqual(self.agent.ticket_updates(self.state), [], "each change is told once")

    def resolve_and_check(self):
        code = self.store.create("Wylie Hall", "1450", "Sink leaking")["code"]
        self.say(f"What's the status of {code}?")                 # this chat now watches the ticket
        self.store.update_status(code, "Resolved", assigned_to="Plumbing")
        update = self.agent.ticket_updates(self.state)[0]
        self.assertIn("Did that fix it?", update.text)
        self.assertEqual(update.options, [agent.FIXED_YES, agent.FIXED_NO])
        return code

    def test_resolved_ticket_confirmed_fixed(self):
        """Resolved -> 'Did that fix it?' -> yes: the student confirmed it; the loop is closed"""
        code = self.resolve_and_check()
        with mock.patch.object(notify, "send_ticket_email") as send:
            reply = self.say(agent.FIXED_YES)
        self.assertIn("closed", reply.text)
        send.assert_not_called()
        t = self.store.get(code)
        self.assertEqual((t["status"], t["student_feedback"]), ("Resolved", "fixed"))
        self.assertIn("You confirmed it's fixed", self.say(f"What's the status of {code}?").text)
        self.assertEqual(self.agent.ticket_updates(self.state), [])

    def test_resolved_ticket_not_fixed_is_reopened(self):
        """Resolved -> 'Did that fix it?' -> no: reopened, back to the same team, and staff are emailed"""
        code = self.resolve_and_check()
        with mock.patch.object(notify, "send_ticket_email") as send:
            reply = self.say(agent.FIXED_NO)
        self.assertIn("reopened", reply.text)
        self.assertIn("REOPENED", send.call_args.kwargs["update"])
        t = self.store.get(code)
        self.assertEqual((t["status"], t["assigned_to"], t["student_feedback"], t["resolved_at"]),
                         ("Assigned", "Plumbing", "not fixed", None))
        self.assertEqual(self.agent.ticket_updates(self.state), [], "no 'Update' about the student's own answer")

    def test_ticket_email_content_and_sending(self):
        """the email says what, where, how urgent and the deadline, and goes to TICKET_EMAIL_TO"""
        ticket = self.store.create("Thompson Hall", "1214", "Heater not working", "Heating/AC", "urgent",
                                   channel="voice", student_message="My heater isn't working")
        results = []
        with self.email_settings(), mock.patch("smtplib.SMTP") as smtp:
            self.assertTrue(notify.send_ticket_email(ticket, on_done=results.append, background=False))
        server = smtp.return_value.__enter__.return_value
        server.login.assert_called_once_with("team@gmail.com", "fake app password")
        email = server.send_message.call_args.args[0]
        self.assertEqual(email["To"], "staff@example.edu")
        self.assertIn(ticket["code"], email["Subject"])
        self.assertIn("URGENT", email["Subject"])
        self.assertIn("Thompson Hall, room 1214", email.get_content())
        self.assertIn("My heater isn't working", email.get_content())
        self.assertEqual(results, ["sent"])

    def test_no_email_settings_no_email(self):
        """email not set up in .env -> nothing is sent, and the ticket still works"""
        ticket = self.store.create("Wylie Hall", "1408", "Sink leaking")
        with mock.patch("smtplib.SMTP") as smtp:
            self.assertFalse(notify.send_ticket_email(ticket, background=False))
        smtp.assert_not_called()

    def test_failed_email_never_breaks_anything(self):
        """wrong password or no internet -> 'failed: ...' in the log, no crash"""
        ticket = self.store.create("Wylie Hall", "1408", "Sink leaking")
        results = []
        with self.email_settings(), mock.patch("smtplib.SMTP", side_effect=OSError("no internet")):
            notify.send_ticket_email(ticket, on_done=results.append, background=False)
        self.assertTrue(results[0].startswith("failed: OSError"))

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
        for real in ["My heater isn't working", "Thank you so much for fixing my heater", "322", "Room 214."]:
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
        reply = self.last_reply(self.chat("My heater isn't working", "Thompson 1214"))
        self.assertIn("🎫 AV-", reply)
        board = self.dashboard()
        self.assertEqual(next(m.value for m in board.metric if m.label == "Open tickets"), "1")
        self.assertIn("Thompson Hall 1214", board.dataframe[0].value["Where"].tolist())

    def test_reset_demo_restarts_numbering_and_loads_samples(self):
        """task 6: Reset demo -> only the 6 sample tickets, numbered AV-1001..AV-1006, next is AV-1007"""
        store = TicketStore()
        store.create("Wylie Hall", "1408", "Old test ticket")
        board = self.dashboard()
        next(b for b in board.button if b.label == "Yes, reset the demo").click().run()
        self.assertFalse(board.exception, board.exception)
        self.assertEqual(sorted(t["code"] for t in store.all()), [f"AV-{1001 + i}" for i in range(6)])
        self.assertEqual(store.create("Thompson Hall", "1214", "New")["code"], "AV-1007")
        self.assertTrue(any("next new ticket is AV-1007" in s.value for s in board.success))

    def test_phone_qr_code_on_the_dashboard(self):
        """phones: with an online address, the dashboard shows a QR code to the student page"""
        with mock.patch.dict(os.environ, {"APP_URL": "https://avila-housing.streamlit.app/staff_dashboard"}):
            import phone_link
            self.assertEqual(phone_link.app_url(), "https://avila-housing.streamlit.app")
            captions = " ".join(c.value for c in self.dashboard().caption)
        self.assertIn("Scan to try it on your phone", captions)
        self.assertIn("https://avila-housing.streamlit.app", captions)

    def test_no_qr_code_for_this_computer_only(self):
        """phones can't open 'localhost', so offline the dashboard explains how to get a phone link"""
        with mock.patch.dict(os.environ, {"APP_URL": "http://localhost:8501"}):
            captions = " ".join(c.value for c in self.dashboard().caption)
        self.assertIn("put the app online first", captions)

    def locked_dashboard(self):
        with mock.patch.object(config, "STAFF_PASSWORD", "test-staff-pass"):
            at = AppTest.from_file(str(ROOT / "views" / "staff_dashboard.py"), default_timeout=60)
            at.run()
        self.assertFalse(at.exception, at.exception)
        return at

    def test_dashboard_is_locked_without_the_password(self):
        """staff lock: with a password set, the dashboard shows no tickets and no Reset button until it's typed"""
        TicketStore().create("Wylie Hall", "1450", "Sink leaking")
        at = self.locked_dashboard()
        self.assertEqual(len(at.dataframe), 0, "no ticket table before the password")
        self.assertEqual(len(at.metric), 0, "no counters before the password")
        self.assertFalse(any(b.label == "Yes, reset the demo" for b in at.button), "no Reset demo before the password")
        self.assertEqual(at.text_input[0].label, "Staff password")

    def test_wrong_staff_password_stays_locked(self):
        at = self.locked_dashboard()
        with mock.patch.object(config, "STAFF_PASSWORD", "test-staff-pass"):
            at.text_input[0].input("guess")
            at.button[0].click().run()
        self.assertIn("Wrong password.", [e.value for e in at.error])
        self.assertEqual(len(at.metric), 0)

    def test_right_staff_password_opens_the_dashboard(self):
        TicketStore().create("Wylie Hall", "1450", "Sink leaking")
        at = self.locked_dashboard()
        with mock.patch.object(config, "STAFF_PASSWORD", "test-staff-pass"):
            at.text_input[0].input("test-staff-pass")
            at.button[0].click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(next(m.value for m in at.metric if m.label == "Open tickets"), "1")
        self.assertTrue(any(b.label == "Yes, reset the demo" for b in at.button))

    def test_no_staff_password_warns(self):
        """no STAFF_PASSWORD in .env -> the dashboard still opens (for practice) but says it's unlocked"""
        captions = " ".join(c.value for c in self.dashboard().caption)
        self.assertIn("No staff password set", captions)

    def test_resolved_on_the_dashboard_shows_in_the_chat(self):
        """on screen: the student reports a repair; staff resolve it; the chat shows the update with Yes/No"""
        at = self.chat("My heater isn't working", "Thompson 1214")
        code = TicketStore().all()[0]["code"]
        TicketStore().update_status(code, "Resolved", assigned_to="Heating & AC")
        at.run()                                       # what the 5-second check does by itself
        self.assertFalse(at.exception, at.exception)
        self.assertIn(f"Update: ticket {code}", self.last_reply(at))
        # (Streamlit shows a button's leading emoji as its icon, so the options read without it)
        self.assertEqual(list(at.pills[0].options), ["Yes, it's fixed", "No, still broken"])
        at.pills[0].set_value(agent.FIXED_YES).run()
        self.assertFalse(at.exception, at.exception)
        self.assertIn("closed", self.last_reply(at))
        self.assertEqual(TicketStore().get(code)["student_feedback"], "fixed")

    def test_assign_on_the_dashboard(self):
        """on screen: pick a team and a status, Save -> saved, and the table shows 'Assigned · Plumbing'"""
        TicketStore().create("Wylie Hall", "1450", "Sink leaking")
        board = self.dashboard()
        boxes = {s.label: s for s in board.selectbox}
        boxes["New status"].set_value("Assigned")
        boxes["Assigned to"].set_value("Plumbing")
        next(b for b in board.button if b.label == "Save").click().run()
        self.assertFalse(board.exception, board.exception)
        t = TicketStore().all()[0]
        self.assertEqual((t["status"], t["assigned_to"]), ("Assigned", "Plumbing"))
        self.assertTrue(any("is now Assigned (Plumbing)" in s.value for s in board.success))
        self.assertIn("Assigned · Plumbing", board.dataframe[0].value["Status"].tolist())

    def test_reopened_flag_on_the_dashboard(self):
        store = TicketStore()
        code = store.create("Wylie Hall", "1450", "Sink leaking")["code"]
        store.update_status(code, "Resolved")
        store.record_feedback(code, fixed=False)
        self.assertIn("🔁 Reopened", self.dashboard().dataframe[0].value["Flag"].tolist())

    def test_emergency_alert_on_the_dashboard(self):
        """(d) on screen: an emergency shows as a red alert at the top of the dashboard"""
        self.chat("There's smoke coming from an outlet in Buchanan 502")
        alerts = [e.value for e in self.dashboard().error]
        self.assertTrue(any("EMERGENCY" in a and "Buchanan Hall" in a for a in alerts), alerts)

    def test_tap_through_a_repair_on_screen(self):
        """(b) on screen: tap the buttons, type the room, and the ticket card appears"""
        at = self.chat()
        for tap in [agent.REPAIR, "💧 Leak / plumbing", "Wylie Hall"]:
            at.pills[0].set_value(tap).run()
            self.assertFalse(at.exception, at.exception)
        self.assertIn("room number in Wylie Hall", self.last_reply(at))
        at.chat_input[0].set_value("1408").run()
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
