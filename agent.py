"""
agent.py - the brain of the Avila Housing Assistant.

Every student message goes through 2 steps:

  1. ANALYZE  - one AI call that returns JSON: what does the student want (intent),
                what language are they using, what English keywords to search the
                handbook with, and any repair details they mentioned.
  2. ACT      - plain Python decides what to do based on that intent:
                  question       -> search the handbook, answer with page citations
                  maintenance    -> collect building / room / issue, then create a ticket
                  emergency      -> safety instructions FIRST, then log an emergency ticket
                  ticket_status  -> look the ticket up
                  smalltalk      -> short friendly reply

Shortcut: a tapped quick-reply button (or a bare room number when we asked for one) skips the AI
and goes straight to ACT - instant, and it saves the free tier's tokens.

WHY split it like this: the AI is great at understanding messy human language,
but Python is better at rules (search, look things up, save data). Letting the AI
understand and Python decide makes the agent predictable - important when a
university is trusting it with students.
"""

import json
import re
import time
from dataclasses import dataclass, field

import config
import llm
import notify
from knowledge import Retriever
from tickets import URGENCY_RANK, TicketStore


@dataclass
class AgentReply:
    text: str
    kind: str = "answer"            # answer | smalltalk | ask_details | ticket | emergency | status | error
    sources: list = field(default_factory=list)
    ticket: dict | None = None
    options: list = field(default_factory=list)     # quick-reply buttons shown under the reply
    calls: list = field(default_factory=list)       # (label, phone link) buttons, e.g. Call Campus Safety


ANALYZE_PROMPT = f"""TASK: ANALYZE
You are the message analyzer for the Avila University Housing Assistant.
Read the conversation, the CURRENT TICKET DRAFT and the student's LATEST MESSAGE.
Reply with ONLY a JSON object in exactly this shape:

{{
  "language": "language of the latest message, e.g. English, Spanish, Urdu, Arabic",
  "intent": "question | maintenance | emergency | ticket_status | smalltalk",
  "search_query": "4-10 English keywords to search the housing handbook",
  "ticket": {{
    "building": "one of {config.BUILDINGS} or null",
    "room": "room number as a string, or null",
    "issue": "short English description of what is broken, or null",
    "category": "one of {config.CATEGORIES} or null",
    "urgency": "emergency | urgent | normal | null"
  }},
  "ticket_id": "a ticket number like AV-1003 if the student mentions one, else null",
  "cancel": false
}}

Intent rules:
- maintenance: something in their room or building is broken, leaking, not working, dirty, or has pests.
  ALSO use maintenance when a ticket draft is in progress and the student is answering with
  their building, room, or more details.
- emergency: fire, smoke, smell of gas, flooding or water pouring, sparks or burning smell,
  carbon monoxide alarm, someone hurt, or an outside door that cannot lock at night.
- ticket_status: asking about an existing ticket or repair they already reported.
- question: questions about campus living rules (guests, quiet hours, lockouts, laundry, parking,
  room changes, move-out, fees, meal plans, pets, etc.).
- smalltalk: greetings, thanks, or anything unrelated to housing.

Ticket rules:
- Only fill ticket fields the student actually said; use null for anything unknown. Never guess a room.
- "issue" is ONLY the problem in the LATEST MESSAGE if it reports one ("Also our toilet is clogged" -> "Toilet
  clogged"), never problems from earlier in the conversation.
- Map buildings: "Thompson" -> "Thompson Hall", "Wylie" -> "Wylie Hall", "Buchanan" -> "Buchanan Hall",
  "Villa" or "Villas" -> "Villa Ventura".
- urgency: emergency (see above); urgent = no heat, no hot water, broken lock, toilet not working,
  AC out in hot weather, active leak; normal = everything else.
- Set "cancel": true only if the student says to cancel or forget the request.
- Always write search_query and issue in English, even if the student writes in another language.
"""

ANSWER_PROMPT = f"""TASK: ANSWER
You are the Avila University Housing Assistant, a friendly helper for students living on campus.
Answer the student's question using ONLY the HANDBOOK EXCERPTS provided.

Rules:
- Reply in {{language}}.
- Be brief: 1-3 short sentences, the key fact first. No tables or long lists.
- Cite the page like (Handbook p. 13) after the fact you used.
- If the excerpts don't answer the question, say you're not sure and suggest emailing
  {config.HOUSING_EMAIL}. Never invent policies, fees, or phone numbers.
- For anything dangerous, tell them to call Campus Safety at {config.CAMPUS_SAFETY_PHONE} or 911.
"""

TRANSLATE_PROMPT = """TASK: TRANSLATE
Translate the user's text into {language}. Keep it warm and natural.
Keep ticket IDs (like AV-1001), phone numbers, emails and building names exactly as they are.
Return only the translation."""

GREETING = "Hi! I'm the Avila Housing Assistant. Tap a button, type, or use the 🎤 - I'll answer in writing."

# Safety net: if the AI ever misses an emergency, these words still trigger the safety message.
EMERGENCY_PATTERN = re.compile(
    r"\b(fire|flames?|smoke|smoking outlet|gas smell|gas leak(ing|s)?|leak(ing|s)? gas|smell(s|ing)? (of |like )?gas|"
    r"carbon monoxide|"
    r"co alarm|flood(ing|ed)?|water (is )?(pouring|everywhere|gushing)|water\b.{0,25}\b(through|from|out of) (the |my )?ceiling|"
    r"ceiling (is )?(leaking|caving in|collapsing|falling)|sparks?|sparking|burning smell|unconscious|bleeding|"
    r"someone (is )?hurt)\b",
    re.I)

