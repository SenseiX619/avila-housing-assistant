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

**Stack (all free):** Python, Streamlit (UI), Groq (gpt-oss AI brain + Whisper speech-to-text),
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

---

## v0.5-dashboard - Staff see every ticket, live

**What:** `pages/1_Staff_Dashboard.py` - a second page (Streamlit makes every file in
`pages/` its own page). Shows:
- Emergency alerts at the top in red
- Counters: open, urgent/emergency, overdue, resolved
- A ticket table with flags: 🚨 Emergency, 🟠 Urgent, ⚠️ OVERDUE, 🟣 On track, ✅ Resolved
- Open tickets by building and by category
- A form to move a ticket to In Progress / Resolved
- Demo tools: load clearly-fake sample tickets, clear all (needs a checkbox first)

**The killer feature:** overdue flags use Avila's OWN rules from the Housing Handbook
(regular repairs within 2 weeks, emergencies within 2 days). Staff instantly see
which promises are being broken, and which building keeps having problems.

**Why it refreshes every 5 seconds:** `@st.fragment(run_every=5)` re-runs only the
board, not the whole page. In the demo, a ticket made by voice on one screen pops
up on the other screen by itself.

**What broke:** Streamlit warned that `use_container_width` is being retired.
**Fix:** switched to `width="stretch"` and required Streamlit 1.50+.

---

## v0.6-voice - Students can just talk

**What:** `voice.py` + a mic recorder in the chat.
- **Ears:** the recording goes to Groq Whisper (free), which turns speech into text
  and detects the language by itself.
- **Mouth:** the agent's reply goes to edge-tts (free) and plays back automatically.
- Voice tickets are tagged "Voice" on the staff dashboard.

**Why it's built this way:** voice is just a second door into the SAME agent brain.
`agent.respond()` doesn't care if the message was typed or spoken. So if the mic or
speaker fails on stage, switch to typing and everything still works.

**Details that matter:**
- Ticket IDs are read digit by digit ("A V 1 0 0 3") so students can write them down.
- After each recording, the recorder gets a new key, so it resets and the same
  recording is never processed twice.
- If text-to-speech fails (no internet), `speak()` returns None and the app stays
  text-only instead of crashing.

---

## v0.7-polish - Looked at it in a real browser and fixed what was off

We opened both pages in a headless browser and took screenshots. Three problems:

1. **The purple banner was missing on the chat page.** Streamlit hides a markdown block
   that starts with a `<style>` tag. **Fix:** put the styles and the banner in two
   separate `st.markdown` calls.
2. **The sidebar menu said "app"** instead of a real page name. **Fix:** `app.py` is now a
   small "front door" using `st.navigation`, and the two screens moved into `views/`
   ("Student Assistant" and "Staff Dashboard").
3. **The dashboard table hid "Due by" off-screen.** **Fix:** reordered the columns so
   the important ones (flag, issue, building, room, priority, due date) come first.

Lesson: tests passing is not the same as the app looking right. Always look at it.

---

## v1.0-demo - Ready for the Nov 4 presentation

**What:**
- `setup.bat` - one double-click: creates `.venv`, installs libraries, downloads the handbook.
- `run.bat` - runs `check_setup.py` first, then starts the app.
- `check_setup.py` - checks the handbook, the Groq key, a live AI reply and voice,
  and prints a plain-English fix for anything that fails.
- `README.md` - architecture diagram, setup, the demo script, free deployment, privacy notes.
- `.gitattributes` - keeps Windows line endings in the `.bat` files so they always run.

**Why a self-check:** on demo day you want to know in 10 seconds whether the key works
and you're online, not find out live in front of the audience.

**Final test (offline stand-in AI):** repair report across 3 messages -> ticket;
smoke report -> safety first + emergency ticket + red alert on the dashboard;
status lookup; handbook question with page citation. All passed.

**Not yet tested with the real AI** (blocked from the build machine): Groq answers,
Whisper transcription and edge-tts voices. `check_setup.py` verifies these on your PC.

