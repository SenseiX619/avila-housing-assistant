"""
fake_llm.py - a dumb, rule-based stand-in for the real AI.

ONLY for testing the app's plumbing without internet (set FAKE_LLM=1).
It recognises which job it was given by the "TASK:" line in the system prompt.
The real demo always uses Groq.
"""

import json
import re

import config


def _last_user(messages):
    for m in reversed(messages):
        if m["role"] == "user":
            return m["content"]
    return ""


def respond(messages, json_mode):
    system = messages[0]["content"]
    user = _last_user(messages)

    if "TASK: ANALYZE" in system:
        msg = user.split("LATEST MESSAGE:")[-1].lower()
        draft_open = '"issue": "' in user.split("CURRENT TICKET DRAFT:")[-1].split("LATEST MESSAGE:")[0]
        building = next((b for b in config.BUILDINGS if b.split()[0].lower() in msg), None)
        room = re.search(r"\b(\d{3,4}[a-z]?)\b", msg)
        ticket_id = re.search(r"av-?\d{4}", msg)
        if ticket_id or "status" in msg or "my ticket" in msg:
            intent = "ticket_status"
        elif any(w in msg for w in ("fire", "smoke", "gas", "flood", "sparks")):
            intent = "emergency"
        elif any(w in msg for w in ("broken", "leak", "not working", "isn't working", "clogged")) or (
                draft_open and (building or room)):
            intent = "maintenance"
        elif msg.strip() in ("hi", "hello", "hey"):
            intent = "smalltalk"
        else:
            intent = "question"
        issue = None
        if intent in ("maintenance", "emergency") and not (draft_open and (building or room) and len(msg) < 40):
            issue = msg.strip()[:80]
        return json.dumps({
            "language": "English",
            "intent": intent,
            "search_query": msg,
            "ticket": {"building": building, "room": room.group(1) if room else None, "issue": issue,
                       "category": "Plumbing" if "leak" in msg or "sink" in msg else "Other",
                       "urgency": "emergency" if intent == "emergency" else "normal"},
            "ticket_id": ticket_id.group(0).upper().replace("AV", "AV-").replace("--", "-") if ticket_id else None,
            "cancel": "cancel" in msg,
        })

    if "TASK: ANSWER" in system:
        page = re.search(r"\(page (\d+)\)", user)
        excerpt = user.split("HANDBOOK EXCERPTS:")[-1].strip().split("\n")[1][:160]
        return f"[FAKE AI] {excerpt} (Handbook p. {page.group(1) if page else '?'})"

    if "TASK: TRANSLATE" in system:
        return "[FAKE TRANSLATION] " + user

    return "[FAKE AI] ok"
