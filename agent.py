"""
agent.py - the brain of the Avila Housing Assistant.

Every student message goes through 2 steps:

  1. ANALYZE  - one AI call that returns JSON: what does the student want (intent),
                what language are they using, and what English keywords should we
                search the handbook with.
  2. ACT      - plain Python decides what to do based on that intent.

WHY split it like this: the AI is great at understanding messy human language,
but Python is better at rules (search, look things up, save data). Letting the AI
understand and Python decide makes the agent predictable - important when a
university is trusting it with students.
"""

from dataclasses import dataclass, field

import config
import llm
from knowledge import Retriever


@dataclass
class AgentReply:
    text: str
    kind: str = "answer"                      # answer | smalltalk | error
    sources: list = field(default_factory=list)


ANALYZE_PROMPT = f"""TASK: ANALYZE
You are the message analyzer for the Avila University Housing Assistant.
Read the conversation and the student's LATEST MESSAGE, then reply with ONLY a JSON object:

{{
  "language": "the language of the latest message, e.g. English, Spanish, Urdu",
  "intent": "question | smalltalk",
  "search_query": "4-10 English keywords to search the housing handbook, using handbook-style words"
}}

Intent rules:
- question: any question about living on campus (rules, guests, quiet hours, lockouts, laundry,
  parking, room changes, move-out, fees, meal plans, etc.)
- smalltalk: greetings, thanks, or messages unrelated to housing.

Always write search_query in English even if the student writes in another language.
Use follow-up context: if the student says "what about weekends?" after asking about quiet hours,
search_query should be "quiet hours weekend Friday Saturday".
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

GREETING = ("Hi! I'm the Avila Housing Assistant. Ask me anything about living on campus - "
            "guests, quiet hours, lockouts, laundry, move-out - and I'll answer from the Housing Handbook.")


class HousingAgent:
    def __init__(self, retriever: Retriever):
        self.retriever = retriever

    # ---------- public entry point ----------
    def respond(self, message: str, state: dict) -> AgentReply:
        """state = this student's conversation memory (kept by the app between messages)."""
        state.setdefault("history", [])
        try:
            analysis = self._analyze(message, state)
            language = analysis.get("language") or "English"
            intent = analysis.get("intent", "question")

            if intent == "smalltalk":
                reply = AgentReply(self._say(GREETING, language), kind="smalltalk")
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
        return reply

    # ---------- step 1: understand ----------
    def _analyze(self, message: str, state: dict) -> dict:
        recent = "\n".join(f"{m['role']}: {m['content']}" for m in state["history"][-6:]) or "(none)"
        return llm.chat_json([
            {"role": "system", "content": ANALYZE_PROMPT},
            {"role": "user", "content": f"CONVERSATION SO FAR:\n{recent}\n\nLATEST MESSAGE: {message}"},
        ])

    # ---------- step 2: act ----------
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