# Who to call first. Housing Handbook, Safety & Emergency Policies (printed p. 23): "In Case of Emergency,
# contact Campus Safety at 816.985.6079 or 911. For extreme emergencies, call 911."
# So: a real fire, someone hurt, gas or carbon monoxide -> 911 first. Everything else (smoke, sparks,
# flooding, alarms) -> Campus Safety first. Both numbers are always shown.
CALL_911_PATTERN = re.compile(
    r"\b(on fire|fire (in|on|at|is|has|started|spreading)|(a|the) fire(?! (alarm|drill|extinguisher|door|hazard|safety|pit))|"
    r"flames?|burning down|unconscious|unresponsive|not breathing|can'?t breathe|collapsed|overdos(e|ed|ing)|seizure|"
    r"bleeding|someone (is |got )?(hurt|injured)|gas (smell|leak(ing|s)?)|leak(ing|s)? gas|smell(s|ing)? (of |like )?gas|"
    r"carbon monoxide|"
    r"co alarm)\b",
    re.I)

# A fire alarm sounding means leave first (handbook, Fire Drills: "all residents must evacuate the buildings").
ALARM_PATTERN = re.compile(
    r"\b((fire|smoke) alarms?\b.{0,20}|alarms? (is |are )?)(going off|went off|sounding|ringing)\b", re.I)

# Danger words that aren't happening right now: a rules question ("Can I smoke in my room?",
# "Are candles a fire hazard?") or a smoke detector beeping. Only counts when the AI agrees (see respond()).
NOT_AN_EMERGENCY = re.compile(
    r"^\W*(can|may|could|am|are) (i|we)\b|\b(allowed|permitted|policy|policies|rules?|hazards?|drills?|extinguishers?)\b|"
    r"\bsmoke (detector|alarm)s?\b.{0,30}\b(beep|chirp|batter)", re.I)

SAFETY_REMINDER = (f"If something is smoking, beeping or unsafe right now, call Campus Safety at "
                   f"{config.CAMPUS_SAFETY_PHONE}, or 911 if you see fire or someone is hurt.")

# Repairs that can't wait in a queue: no heat (Sensei's call, Nov 11) and the handbook's "severe" examples
# (printed p. 19: "a door cannot be locked, an overflowing sink"). They become EMERGENCY repairs: top of the
# staff board, emailed as high priority, and the student is told who to contact right now.
CANT_WAIT = re.compile(
    r"\b(no heat(ing)?|heat(ing)? (is )?(out|off|broken|not working|isn['’]?t working|stopped working)|"
    r"heater\b.{0,25}\b(broken|out|dead|stopped|not|isn['’]?t|won['’]?t|doesn['’]?t)|"
    r"(door|lock)\b.{0,25}\b(won['’]?t|can['’]?t|cannot|doesn['’]?t|does not|will not) (lock|close|latch)|"
    r"(lock|door lock) (is )?broken|overflow(ing|ed|s)?)\b", re.I)
CANT_WAIT_HELP = (f"In the evening or on a weekend, also contact the RA on duty or Campus Safety at "
                  f"{config.CAMPUS_SAFETY_PHONE} now (Housing Handbook p. 19).")

# Locked out right now: point to the people who can let them in. The handbook (printed p. 14) says housing staff
# give lockout assistance, but names no number, so we name the RA, as p. 19 does for after-hours problems.
LOCKOUT_PATTERN = re.compile(
    r"\b(locked out|lock(ed)? (myself|me) out|lost my (room )?(keys?|key ?card|card)|"
    r"can['’]?t get (in|into) my room)\b", re.I)
LOCKOUT_HELP = (f"Locked out right now? Housing staff help with lockouts (Housing Handbook p. 14): contact your RA, "
                f"or the RA on duty in the evening or on a weekend. If you feel unsafe, call Campus Safety at "
                f"{config.CAMPUS_SAFETY_PHONE}.")
NO_LOCATION = "Location unknown"               # building on an emergency ticket made before we know where


REPAIR_PATTERN = re.compile(
    r"\b(broken|broke|not working|isn['’]?t working|doesn['’]?t work|won['’]?t (turn on|work|lock|close|open|flush|drain)|"
    r"stopped working|leak(s|ing)?|clogged|no (heat|hot water|water|power|internet|wifi)|"
    r"(heater|heating|ac|a/c|air conditioning|sink|toilet|shower|light|outlet|fridge|door|lock|window)\b.{0,25}"
    r"\b(broken|out|dead|stuck|loud|dripping|not))", re.I)

SHORT_HELLO = "Hey! What can I help with?"

