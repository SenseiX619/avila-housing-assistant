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

WHY split it like this: the AI is great at understanding messy human language,
but Python is better at rules (search, look things up, save data). Letting the AI
understand and Python decide makes the agent predictable - important when a
university is trusting it with students.
"""

import json
import re
from dataclasses import dataclass, field

import config
import llm
from knowledge import Retriever
from tickets import TicketStore


@dataclass
class AgentReply:
    text: str
    kind: str = "answer"            # answer | smalltalk | ask_details | ticket | emergency | status | error
    sources: list = field(default_factory=list)
    ticket: dict | None = None


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
- Map buildings: "Thompson" -> "Thompson Hall", "Wylie" -> "Wylie Hall", "Buchanan" -> "Buchanan Hall",
  "Villa" -> "Villa Ventura".
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
- Be short and clear: 2-5 sentences. Your answer may be read aloud, so no tables or long lists.
- Cite the page like (Handbook p. 13) after the fact you used.
- If the excerpts don't answer the question, say you're not sure and suggest emailing
  {config.HOUSING_EMAIL}. Never invent policies, fees, or phone numbers.
- For anything dangerous, tell them to call Campus Safety at {config.CAMPUS_SAFETY_PHONE} or 911.
"""

TRANSLATE_PROMPT = """TASK: TRANSLATE
Translate the user's text into {language}. Keep it warm and natural.
Keep ticket IDs (like AV-1001), phone numbers, emails and building names exactly as they are.
Return only the translation."""

GREETING = ("Hi! I'm the Avila Housing Assistant. I can answer questions about living on campus, "
            "report a repair for you (just tell me what's broken), or check on a ticket you already reported.")

# Safety net: if the AI ever misses an emergency, these words still trigger the safety message.
EMERGENCY_PATTERN = re.compile(
    r"\b(fire|smoke|smoking outlet|gas smell|smell(s|ing)? (of )?gas|carbon monoxide|co alarm|flood(ing|ed)?|"
    r"water (is )?(pouring|everywhere|gushing)|sparks?|sparking|burning smell|unconscious|bleeding|someone (is )?hurt)\b",
    re.I)


REPAIR_PATTERN = re.compile(
    r"\b(broken|broke|not working|isn'?t working|doesn'?t work|won'?t (turn on|work|lock|close|open|flush|drain)|"
    r"stopped working|leak(s|ing)?|clogged|no (heat|hot water|water|power|internet|wifi)|"
    r"(heater|heating|ac|a/c|air conditioning|sink|toilet|shower|light|outlet|fridge|door|lock|window)\b.{0,25}"
    r"\b(broken|out|dead|stuck|loud|dripping|not))", re.I)

SHORT_HELLO = "Hey! What can I help you with - a housing question, or something in your room that needs fixing?"

LOG_FILE = config.DATA_DIR / "conversation_log.jsonl"


def log_turn(message: str, channel: str, analysis: dict, reply: "AgentReply") -> None:
    """Write each turn to data/conversation_log.jsonl - what the student said, what the AI understood,
    and what we did. Invaluable for debugging ("why did it say that?"). Demo data only - no real students."""
    try:
        from datetime import datetime
        row = {"time": datetime.now().isoformat(timespec="seconds"), "channel": channel, "message": message,
               "analysis": analysis, "reply_kind": reply.kind, "reply": reply.text[:300],
               "ticket": reply.ticket["code"] if reply.ticket else None}
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass                                  # logging must never break the chat


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