---

## v1.1-models - First real run: Groq changed its models

**What broke:** the first run on Sensei's PC said the key worked, but the AI failed with
`model_not_found`. Groq had moved the Llama models we picked to **Enterprise-only**,
so free keys can't use them anymore.

**Fix:**
- Switched to Groq's free production models: `openai/gpt-oss-120b` (main) and
  `openai/gpt-oss-20b` (backup).
- `config.CHAT_MODELS` is now a ranked list. On startup `llm.chat_models()` asks Groq
  which models this key can actually use and skips the rest. If Groq renames a
  model again, the app quietly uses the next one instead of crashing on stage.
- gpt-oss "thinks" before it answers, so we set `reasoning_effort: low` (faster) and
  raised the token budget, so the thinking can't eat the whole answer.

**Lesson:** never hard-code one AI model. Providers rename and retire them all the time.
Keep a ranked list and check what's actually available.

---

## v1.2-voice-fixes - First real voice test: "she only says hi"

**What broke:** Sensei said "hey, my heater isn't working" into the mic, and every time the
assistant answered with the same greeting. No ticket was created.

**Two likely causes, both fixed:**
1. **Silent or noisy recordings.** When a mic picks up nothing, Whisper "hears" phantom
   words like "Thank you." The AI then sees small talk and greets you again.
   **Fix:** `voice.py` measures how loud the recording is. If it's silent, or Whisper only
   returns a known phantom phrase, the student sees "🎤 the recording was silent - check
   your mic" instead of a confusing reply. Whisper also gets a hint with our building names.
2. **The AI mislabelling repairs as small talk.** **Fix:** a second safety net,
   `REPAIR_PATTERN` (like the emergency one). If the message clearly describes something
   broken ("isn't working", "leaking", "won't flush"...), it becomes a repair no matter
   what the AI said. Long messages are never treated as just "hi".