# ---------- Quick replies: buttons under each answer, so students can tap instead of typing ----------
REPAIR, QUESTION, MY_TICKET, EMERGENCY = "🔧 Report a repair", "❓ Ask a question", "🎫 Check my ticket", "🚨 Emergency"
MAIN_MENU = [REPAIR, QUESTION, MY_TICKET, EMERGENCY]
# Button -> (category, issue saved on the ticket, urgency). Urgency follows the ANALYZE_PROMPT rules.
ISSUE_BUTTONS = {
    "🔥 No heat": ("Heating/AC", "No heat", "emergency"),               # can't wait (see CANT_WAIT)
    "❄️ AC": ("Heating/AC", "AC not working", "urgent"),
    "💧 Leak / plumbing": ("Plumbing", "Leak or plumbing problem", "urgent"),
    "🚪 Door / lock": ("Door/Lock", "Door or lock problem", "urgent"),
    "⚡ Electrical": ("Electrical", "Electrical problem", "normal"),
    "🧊 Appliance": ("Appliance", "Appliance not working", "normal"),
    "🐜 Pests": ("Pest", "Pests", "normal"),
    "📶 Wi-Fi": ("Internet", "Wi-Fi or internet not working", "normal"),
}
SOMETHING_ELSE = "✏️ Something else"
FIXED_YES, FIXED_NO = "✅ Yes, it's fixed", "❌ No, still broken"     # "Did that fix it?" on a resolved ticket
EXAMPLE_QUESTIONS = ["Can my friend stay over?", "I'm locked out. Is there a fee?", "When are quiet hours?",
                     "Can I have a pet?"]
ROOM_ONLY = re.compile(r"^\W*(?:room|rm)?\W*([a-z]?\d{1,4}[a-z]?)\W*$", re.I)      # "1214", "Room 1214.", "1210B"
# "Wylie Hall, room 1450" - the button we offer when a room belongs to a different hall (also works typed)
HALL_AND_ROOM = re.compile(rf"^\W*({'|'.join(map(re.escape, config.BUILDINGS))}),? room (\w+)\W*$", re.I)
CALL_SAFETY = ("📞 Call Campus Safety", "tel:+1" + re.sub(r"\D", "", config.CAMPUS_SAFETY_PHONE))
CALL_911 = ("📞 Call 911", "tel:911")

LOG_FILE = config.DATA_DIR / "conversation_log.jsonl"


def write_log(row: dict) -> None:
    """Add one line to data/conversation_log.jsonl. Demo data only - no real students."""
    try:
        from datetime import datetime
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), **row},
                               ensure_ascii=False) + "\n")
    except Exception:
        pass                                  # logging must never break the chat


def log_turn(message: str, channel: str, analysis: dict, reply: "AgentReply") -> None:
    """Log each turn - what the student said, what the AI understood, and what we did.
    Invaluable for debugging ("why did it say that?")."""
    write_log({"channel": channel, "message": message, "analysis": analysis, "reply_kind": reply.kind,
               "reply": reply.text[:300], "ticket": reply.ticket["code"] if reply.ticket else None})


INTENTS = {"question", "maintenance", "emergency", "ticket_status", "smalltalk"}
TICKET_FIELDS = ("building", "room", "issue", "category", "urgency")


def _tidy(analysis: dict) -> dict:
    """Valid JSON can still have the wrong shape ("ticket": "none", "intent": "Emergency!").
    Coerce it into the shape ACT expects, so a sloppy answer can't crash the agent."""
    def clean(value):
        text = str(value).strip() if value is not None else ""
        return None if text.lower() in ("", "null", "none", "unknown", "n/a") else text

    ticket = analysis.get("ticket") if isinstance(analysis.get("ticket"), dict) else {}
    intent = (clean(analysis.get("intent")) or "question").lower().strip("!. ")
    return {"language": clean(analysis.get("language")) or "English",
            "intent": intent if intent in INTENTS else "question",
            "search_query": clean(analysis.get("search_query")),
            "ticket": {k: clean(ticket.get(k)) for k in TICKET_FIELDS},
            "ticket_id": clean(analysis.get("ticket_id")),
            "cancel": analysis.get("cancel") is True}


def normalize_building(value: str | None) -> str | None:
    if not value:
        return None
    v = value.lower()
    for building in config.BUILDINGS:
        if building.split()[0].lower() in v:
            return building
    if "ventura" in v:
        return "Villa Ventura"
    return value.strip().title()


# ---------- Rooms: which numbers belong to which hall (config.ROOM_RANGES) ----------
ROOM_GUIDE = ", ".join(f"{b.split()[0]} {low}-{high}" for b, (low, high) in config.ROOM_RANGES.items())


def clean_room(room) -> str:
    """'Room 1450' / '14-50' (how speech writes "fourteen fifty") -> '1450'; '1210b' -> '1210B'."""
    return re.sub(r"[^0-9A-Z]", "", re.sub(r"(?i)\b(room|rm)\b", "", str(room or "")).upper())


def hall_for_room(room) -> str | None:
    """The hall a room number belongs to ('1450' -> Wylie Hall), or None if it isn't a housing room."""
    match = re.fullmatch(r"(\d{3,4})[A-Z]?", clean_room(room))
    if not match:
        return None
    number = int(match.group(1))
    return next((b for b, (low, high) in config.ROOM_RANGES.items() if low <= number <= high), None)


def room_range(building: str) -> str:
    low, high = config.ROOM_RANGES[building]
    return f"{low}-{high}"