class HousingAgent:
    def __init__(self, retriever: Retriever, tickets: TicketStore):
        self.retriever = retriever
        self.tickets = tickets

    # ---------- public entry point ----------
    def respond(self, message: str, state: dict, channel: str = "text") -> AgentReply:
        """state = this student's conversation memory (kept by the app between messages)."""
        state.setdefault("history", [])
        state.setdefault("my_tickets", [])
        analysis = {}
        try:
            analysis = self._analyze(message, state)
            language = analysis.get("language") or "English"
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

            if analysis.get("cancel") and state.get("draft"):
                state["draft"] = None
                reply = AgentReply(self._say("No problem, I've cancelled that request.", language), kind="smalltalk")
            elif intent == "emergency" or EMERGENCY_PATTERN.search(message):
                reply = self._handle_emergency(message, details, language, state, channel)
            elif intent == "ticket_status":
                reply = self._handle_status(analysis.get("ticket_id") or message, language, state)
            elif intent == "maintenance" or (state.get("draft") and any(details.get(k) for k in ("building", "room"))):
                reply = self._handle_maintenance(message, details, language, state, channel)
            elif intent == "smalltalk":
                # Only the very first hello gets the full introduction.
                text = GREETING if len(state["history"]) < 2 else SHORT_HELLO
                reply = AgentReply(self._say(text, language), kind="smalltalk")
            else:
                reply = self._answer_question(message, analysis.get("search_query") or message, language, state)
        except Exception as err:
            reply = AgentReply(
                f"Sorry, I'm having trouble right now ({err}). For housing help, email "
                f"{config.HOUSING_EMAIL}. For anything urgent, call Campus Safety at {config.CAMPUS_SAFETY_PHONE}.",
                kind="error")

        state["history"] += [{"role": "user", "content": message},
                             {"role": "assistant", "content": reply.text}]
        state["history"] = state["history"][-12:]      # keep the last 6 exchanges
        log_turn(message, channel, analysis, reply)
        return reply

    # ---------- step 1: understand ----------
    def _analyze(self, message: str, state: dict) -> dict:
        recent = "\n".join(f"{m['role']}: {m['content']}" for m in state["history"][-6:]) or "(none)"
        draft = state.get("draft") or {}
        draft_text = "\n".join(f'"{k}": "{v}"' for k, v in draft.items() if v) or "(no ticket in progress)"
        return llm.chat_json([
            {"role": "system", "content": ANALYZE_PROMPT},
            {"role": "user", "content": f"CONVERSATION SO FAR:\n{recent}\n\n"
                                        f"CURRENT TICKET DRAFT:\n{draft_text}\n\nLATEST MESSAGE: {message}"},
        ])

    # ---------- step 2: act ----------
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
        ticket = self.tickets.create(
            building=draft["building"], room=draft["room"], issue=draft["issue"],
            category=draft.get("category") or "Other", urgency=draft.get("urgency") or "normal",
            language=language, channel=channel, student_message=draft.get("student_message", ""))
        state["draft"] = None
        state["my_tickets"].append(ticket["code"])
        return ticket

    def _handle_maintenance(self, message, details, language, state, channel) -> AgentReply:
        draft = self._merge_draft(details, state, message)
        missing = [k for k in ("issue", "building", "room") if not draft.get(k)]

        if missing:
            if "issue" in missing:
                ask = "Sorry to hear that. What exactly is broken or not working?"
            elif "building" in missing and "room" in missing:
                ask = (f"Got it: {draft['issue'].lower()}. Which residence hall and room number are you in? "
                       f"(Buchanan, Wylie, Thompson, or Villa Ventura)")
            elif "building" in missing:
                ask = "Which residence hall is that in? (Buchanan, Wylie, Thompson, or Villa Ventura)"
            else:
                ask = f"And what's your room number in {draft['building']}?"
            return AgentReply(self._say(ask, language), kind="ask_details")

        ticket = self._create_ticket(draft, language, channel, state)
        days = config.SLA_DAYS.get(ticket["urgency"], 14)
        window = "2 days" if days <= 2 else "2 weeks"
        text = (f"Done! I created ticket {ticket['code']}: {ticket['issue']} in {ticket['building']}, "
                f"room {ticket['room']} ({ticket['urgency']} priority). Housing staff can see it now. "
                f"The Housing Handbook says this kind of repair should be finished within {window}. "
                f"If it isn't, ask me about {ticket['code']} or email {config.HOUSING_EMAIL}.")
        if ticket["urgency"] == "urgent":
            text += (f" If it gets worse tonight or this weekend, contact the RA on duty or Campus Safety "
                     f"at {config.CAMPUS_SAFETY_PHONE}.")
        return AgentReply(self._say(text, language), kind="ticket", ticket=ticket)

    def _handle_emergency(self, message, details, language, state, channel) -> AgentReply:
        details = dict(details, urgency="emergency")
        if not details.get("issue"):
            details["issue"] = message[:120]
        draft = self._merge_draft(details, state, message)
        draft["urgency"] = "emergency"

        text = (f"This sounds like an emergency. If anyone is in danger, or there is fire or smoke, "
                f"get to safety and call 911 now. Then call Campus Safety at {config.CAMPUS_SAFETY_PHONE}.")
        ticket = None
        if draft.get("building") and draft.get("room"):
            ticket = self._create_ticket(draft, language, channel, state)
            text += f" I've also logged ticket {ticket['code']} as an EMERGENCY so housing staff see it right away."
        else:
            text += " Once you're safe, tell me your building and room number and I'll alert housing staff."
        return AgentReply(self._say(text, language), kind="emergency", ticket=ticket)

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
        text = (f"Ticket {code} ({ticket['issue']} in {ticket['building']}, room {ticket['room']}) "
                f"is currently: {ticket['status']}.")
        if ticket["overdue"]:
            text += (" It's past the Housing Handbook's repair deadline, so it's flagged as overdue on the staff "
                     f"dashboard. You can also email {config.HOUSING_EMAIL} or talk to your RA.")
        return AgentReply(self._say(text, language), kind="status", ticket=ticket)

    def _answer_question(self, message: str, query: str, language: str, state: dict) -> AgentReply:
        hits = self.retriever.search(query, k=4)
        if not hits:
            text = (f"I couldn't find that in the Housing Handbook. Please email {config.HOUSING_EMAIL} "
                    "and the Residential Life team will help you.")
            return AgentReply(self._say(text, language))

        excerpts = "\n\n".join(f"[{h['source']}] (page {h['page']})\n{h['text']}" for h in hits)
        messages = [{"role": "system", "content": ANSWER_PROMPT.format(language=language)}]
        messages += state["history"][-4:]               # lets follow-up questions make sense
        messages.append({"role": "user",
                         "content": f"STUDENT QUESTION: {message}\n\nHANDBOOK EXCERPTS:\n{excerpts}"})
        text = llm.chat(messages, temperature=0.2)
        sources = sorted({h["page"] for h in hits})
        return AgentReply(text, kind="answer", sources=sources)

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