**Also changed (Sensei's call):**
- The assistant no longer talks. The student speaks, the assistant writes. The speaker
  toggle and the text-to-speech step are gone from the chat screen.
- A repeat "hi" gets a short reply instead of the full introduction every time.
- `data/conversation_log.jsonl` records each turn: what was heard, what the AI understood,
  what we did. Now "why did it say that?" has an answer.
- `run.bat` skips Streamlit's one-time "enter your email" prompt on teammates' PCs.

**Tested:** with a deliberately bad AI that labels everything small talk, the heater
message still became ticket AV-1001; a silent recording shows the mic warning.

---

## v1.3-safety-tiers - First full test with the real AI: the right number to call

**What:**
- `tests/test_flows.py` + `test.bat`: 23 saved offline checks of the five demo flows, on the agent
  AND on the real Streamlit pages. They use the fake AI and a temporary database, so they never
  touch real tickets. Double-click `test.bat` after every change.
- A dry run of all five flows with the real Groq AI. For the mic flow, Windows' built-in voice spoke
  "Hey, my heater isn't working" into a WAV file, which went through the real Whisper. Everything
  worked: Whisper heard every line exactly, Spanish came back in Spanish with an English ticket,
  and normal replies took about half a second.

**What broke:**
1. "Can I smoke in my room?" got "This sounds like an emergency... call 911 now", and "my smoke
   detector keeps beeping" became a red EMERGENCY ticket. The AI had read both correctly; the
   danger-word net overruled it just because they contain "smoke".
2. Every emergency said "call 911" first. The handbook (printed p. 23) says: "In Case of Emergency,
   contact Campus Safety at 816.985.6079 or 911. For extreme emergencies, call 911."

**Fix (Sensei's call: "911 only for a proper fire, Campus Safety for smoke and beeping, give both"):**

| Situation | First thing the student reads |
|---|---|
| Real fire, someone hurt, gas, carbon monoxide | Call 911, then Campus Safety |
| Fire alarm sounding | Leave the building, then call Campus Safety |
| Smoke, sparks, flooding, beeping | Call Campus Safety (911 if flames or someone hurt) |

- Both numbers are always shown. The danger-word net stays, and the AI alone still can't call off
  a danger word. Only when the AI AND the wording agree it isn't happening right now (a rules
  question, or a smoke detector beeping) does the student get a one-line safety reminder and a
  normal reply. A beeping detector becomes an urgent repair (2-day deadline).
- The nets also read the AI's English summary of the message, so a fire reported in Spanish
  still gets 911 first.

**Also found (for task 5):** Groq's free tier gives the main model 8,000 tokens per minute.
Understanding one message costs about 800 tokens, a handbook answer about 1,900 in total. After
about 10 quick messages, replies slowed from 0.5 seconds to 5-14 seconds.

**Lesson:** a safety net that ignores context cries wolf, and people learn to ignore it. Before
standing an alarm down, make two independent signals agree.

---

## v1.4-click-first - Second real test: the mic heard nothing, and "make it easy to tap"

**What broke:**
1. Sensei's voice test left **no trace in the log**. Every recording was turned away before it
   reached the assistant (a silent mic, or Whisper "hearing" phantom words like "Thank you."),
   and those rejections were never logged. Windows lists two microphones on this PC, the
   laptop's and a PS5 DualSense controller's headset mic, so the browser may have been
   listening to the wrong one.
2. The app that was already running kept the assistant in memory, so it was still answering
   with the old code: "Can I smoke in my room?" still got "call 911" after v1.3 fixed it.

**Fix:**
- Mic: loudness is measured on the loudest 0.1 second, so a pause before speaking no longer
  looks like silence. Repeated phantom phrases are caught too. The warning says to check which
  mic the browser is using. Every recording is logged: what was heard, how loud, what went wrong.
- The chat reloads the assistant whenever a code file changes. No restart needed.

**New (Sensei's call: "click and speak or type... answer in steps... not too much to read"):**
- Buttons under every reply for the next step. A repair is now four taps:
  🔧 Report a repair -> 🔥 Heat / AC -> Thompson Hall -> type 214.
- Tapped buttons skip the AI entirely: instant, and they don't use the free tier's tokens.
- Emergencies show Call 911 / Call Campus Safety buttons, which dial on a phone.
- Shorter replies everywhere. Handbook answers are 1-3 sentences.

**Tested:** 30 offline checks, including a full tap-through with zero AI calls and a recorded clip
on the real chat page. All five flows again with the real AI and Whisper: every reply in about
1.2 seconds or less. On the already-running app, the four-tap repair made ticket AV-1008 instantly.

**Update (Nov 3):** a screenshot of Sensei's Chrome tab showed the old warning "The recording was
silent", so the mic did record; the laptop mic was the default (48% volume, not muted). The likely
cause was the old check that averaged loudness over the whole clip, which is what v1.4 replaced.

**Lesson:** log failures, not just successes. The worst bug is the one that leaves no trace.

---

## v1.5-printed-pages - Citations match the page numbers students see

**What broke:** citations used the PDF's page count, but the handbook prints its own numbers,
and they are one lower (the cover has no number). The guest policy was cited as "p. 15", but a
student flipping through the handbook finds it on the page printed **14**.

**Choice:** cite the number printed on the page, not "PDF page". A student holding the handbook
(paper or PDF) looks for the number on the page, and staff can check us the same way.

**Fix:**
- `knowledge.py` reads the printed number from the first line of each page (every page has one)
  and stores it as `page`, keeping the PDF's count as `pdf_page`. Pages without a printed number
  keep the PDF count, so another document dropped into `data/docs/` still works.
- The printed number is also removed from the chunk text, so it isn't searched as a word.
- The "Sources" line under an answer now lists only the pages the answer cites, not every page
  the search pulled up.

**After pulling this change, run `python ingest.py` once** to rebuild `data/knowledge.json`.

**Tested:** 33 offline checks (Lockouts = printed p. 14, PDF p. 15). With the real AI: lockout fee
-> p. 14-15, guests -> p. 14, smoking -> p. 17; each checked against the printed handbook pages.