class HousingAgent:
    def __init__(self, retriever: Retriever, tickets: TicketStore):
        self.retriever = retriever
        self.tickets = tickets

    # ---------- public entry point ----------
    def respond(self, message: str, state: dict, channel: str = "text") -> AgentReply:
        """state = this student's conversation memory (kept by the app between messages)."""
        state.setdefault("history", [])
        state.setdefault("my_tickets", [])
        state.setdefault("seen", {})                  # tickets this chat watches -> the status we last told them
        analysis = {}
        try:
            reply = self._quick_reply(message, state, channel)
            if reply:
                analysis = {"quick_reply": True}                     # a tapped button: no AI call needed
            else:
                analysis = self._analyze(message, state)
                reply = self._act(message, analysis, state, channel)
        except Exception as err:
            # The technical details go to the log for us, not on the student's screen.
            write_log({"event": "error", "message": message, "error": f"{type(err).__name__}: {err}"[:500]})
            reply = AgentReply(
                f"Sorry, I'm having trouble right now. For housing help, email {config.HOUSING_EMAIL}. "
                f"For anything urgent, call Campus Safety at {config.CAMPUS_SAFETY_PHONE}.",
                kind="error", options=MAIN_MENU)

        state["history"] += [{"role": "user", "content": message},
                             {"role": "assistant", "content": reply.text}]
        state["history"] = state["history"][-12:]      # keep the last 6 exchanges
        log_turn(message, channel, analysis, reply)
        return reply

    # ---------- shortcut: tapped buttons ----------
    def _quick_reply(self, message: str, state: dict, channel: str) -> AgentReply | None:
        """A tapped button (or a bare room number we asked for) needs no AI, so it's handled here,
        instantly. Anything else returns None and goes through ANALYZE + ACT."""
        language = state.get("language", "English")
        draft = state.get("draft") or {}
        if message == REPAIR:
            return AgentReply(self._say("What's the problem? Tap one, or tell me in your own words.", language),
                              kind="ask_details", options=[*ISSUE_BUTTONS, SOMETHING_ELSE])
        if message in ISSUE_BUTTONS:
            category, issue, urgency = ISSUE_BUTTONS[message]
            return self._handle_maintenance(issue, {"issue": issue, "category": category, "urgency": urgency},
                                            language, state, channel)
        if message == SOMETHING_ELSE:
            return AgentReply(self._say("Tell me what's broken - type it or tap the mic.", language),
                              kind="ask_details")
        if message == QUESTION:
            return AgentReply(self._say("Ask me anything about living on campus, or tap one:", language),
                              kind="ask_details", options=EXAMPLE_QUESTIONS)
        if message == MY_TICKET:
            return self._handle_status("", language, state)
        if message in (FIXED_YES, FIXED_NO) and state.get("confirm_ticket"):
            return self._handle_feedback(message == FIXED_YES, language, state)
        if message == EMERGENCY:
            text = (f"If there's a fire or someone is hurt, call 911 now. For smoke, sparks, flooding or anything "
                    f"unsafe, call Campus Safety at {config.CAMPUS_SAFETY_PHONE}. Then tell me what's happening "
                    f"and where, and I'll alert housing staff.")
            return AgentReply(self._say(text, language), kind="emergency", calls=[CALL_911, CALL_SAFETY])
        if draft.get("issue") and not draft.get("building") and message in config.BUILDINGS:
            return self._handle_maintenance(message, {"building": message}, language, state, channel)
        hall_room = HALL_AND_ROOM.match(message)
        if hall_room and draft.get("issue"):
            return self._handle_maintenance(message, {"building": hall_room.group(1), "room": hall_room.group(2)},
                                            language, state, channel)
        room = ROOM_ONLY.match(message)
        if room and draft.get("issue") and not draft.get("room"):       # no hall yet? the number tells us
            return self._handle_maintenance(message, {"room": room.group(1).upper()}, language, state, channel)
        return None

    # ---------- step 1: understand ----------
    def _analyze(self, message: str, state: dict) -> dict:
        recent = "\n".join(f"{m['role']}: {m['content']}" for m in state["history"][-6:]) or "(none)"
        draft = state.get("draft") or {}
        draft_text = "\n".join(f'"{k}": "{v}"' for k, v in draft.items() if v) or "(no ticket in progress)"
        problems = []
        try:
            analysis = _tidy(llm.chat_json([
                {"role": "system", "content": ANALYZE_PROMPT},
                {"role": "user", "content": f"CONVERSATION SO FAR:\n{recent}\n\n"
                                            f"CURRENT TICKET DRAFT:\n{draft_text}\n\nLATEST MESSAGE: {message}"},
            ], problems))
        except Exception as err:                 # broken JSON twice, or the AI can't be reached at all
            problems.append(f"{type(err).__name__}: {err}"[:300])
            analysis = self._fallback_analysis(message, state)
        if problems:                             # logged even when the retry worked, so we see how often
            write_log({"event": "analyze_problem", "message": message, "problems": problems,
                       "used_fallback": bool(analysis.get("fallback"))})
        return analysis

    def _fallback_analysis(self, message: str, state: dict) -> dict:
        """A rough, rule-based reading for when the AI can't help: the safety nets, repairs and
        ticket lookups keep working (only free-form understanding and other languages suffer)."""
        text = message.lower()
        draft_open = bool((state.get("draft") or {}).get("issue"))
        building = next((b for b in config.BUILDINGS if b.split()[0].lower() in text), None)
        ticket_id = re.search(r"\bav-?\s?(\d{4})\b", text)
        room = re.search(r"\b([a-z]?\d{2,4}[a-z]?)\b", re.sub(r"\bav-?\s?\d{4}\b", " ", text))
        if ticket_id or "status" in text or "my ticket" in text:
            intent = "ticket_status"
        elif EMERGENCY_PATTERN.search(message):
            intent = "emergency"
        elif REPAIR_PATTERN.search(message) or (draft_open and (building or room)):
            intent = "maintenance"
        else:
            intent = "question"
        new_problem = intent in ("maintenance", "emergency") and not draft_open
        return {"fallback": True, "language": state.get("language", "English"), "intent": intent,
                "search_query": message, "ticket_id": f"AV-{ticket_id.group(1)}" if ticket_id else None,
                "cancel": False, "ticket": {"building": building, "room": room.group(1).upper() if room else None,
                                            "issue": message.strip()[:120] if new_problem else None}}

    # ---------- step 2: act ----------
    def _act(self, message: str, analysis: dict, state: dict, channel: str) -> AgentReply:
        """Plain Python decides what to do with what the AI understood."""
        language = analysis.get("language") or "English"
        state["language"] = language                             # tapped buttons answer in it too
        intent = str(analysis.get("intent") or "question").strip().lower()
        details = analysis.get("ticket") or {}

        # Safety net #2: the AI sometimes files a repair under "smalltalk" or "question".
        # If the message clearly describes something broken, treat it as a repair anyway.
        if intent in ("smalltalk", "question") and REPAIR_PATTERN.search(message):
            intent = "maintenance"
            if not details.get("issue"):
                details = dict(details, issue=message.strip()[:120])
        # Long messages are never just "hi" - answer them from the handbook instead.
        if intent == "smalltalk" and len(message.split()) > 6:
            intent = "question"
        # Locked out: not a repair. Who can let them in comes first (LOCKOUT_HELP), then any handbook answer (fees).
        lockout = bool(LOCKOUT_PATTERN.search(message)) and not REPAIR_PATTERN.search(message)
        if lockout and intent in ("smalltalk", "maintenance") and not state.get("draft"):
            intent = "question"

        # Safety net #1: danger words (in the message, or in the AI's English summary of it).
        # The AI alone can't talk them down. Only when the AI AND the wording both say nothing is
        # happening right now ("Can I smoke in my room?", "my smoke detector keeps beeping") do we
        # reply normally - after a one-line safety reminder with both numbers.
        words = f"{message} {details.get('issue') or ''}"
        danger = bool(EMERGENCY_PATTERN.search(words))
        reminder = danger and intent != "emergency" and bool(NOT_AN_EMERGENCY.search(words))
        if reminder:
            danger = False
            if details.get("urgency") in (None, "", "null", "normal"):
                details = dict(details, urgency="urgent")          # e.g. a beeping smoke detector: 2-day fix
        # Repairs that can't wait (no heat, a door that won't lock, an overflow): an EMERGENCY repair, not the queue.
        if not danger and CANT_WAIT.search(words) and (intent == "maintenance" or state.get("draft")):
            details = dict(details, urgency="emergency")

        if analysis.get("cancel") and state.get("draft"):
            state["draft"] = None
            reply = AgentReply(self._say("No problem, I've cancelled that request.", language), kind="smalltalk",
                               options=MAIN_MENU)
        elif intent == "emergency" or danger:
            reply = self._handle_emergency(message, details, language, state, channel)
        elif intent == "ticket_status":
            reply = self._handle_status(analysis.get("ticket_id") or message, language, state)
        elif intent == "maintenance" or (state.get("draft") and any(details.get(k) for k in ("building", "room"))):
            reply = self._handle_maintenance(message, details, language, state, channel)
        elif intent == "smalltalk":
            # Only the very first hello gets the full introduction.
            text = GREETING if len(state["history"]) < 2 else SHORT_HELLO
            reply = AgentReply(self._say(text, language), kind="smalltalk", options=MAIN_MENU)
        else:
            try:
                reply = self._answer_question(message, analysis.get("search_query") or message, language, state)
            except Exception:
                if not lockout:
                    raise
                reply = AgentReply("", kind="answer", options=MAIN_MENU)   # AI down: the lockout help still shows
        if reminder:
            reply.text = f"{self._say(SAFETY_REMINDER, language)}\n\n{reply.text}"
        if lockout and reply.kind != "emergency":
            reply.text = f"{self._say(LOCKOUT_HELP, language)}\n\n{reply.text}".strip()
            reply.calls = reply.calls or [CALL_SAFETY]
        return reply

    def _merge_draft(self, details: dict, state: dict, message: str) -> dict:
        draft = state.get("draft") or {"student_message": message}
        for key in ("building", "room", "issue", "category", "urgency"):
            value = details.get(key)
            if value and str(value).lower() not in ("null", "none", "unknown"):
                if key == "urgency" and draft.get("urgency") == "emergency":
                    continue                       # never downgrade an emergency
                draft[key] = normalize_building(value) if key == "building" else str(value).strip()
        state["draft"] = draft
        return draft

    def _create_ticket(self, draft: dict, language: str, channel: str, state: dict) -> dict:
        same = self.tickets.find_open_duplicate(draft["building"], draft["room"], draft["issue"], draft.get("category"))
        if same:
            return self._add_to_existing(same, draft, state)
        ticket = self.tickets.create(
            building=draft["building"], room=draft["room"], issue=draft["issue"],
            category=draft.get("category") or "Other", urgency=draft.get("urgency") or "normal",
            language=language, channel=channel, student_message=draft.get("student_message", ""))
        state["draft"] = None
        state["my_tickets"].append(ticket["code"])
        state["seen"][ticket["code"]] = ticket["status"]          # watch it: staff updates show up in this chat
        state["last_ticket"] = {**ticket, "at": time.time()}
        self._email(ticket)
        return ticket

    @staticmethod
    def _email(ticket: dict, update: str = "") -> None:
        """Email the ticket to housing staff (in the background; the result goes to the conversation log)."""
        notify.send_ticket_email(ticket, update=update, on_done=lambda result: write_log(
            {"event": "ticket_email", "ticket": ticket["code"], "update": update or None,
             "to": config.TICKET_EMAIL_TO, "result": result}))

    def _add_to_existing(self, ticket: dict, draft: dict, state: dict) -> dict:
        """The same problem is already an open ticket in that room: no second ticket. A roommate's report is
        counted on it (staff get one "Reported again" email); the same student repeating themselves isn't,
        unless it got more serious (then the ticket's urgency goes up and staff are told).
        Returns the ticket with "duplicate": "mine" or "other", for the reply."""
        state["draft"] = None
        mine = ticket["code"] in state["my_tickets"]
        urgency = draft.get("urgency") or "normal"
        more_serious = URGENCY_RANK.get(urgency, 0) > URGENCY_RANK.get(ticket["urgency"], 0)
        if not mine or more_serious:
            ticket = self.tickets.add_report(ticket["code"], urgency, draft.get("student_message", ""))
            note = "now an EMERGENCY" if more_serious and urgency == "emergency" else "more urgent" if more_serious else ""
            self._email(ticket, update=f"Reported again ({ticket['reports']} reports){', ' + note if note else ''}")
        if not mine:
            state["my_tickets"].append(ticket["code"])
        state["seen"][ticket["code"]] = ticket["status"]          # watch it like their own
        return {**ticket, "duplicate": "mine" if mine else "other"}

    def _alert_without_location(self, draft: dict, language: str, channel: str, state: dict) -> dict:
        """An emergency, but no building or room yet: save and email the ticket NOW with what we know, so staff
        hear about it even if the student never answers. One alert per conversation; the place is added later."""
        if state.get("emergency_ticket"):
            return self.tickets.get(state["emergency_ticket"])
        ticket = self.tickets.create(
            building=draft.get("building") or NO_LOCATION, room=draft.get("room") or "?", issue=draft["issue"],
            category=draft.get("category") or "Other", urgency="emergency", language=language, channel=channel,
            student_message=draft.get("student_message", ""))
        state["emergency_ticket"] = ticket["code"]
        state["my_tickets"].append(ticket["code"])
        state["seen"][ticket["code"]] = ticket["status"]
        self._email(ticket)
        return ticket

    def _add_location(self, draft: dict, state: dict) -> dict:
        """The student told us where the emergency is: put it on the ticket staff already have."""
        code = state.pop("emergency_ticket")
        state["draft"] = None
        ticket = self.tickets.update_location(code, draft["building"], draft["room"])
        state["last_ticket"] = {**ticket, "at": time.time()}
        self._email(ticket, update="Location added")
        return ticket

    def _correct_room(self, draft: dict, state: dict) -> dict | None:
        """Same problem, same hall, a few minutes later, but a different room? Then the student is correcting
        the room (speech often mishears numbers): fix the ticket they just made instead of opening a second one."""
        last = state.get("last_ticket")
        if not (last and time.time() - last["at"] < 600 and draft["room"] != last["room"]
                and draft["building"] == last["building"] and draft["issue"].lower() == last["issue"].lower()):
            return None
        state["draft"] = None
        ticket = self.tickets.update_room(last["code"], draft["room"])
        state["last_ticket"] = {**ticket, "at": time.time()}
        self._email(ticket, update="Room corrected")
        return ticket

    @staticmethod
    def _check_location(draft: dict) -> tuple[str, list]:
        """Routing: the room must belong to the hall (config.ROOM_RANGES). A room on its own fills in the
        hall. Anything that doesn't fit is taken off the draft, so we ask for it again.
        Returns (what was wrong, in one sentence, or "") and any extra buttons to offer."""
        building, room = draft.get("building"), draft.get("room")
        if building and building not in config.ROOM_RANGES:
            draft["building"] = None
            return f"I don't know a hall called {building}.", []
        if not room:
            return "", []
        hall = hall_for_room(room)
        if hall is None:
            draft["room"] = None
            rooms = f"{building} rooms are {room_range(building)}" if building else f"rooms are {ROOM_GUIDE}"
            return f"Room {room} isn't an Avila housing room ({rooms}).", []
        draft["room"] = clean_room(room)
        if not building:
            draft["building"] = hall
        elif hall != building:
            draft["room"] = None
            return (f"Room {clean_room(room)} is a {hall} number, but {building} rooms are {room_range(building)}. "
                    f"In {hall}? Tap below.", [f"{hall}, room {clean_room(room)}"])
        return "", []

    def _handle_maintenance(self, message, details, language, state, channel) -> AgentReply:
        draft = self._merge_draft(details, state, message)
        problem, extra_options = self._check_location(draft)
        missing = [k for k in ("issue", "building", "room") if not draft.get(k)]

        if missing:
            options = []
            if "issue" in missing:
                ask = "Sorry to hear that. What's the problem? Tap one, or tell me in your own words."
                options = [*ISSUE_BUTTONS, SOMETHING_ELSE]
            elif "building" in missing:
                issue = draft["issue"][:1].lower() + draft["issue"][1:]
                ask = f"Got it: {issue}. Where? Tap your hall" + (
                    ', or say it like "Thompson 1214".' if "room" in missing else ".")
                options = config.BUILDINGS
            elif problem:                                   # the problem already gave the hall's room range
                ask = "Otherwise, what's your room number?" if extra_options else "What's your room number?"
                options = extra_options
            else:
                ask = f"What's your room number in {draft['building']} ({room_range(draft['building'])})?"
            if problem:
                ask = f"{problem} {ask}"
            return AgentReply(self._say(ask, language), kind="ask_details", options=options)

        if state.get("emergency_ticket"):           # staff were already alerted; this is where it's happening
            ticket = self._add_location(draft, state)
            text = (f"Thanks! Emergency ticket {ticket['code']} now says {ticket['building']} room {ticket['room']}, "
                    f"so housing staff can find you.")
            return AgentReply(self._say(text, language), kind="ticket", ticket=ticket, calls=[CALL_SAFETY, CALL_911],
                              options=[MY_TICKET, REPAIR, QUESTION])

        corrected = self._correct_room(draft, state)
        if corrected:
            text = (f"Fixed! Ticket {corrected['code']} is now for room {corrected['room']} in "
                    f"{corrected['building']}, and housing staff have the update.")
            return AgentReply(self._say(text, language), kind="ticket", ticket=corrected,
                              options=[MY_TICKET, REPAIR, QUESTION])

        ticket = self._create_ticket(draft, language, channel, state)
        if ticket.get("duplicate"):                 # already an open ticket for this problem in this room
            where = f"{ticket['issue']}, {ticket['building']} {ticket['room']}"
            status = ticket["status"] + (f" ({ticket['assigned_to']})" if ticket["assigned_to"] else "")
            if ticket["duplicate"] == "mine":
                text = (f"You already reported this: ticket {ticket['code']} ({where}) is {status}. "
                        f"No need to report it again; I'll tell you here when it changes.")
            else:
                text = (f"This was already reported: ticket {ticket['code']} ({where}) is {status}. "
                        f"I've added your report, so housing staff know it affects you too.")
            return AgentReply(self._say(text, language), kind="ticket", ticket=ticket,
                              calls=[CALL_SAFETY] if ticket["urgency"] == "emergency" else [],
                              options=[MY_TICKET, REPAIR, QUESTION])
        if ticket["urgency"] == "emergency":        # can't wait (no heat, door won't lock...): don't queue it
            text = (f"Done! Ticket {ticket['code']} is flagged as an EMERGENCY repair, so housing staff see it first. "
                    f"{CANT_WAIT_HELP}")
            return AgentReply(self._say(text, language), kind="ticket", ticket=ticket, calls=[CALL_SAFETY],
                              options=[MY_TICKET, REPAIR, QUESTION])
        window = "2 days" if config.SLA_DAYS.get(ticket["urgency"], 14) <= 2 else "2 weeks"
        text = (f"Done! Ticket {ticket['code']} is in, and housing staff can see it now. "
                f"The Housing Handbook says it should be fixed within {window}.")
        if ticket["urgency"] == "urgent":
            text += f" If it gets worse after hours, call Campus Safety at {config.CAMPUS_SAFETY_PHONE}."
        return AgentReply(self._say(text, language), kind="ticket", ticket=ticket,
                          options=[MY_TICKET, REPAIR, QUESTION])

    def _handle_emergency(self, message, details, language, state, channel) -> AgentReply:
        details = dict(details, urgency="emergency")
        if not details.get("issue"):
            details["issue"] = message[:120]
        draft = self._merge_draft(details, state, message)
        draft["urgency"] = "emergency"
        if not draft.get("building") and hall_for_room(draft.get("room")):
            draft["building"] = hall_for_room(draft["room"])     # "smoke in 1450" -> Wylie Hall
        # Room numbers aren't checked any further here: an emergency is never held back for a wrong room.

        words = f"{message} {details['issue']}"
        calls = [CALL_SAFETY, CALL_911]
        if CALL_911_PATTERN.search(words):                                 # real fire, someone hurt, gas
            text = (f"This sounds like an emergency. Get to safety and call 911 now, "
                    f"then call Campus Safety at {config.CAMPUS_SAFETY_PHONE}.")
            calls = [CALL_911, CALL_SAFETY]
        elif ALARM_PATTERN.search(words):                                  # fire alarm sounding: leave first
            text = (f"When the fire alarm sounds, everyone must leave the building. Go now, then call Campus "
                    f"Safety at {config.CAMPUS_SAFETY_PHONE}. If you see flames or anyone is hurt, call 911.")
        else:                                                              # smoke, sparks, flooding, beeping
            text = (f"This could be dangerous. Call Campus Safety now at {config.CAMPUS_SAFETY_PHONE} so they can "
                    f"check it right away. If you see flames or anyone is hurt, get to safety and call 911.")
        if draft.get("building") and draft.get("room"):
            if state.get("emergency_ticket"):
                ticket = self._add_location(draft, state)
                text += (f" I've added {ticket['building']} room {ticket['room']} to emergency ticket {ticket['code']} "
                         f"for housing staff.")
            else:
                ticket = self._create_ticket(draft, language, channel, state)
                if ticket.get("duplicate"):
                    text += (f" Housing staff already have this as ticket {ticket['code']}; I've added your report "
                             f"and it's marked EMERGENCY.")
                else:
                    text += f" I've also logged ticket {ticket['code']} as an EMERGENCY so housing staff see it right away."
        else:
            # Don't wait for the room: alert staff now with what we know. The place is added when we hear it.
            ticket = self._alert_without_location(draft, language, channel, state)
            text += (f" I've alerted housing staff (emergency ticket {ticket['code']}). Once you're safe, tell me your "
                     f"building and room number so they can find you.")
        return AgentReply(self._say(text, language), kind="emergency", ticket=ticket, calls=calls,
                          options=[] if draft.get("building") else config.BUILDINGS)

    def _handle_status(self, ticket_ref: str, language: str, state: dict) -> AgentReply:
        match = re.search(r"AV-?\s?(\d{4})", ticket_ref or "", re.I)
        code = f"AV-{match.group(1)}" if match else (state["my_tickets"][-1] if state["my_tickets"] else None)
        if not code:
            return AgentReply(self._say("Sure! What's your ticket number? It looks like AV-1001.", language),
                              kind="ask_details")
        ticket = self.tickets.get(code)
        if not ticket:
            return AgentReply(self._say(f"I couldn't find ticket {code}. Can you double-check the number?",
                                        language), kind="status")
        state["seen"][code] = ticket["status"]                    # watch it: staff updates show up in this chat
        return self._status_reply(ticket, language, state)

    def _status_reply(self, ticket: dict, language: str, state: dict, update: bool = False) -> AgentReply:
        """Where a ticket stands, in plain words. A resolved ticket asks "Did that fix it?"."""
        status, who = ticket["status"], ticket.get("assigned_to")
        name = f"{ticket['code']} ({ticket['issue']}, {ticket['building']} {ticket['room']})"
        text = f"📬 Update: ticket {name} is now " if update else f"Ticket {name} is "
        text += {"Submitted": "Submitted. Housing staff have it and will assign it soon.",
                 "Assigned": f"Assigned{' to ' + who if who else ''}.",
                 "In Progress": f"In Progress{' (' + who + ' is on it)' if who else ''}.",
                 "Resolved": "Resolved: housing staff marked it fixed."}.get(status, f"{status}.")
        options = [REPAIR, QUESTION]
        if status == "Resolved" and ticket.get("student_feedback") == "fixed":
            text += " You confirmed it's fixed. Thanks!"
        elif status == "Resolved":
            text += " Did that fix it?"
            options = [FIXED_YES, FIXED_NO]
            state["confirm_ticket"] = ticket["code"]
        elif ticket.get("student_feedback") == "not fixed":
            text += " It was reopened because it still wasn't fixed."
        if ticket["overdue"]:
            text += (" It's past the Housing Handbook's repair deadline, so it's flagged overdue for staff. "
                     f"You can also email {config.HOUSING_EMAIL} or talk to your RA.")
        return AgentReply(self._say(text, language), kind="status", ticket=ticket, options=options)

    def _handle_feedback(self, fixed: bool, language: str, state: dict) -> AgentReply:
        """The student's answer to "Did that fix it?": yes closes the loop, no reopens the ticket for staff."""
        code = state.pop("confirm_ticket")
        ticket = self.tickets.record_feedback(code, fixed)
        if not ticket:
            return AgentReply(self._say(f"I couldn't find ticket {code} any more.", language), kind="status",
                              options=MAIN_MENU)
        state["seen"][code] = ticket["status"]               # our own change: no "Update" message about it
        if fixed:
            text = f"Great, thanks for confirming! Ticket {code} is closed."
        else:
            text = f"Sorry it's still not fixed. I've reopened ticket {code} and told housing staff."
            self._email(ticket, update="REOPENED - the student says it's still not fixed")
        return AgentReply(self._say(text, language), kind="status", ticket=ticket, options=[MY_TICKET, REPAIR, QUESTION])

    def ticket_updates(self, state: dict) -> list[AgentReply]:
        """Did staff change a ticket this student reported or asked about? Then say so in the chat - the loop
        back to the student. The chat page checks every few seconds; no AI call (unless we translate)."""
        replies = []
        for code, last_told in list(state.get("seen", {}).items()):
            ticket = self.tickets.get(code)
            if ticket and ticket["status"] != last_told:
                state["seen"][code] = ticket["status"]
                reply = self._status_reply(ticket, state.get("language", "English"), state, update=True)
                state.setdefault("history", []).append({"role": "assistant", "content": reply.text})
                write_log({"event": "status_update_shown", "ticket": code, "status": ticket["status"]})
                replies.append(reply)
        return replies

    def _answer_question(self, message: str, query: str, language: str, state: dict) -> AgentReply:
        hits = self.retriever.search(query, k=4)
        if not hits:
            text = (f"I couldn't find that in the Housing Handbook. Please email {config.HOUSING_EMAIL} "
                    "and the Residential Life team will help you.")
            return AgentReply(self._say(text, language), options=[QUESTION, REPAIR, MY_TICKET])

        excerpts = "\n\n".join(f"[{h['source']}] (page {h['page']})\n{h['text']}" for h in hits)
        messages = [{"role": "system", "content": ANSWER_PROMPT.format(language=language)}]
        messages += state["history"][-4:]               # lets follow-up questions make sense
        messages.append({"role": "user",
                         "content": f"STUDENT QUESTION: {message}\n\nHANDBOOK EXCERPTS:\n{excerpts}"})
        text = llm.chat(messages, temperature=0.2)
        # List the pages the answer actually cites; if it cites none, every page we gave it.
        cited = {int(p) for p in re.findall(r"p\.\s?(\d+)", text)} & {h["page"] for h in hits}
        sources = sorted(cited or {h["page"] for h in hits})
        return AgentReply(text, kind="answer", sources=sources, options=[QUESTION, REPAIR, MY_TICKET])

    # ---------- helpers ----------
    def _say(self, english_text: str, language: str) -> str:
        """Our fixed messages are written in English; translate them when the student isn't."""
        if not language or language.lower().startswith("english"):
            return english_text
        try:
            return llm.chat([
                {"role": "system", "content": TRANSLATE_PROMPT.format(language=language)},
                {"role": "user", "content": english_text},
            ], temperature=0.1)
        except Exception:
            return english_text
