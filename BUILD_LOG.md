# Build Log - Avila Housing Assistant

Every step we take gets a short entry: **what** we built, **why** we built it that way,
and **what broke** (plus how we fixed it). Read this alongside `git log` to see
exactly how the app came together. Each milestone has a Git tag you can jump to:

```
git checkout v0.1-setup        # go back in time to that step
git checkout main              # come back to the latest version
git diff v0.2-knowledge v0.3-qa-chat   # see exactly what one step added
```

---

## v0.1-setup - Project skeleton

**What:** Folder structure, `config.py`, `requirements.txt`, `.gitignore`, `.env.example`.

**Why:**
- All settings live in `config.py`. To reuse this agent for another school or a
  KC business, we only change that one file.
- API keys go in a hidden `.env` file that `.gitignore` keeps OFF GitHub.
  Leaked keys get abused within minutes, so this is rule #1.
- Facts like building names, the Campus Safety number and repair deadlines
  come straight from the public 2026-27 Avila Housing Handbook.

**Stack (all free):** Python, Streamlit (UI), Groq (AI brain + speech-to-text),
edge-tts (voice), SQLite (tickets), rank_bm25 (search).

---

## v0.2-knowledge - The agent learns the Housing Handbook

**What:** `ingest.py` downloads Avila's public Housing Handbook PDF and splits it into
small overlapping chunks (each remembers its page number). `knowledge.py` searches them.

**Pipeline:** `PDF -> text per page -> 900-character chunks (150 overlap) -> BM25 keyword index`

**Why:**
- Page numbers on every chunk = the agent can cite "Handbook p. 13", so staff can trust it.
- Overlap between chunks means a policy cut in half still appears whole somewhere.
- BM25 keyword search instead of a vector database: no model downloads, no GPU,
  instant, nothing to break on demo day. Easy to upgrade later.

**What broke:** "I got locked out" found nothing, because the handbook says "Lockouts".
**Fix:** a small `PHRASES` map that translates student slang into handbook words
("locked out" -> "lockout", "girlfriend" -> "guest", "loud" -> "quiet"), plus cutting
plural "s". After that, 4/4 test questions found the right page.

---

## v0.3-qa-chat - Students can ask questions in a chat

**What:**
- `llm.py` - the only file that talks to the AI (Groq). If the main model is busy,
  it automatically retries with a smaller backup model.
- `agent.py` - the brain. Every message goes through **ANALYZE** (AI returns JSON:
  intent, language, English search keywords) then **ACT** (Python decides what to do).
- `app.py` - the Streamlit chat screen in Avila purple and gold, with example
  questions and the Campus Safety number always visible.
- `fake_llm.py` - a rule-based stand-in so we can test without internet (`FAKE_LLM=1`).

**Why:**
- The AI understands messy human language; Python follows the rules. Splitting
  "understand" from "decide" makes the agent predictable.
- The analyzer writes search keywords in English, so a question in Spanish or Urdu
  still finds the right handbook page. The answer comes back in the student's language.
- The agent only answers from handbook excerpts and cites the page. If the answer
  isn't there, it says so and points to HOUSING@avila.edu instead of inventing a policy.

**Tested:** chat screen loads, a lockout question finds the Lockouts page and cites it.

---

## v0.4-tickets - The agent creates real maintenance tickets

**What:**
- `tickets.py` - SQLite database of tickets. Each gets a friendly ID students can
  say out loud (AV-1001) and a repair deadline from the handbook.
- `agent.py` now handles 5 intents: question, maintenance, emergency, ticket_status, smalltalk.
- The chat shows a ticket card (purple = normal, orange = urgent, red = emergency).

**How a repair conversation works:**
1. Student: "my sink is leaking" -> the analyzer pulls out `issue` + `category`.
2. Python checks what's missing (building? room?) and asks ONLY for that.
3. Every answer gets merged into a **draft** ticket kept in the conversation memory.
4. When issue + building + room are all known -> ticket saved -> confirmation with
   the handbook deadline (2 weeks regular, 2 days urgent/emergency).

**Emergencies:** safety message FIRST (911 + Campus Safety 816-985-6079), then the
ticket. A regex safety net (`EMERGENCY_PATTERN`) catches words like smoke, gas,
flooding and sparks even if the AI misreads the message. An emergency can never be
downgraded later in the conversation.

**What broke:** asking "what's the status of my ticket?" without a number did
nothing useful. **Fix:** the agent remembers the tickets created in this
conversation (`my_tickets`) and looks up the latest one.
