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

---

## v1.6-safe-json - The agent keeps working when the AI misbehaves

**Risk:** every message starts with ANALYZE, which must return JSON. Before, if gpt-oss sent broken
JSON the student saw "Sorry, I'm having trouble right now (Expecting ',' delimiter: line 1...)",
technical text on the projector. If Groq was down, nothing worked at all, not even emergencies.

**Fix:**
1. **Retry once:** `llm.chat_json` asks again if the reply isn't a JSON object, then gives up with
   `BadJSON`. (Groq also rejects invalid JSON itself, which already moves the call to the backup model.)
2. **Tidy the shape:** valid JSON can still be wrong (`"ticket": "none"`, `"intent": "Emergency!"`).
   `_tidy()` coerces it into the shape the agent expects, so a sloppy answer can't crash it.
3. **Safe fallback:** if the AI fails twice or can't be reached, a rule-based reading takes over
   (danger words, repair words, building names, room and ticket numbers). Emergencies still get the
   safety message, repairs still become tickets, ticket lookups still work. Tapped buttons never
   needed the AI anyway.
4. **Log, don't show:** every problem goes to `data/conversation_log.jsonl` (`"event": "analyze_problem"`,
   with `"used_fallback"`), even when the retry worked, so we can see how often it happens. Students
   only see a friendly line plus the main buttons.

**Tested:** 39 offline checks, including the AI sending garbage on every message (a repair still
becomes a ticket) and the AI being unreachable (smoke still gets the safety message and an
emergency ticket). With the real AI: all five flows again, 0 JSON problems; none in any real run
today (about 40 ANALYZE calls).

---

## v1.7-fast-failover - Replies stay under ~1 second, even in a fast demo

**What broke:** after about 10 quick messages, replies slowed from 0.5 seconds to 5-14 seconds.

**Why:** Groq's free tier gives **each model** 8,000 tokens per minute (one handbook answer uses
about 1,900). When the main model ran out, the Groq library quietly **waited and retried** it, up
to 2 times, before our code ever tried the backup model. The backup models each have their own
8,000 tokens, sitting unused.

**Fix (`llm.py`):**
- `max_retries=0`: a model that's out of tokens fails in a split second, and `chat()` moves straight
  on to the next model.
- That model then "rests" until Groq says it's ready again, so the next messages skip it instead
  of hitting the limit again.
- A stuck call gives up after 15 seconds instead of 60.
- Together that's about 3x the capacity (three models x 8,000 tokens a minute), still free.
- Tapped buttons (v1.4) use no tokens at all, which helps too.
- `check_setup.py` no longer calls the old text-to-speech service on every start (3 s total now).

**Tested:** 40 offline checks, including a fake "out of tokens" model. Real burst test: 10
handbook questions back-to-back. The main model ran out at question 6, and the switch took
0.06 seconds. **Every reply took 0.7-1.1 seconds.** Before this fix the same situation took 5-14 s.

---

## v1.8-demo-ready - Reset button and projector polish

**What:**
- **🔄 Reset demo** in the dashboard's sidebar: deletes every ticket, numbers them from **AV-1001
  again** and loads 6 clearly-fake sample tickets (2 already overdue, to show the flags). It takes
  two clicks (open, confirm), so a stray click on stage can't wipe the board. Deleting tickets alone
  never reset the numbering, because SQLite remembers the last number; `reset_demo()` clears that too.
- **`.streamlit/config.toml`** (read when the app starts):
  - `toolbarMode = "minimal"`: no Deploy button, Rerun menu or Streamlit's "Help agents" popup.
  - `runOnSave = true`: open tabs load code changes by themselves. No more stale tabs showing
    "File change - Rerun" (that's what made the mic retest test old code).
  - Light theme with Avila purple buttons: projectors wash out dark themes.
- **Ticket table** sized for a 1280-wide projector: Flag, Due by, Issue, Where, Status, Via, Language.
  Building + room are one "Where" column. Category is still in the chart.
- `AVILA_DB_FILE` (optional) points a practice copy of the app at its own database, so trying
  the Reset button never touches the real demo tickets.

**Tested:** 41 offline checks (reset -> AV-1001..AV-1006, next ticket AV-1007). Looked at the
dashboard at 1280x720 in a practice copy with its own database: reset worked, all columns fit.

---

## v1.9-cloud-ready - Ready for a free online copy (not deployed)

**What:**
- `requirements.txt` pins the exact library versions we tested, so a new release can't break the
  demo (or a teammate's fresh `setup.bat`) the night before.
- Removed the text-to-speech code and `edge-tts`, unused since v1.2.
- The repair safety net understands phone keyboards' curly apostrophe ("isn’t").
- `data/knowledge.json` (public handbook text) is committed, so a cloud copy needs no PDF.
- README has step-by-step Streamlit Community Cloud instructions (private repo, key in Secrets,
  app shared privately with teammates).

**Checked:** the Groq key has never been in the code or git history. 42 offline checks pass.

**Not done (Sensei's call):** the deploy itself was skipped for now. The README steps take about
10 minutes when needed.

---

## v1.10-phone-qr - "Scan to try it on your phone"

**Why:** Sensei's laptop microphone sends silence even to Windows (its Sound meter doesn't move),
so voice can't work on that PC. A phone's microphone does work, but only over https, which means
the online copy. A QR code is also the easiest way in for students: nothing to install or type.

**What:** `phone_link.py` turns the app's online address into a purple QR code. It sits at the top
of the dashboard's sidebar (for the projector) and in the student sidebar. It finds the address by
itself (or `APP_URL`), and on "localhost" it explains how to get a phone link instead.

**Status:** built and tested (44 offline checks). It switches on once the app is online. The
deploy was put off for now (Sensei's call); the README has the steps.

**Update (Nov 3): live at https://avila-housing.streamlit.app** (public repo
github.com/SenseiX619/avila-housing-assistant; Groq key only in Streamlit Secrets). The first start
hung on a loading circle; "Reboot app" fixed it. Checked live: a handbook question answered with
"(Handbook p. 14)", and the dashboard shows the QR code. Online tickets are separate from the
laptop's and reset when the app restarts or wakes from sleep, so press Reset demo on the online
dashboard before presenting.

---

## v1.11-laptop-link - A secure link from the laptop, for phones

**What broke:** the Streamlit Cloud copy froze for every visitor a few minutes after each start:
the page's live connection (websocket) stayed on "connecting" while the server answered normal
requests in 0.1 s. The same code on the laptop, even with Cloud-like settings, connected in 6 ms.
So it's Streamlit's hosting, not our code, and we couldn't see their logs.

**Fix:** `run_online.bat` -> `online.py` starts the app on this laptop and opens a free Cloudflare
"quick tunnel" (no account) that gives it an https link. Phones scan the QR code (which points to
the link) and can use their own microphone. Bonus: phone tickets land on the same laptop dashboard
shown on the projector. Checked: connection opens in about 0.2 s through the link.

**Trade-offs:** works only while the laptop is on and that window is open; the link changes on
each start (the QR code updates by itself). The Cloud copy stays as a backup.

---

## v1.12-ticket-emails - Tickets reach a real person

**Why:** saving a ticket on the laptop isn't enough; someone has to hear about it.

**What:** `notify.py` emails every new ticket (number, priority, handbook deadline, where, the
student's own words, voice/text, language) in the background, so the student never waits. A failed
email never breaks the chat; each attempt is logged (`"event": "ticket_email"`, sent/failed).
Sample tickets are never emailed.

**Settings (only in `.env` / Streamlit Secrets, never in the code):** `TICKET_EMAIL_TO` (who gets
them: for now Sensei's own Avila inbox, HOUSING@avila.edu after approval), `SMTP_USER` +
`SMTP_APP_PASSWORD` (a Gmail account and a Gmail app password, which only sends mail and can be
deleted anytime). Avila's Outlook isn't used to *send* because Microsoft blocks simple app logins
and it would put a university password in the app.

**Tested:** 4 faked email tests (tests switch email off, so they never send). Real: `python notify.py`
test email "sent"; a repair typed through the secure link became AV-1008 and its email was "sent"
one second later. `check_setup` shows "[PASS] New tickets are emailed to ...".

**Lesson:** never paste passwords into a chat. An app password shared there was deleted and replaced.

---

## v1.13-room-fixes - Learned from Sensei's real phone test

**What broke (from data/conversation_log.jsonl, Nov 3, 2:58 PM):**
1. Saying only "322" was rejected as a phantom phrase: the filter only counted letters, so a bare
   number looked like "no words".
2. Whisper heard "three twenty-two" as "three to two", so ticket AV-1007 got room "3-2".
3. Repeating "Villa Ventura, room 322" created a second ticket (AV-1008) and a second email.

**Fix:** digits now count as real words. And if, within 10 minutes, the same problem in the same
hall comes in with a different room, the student is correcting the room: the ticket they just made
is updated ("Fixed! Ticket AV-1007 is now for room 322...") and staff get an "UPDATED (Room
corrected)" email instead of a duplicate. A different problem is still a new ticket.

**Tested:** 50 offline checks (2 new + spoken numbers).

---

## v1.14-sturdy-link - The phone link no longer dies on its own

**What broke (Nov 3, afternoon):** the phone link stopped working a while after starting. Two causes:
1. `run_online.bat` found an app already running (one Claude had started in its own terminal) and
   only made a link to it. When that other copy closed, the link pointed at nothing.
2. A sleeper bug: after printing the link, `online.py` stopped reading cloudflared's status
   messages. Once enough pile up, Windows pauses cloudflared until someone reads them, and the
   link freezes.

**Fix (`online.py`):** it always starts **its own** app (on the next free port if 8501 is taken),
keeps reading cloudflared's messages in the background, and watches the app: if it ever stops,
it's restarted on the same link within seconds. Start it by double-clicking `run_online.bat`, in
its own window, not inside another program's terminal.

**Tested:** killed the app on purpose: back on the same link in 3 seconds. Link checked: the
dashboard and QR code load, and the connection opens in about 0.2 s.

---

## v1.15-staff-lock - Only staff can open the Staff Dashboard

**Risk (found in the Nov 13 audit):** the QR code on the projector puts this app on everyone's phone,
and the Staff Dashboard was one tap away in the sidebar. Anyone in the audience could change a
ticket's status or press **Reset demo** and wipe every ticket, live, during the presentation.

**Fix:**
- The dashboard asks for a staff password (`STAFF_PASSWORD` in `.env` or Streamlit Secrets, never in
  the code). Until it's typed, the page shows only the password box: no tickets, no Reset button, no
  status changes. Staff type it once per browser tab.
- No password set? The dashboard still opens (for practice), with a small "🔓 No staff password set"
  note in the sidebar, and `check_setup.py` prints a `[WARN]`.
- The student page is unchanged: students never need a password.

**Tested:** 54 offline checks (4 new: locked without the password, wrong password stays locked, right
password opens it, the warning when no password is set). In a practice copy with its own database:
locked page, "Wrong password.", then the dashboard opened and stayed open through the 5-second refresh.

**Lesson:** a demo link shared with a room is a public link. Anything that can delete data needs a lock.

---

## v1.16-room-ranges - Every ticket goes to a real room in the right hall

**What broke (found in the Nov 13 audit):** any room number was accepted. "Wylie 99999" and
"Buchanan 3-2" both became tickets, and staff would have walked to rooms that don't exist. The
sample tickets and the demo script used rooms like Thompson 214 and Wylie 108, which aren't real.

**The rule (Sensei, Nov 11):** Thompson 1100-1300, Wylie 1400-1600, Buchanan 400-600, Villa Ventura
("the Villas") 200-300, both ends included. It lives in `config.ROOM_RANGES`. A suite letter is fine (1210B).

**Fix (`agent.py`):**
- **A room on its own tells us the hall.** "1450" means Wylie Hall, so the student doesn't have to say
  it. Typing just the number skips the AI.
- **A room that isn't a housing room** ("Wylie 99999") makes no ticket. The reply says which rooms
  that hall has and asks again.
- **A room from a different hall** ("Thompson 1450") makes no ticket. The reply says 1450 is a Wylie number
  and offers a one-tap button, "Wylie Hall, room 1450", or the student types their Thompson room.
- **A hall that isn't one of the four** gets the four hall buttons.
- Spoken numbers with dashes ("14-50") are read as 1450. A dash that doesn't make a room ("3-2") is asked again.
- **Emergencies are never held back** for a wrong room: the safety message and the emergency ticket
  come first. A room on its own still fills in the hall there too.
- Sample tickets, the README demo script and CLAUDE.md now use real rooms (Thompson 1214, Wylie 1408,
  Buchanan 502, Villa Ventura 212...).

**Tested:** 62 offline checks (8 new: every range edge, invalid room, wrong hall + one-tap fix, room
alone, unknown hall, emergency with a room alone, sample tickets all real). With the real AI
(temporary database, no emails): "Thompson 1450" -> Wylie button -> ticket; "room fourteen fifty" ->
Wylie Hall 1450; "the Villas room 250" -> Villa Ventura 250; "Wylie 99" in Spanish -> asked again in Spanish.

**After pulling this: press Reset demo on the dashboard once**, so the sample tickets use the new rooms.

---

## v1.17-no-waiting - Emergencies are never queued

**What broke (found in the Nov 13 audit):**
1. The backup danger-word list (it catches what the AI misses, and takes over when the AI is down) didn't
   know "it smells **like** gas", "gas leak" or "water coming through the ceiling". With the AI down,
   "It smells like gas" only got "Sorry, I'm having trouble right now".
2. "Smoke in the hallway!" with no room saved **nothing**. If the student never answered "which room?",
   staff never heard about it.
3. **No heat** waited in the normal 2-day queue (Sensei: no heat is an emergency). So did a door that
   won't lock and an overflowing sink, which the handbook (printed p. 19) calls "severe".
4. **"I'm locked out"** was answered as a fee question. Nothing told the student who could let them in.

**Fix:**
- **Danger words:** "smells like gas", "gas leak", "leaking gas" (911 first), "water coming through the
  ceiling", "ceiling is leaking" (Campus Safety first).
- **Alert first, ask later:** an emergency is saved and emailed to staff at once, as "Location unknown"
  if needed. When the student says where, the place is added to that **same** ticket, and staff get a
  "Location added" email. Only one alert per conversation.
- **Can't-wait repairs** (`CANT_WAIT`: no heat, a door or lock that won't lock, an overflow) become
  **EMERGENCY repairs**: red on the dashboard, high-priority email, and the student reads "In the evening
  or on a weekend, also contact the RA on duty or Campus Safety now (Housing Handbook p. 19)", with a
  call button. The "🔥 Heat / AC" button is now two buttons: **🔥 No heat** (emergency) and **❄️ AC** (urgent).
- **Lockouts:** "Locked out right now? Housing staff help with lockouts (p. 14): contact your RA, or the
  RA on duty in the evening or on a weekend", with a Campus Safety call button. A fee question still
  gets its handbook answer underneath. This works even when the AI is down.
- **Emergency emails are high priority** ("Importance: High" in Gmail and Outlook).

**Not invented:** the handbook says housing staff help with lockouts (p. 14) but gives no lockout phone
number, so the reply names the RA and the RA on duty (the people p. 19 names for after-hours problems).
**Ask Avila Housing who students should contact for a lockout**, and we'll put that in.

**Tested:** 71 offline checks (9 new). With the real AI (temporary database, no emails): heater -> EMERGENCY
repair; "It smells like gas" -> 911 first + ticket AV-x "Location unknown", then "Wylie 1450" was added to that
ticket; ceiling leak -> Campus Safety first; "door won't lock" -> EMERGENCY repair; lockout -> who to contact,
then the fee answer. "Can I smoke in my room?" and the beeping smoke detector still get no false alarm.

---

## v1.18-closed-loop - Every ticket gets a full life, and the student hears back

**What was missing (found in the Nov 13 audit):** tickets were Open, In Progress or Resolved, with no
record of who had them or when. When staff marked a repair Resolved, **the student never heard**; they
had to come back and ask. Nobody checked whether the fix actually worked.

**Fix:**
- **Four statuses** (Sensei's list): **Submitted -> Assigned -> In Progress -> Resolved.**
- **Who and when:** staff pick a team on the dashboard ("Assigned to": Maintenance team, Plumbing,
  Electrical, Heating & AC, Locksmith, Custodial, IT / Wi-Fi, RA on duty: teams, not real people). Each
  ticket records `assigned_at` and `resolved_at`. Step 5's response times are measured from these.
- **The loop back to the student:** the chat watches the tickets the student reported or asked about.
  When staff change one, a message appears in that chat **by itself within 5 seconds**: "📬 Update:
  ticket AV-1009 ... is now Assigned to Plumbing."
- **"Did that fix it?"** on a resolved ticket: **✅ Yes** marks it confirmed fixed (dashboard: "✅ Confirmed
  fixed"). **❌ No** reopens it, back to the same team (dashboard: "🔁 Reopened"), and staff get a "REOPENED"
  email.
- The status reply says who has it: "Assigned to Plumbing", "In Progress (Plumbing is on it)".
- The dashboard's Status column shows the team ("Assigned · Plumbing"), and the update form starts at the
  ticket's current status and team. The "saved" message used to vanish at once; now it stays.
- Older databases upgrade themselves: the new columns are added, and "Open" becomes "Submitted".

**Limit (on purpose):** the student only hears back while their chat is open, or when they ask about
the ticket. Emailing or texting students needs their contact details, which this prototype doesn't
collect (public info and fake data only). That's a next-phase item once Avila approves.

**Tested:** 80 offline checks (9 new: the lifecycle times, an old database, who has it, updates reach
the chat once, yes/no, on-screen resolve -> chat -> tap Yes, assigning on the dashboard, the Reopened
flag). In the practice copy, in a real browser: asked about AV-1001; marking it Resolved made
"📬 Update ... Did that fix it?" appear in the chat by itself; tapping Yes -> "Ticket AV-1001 is closed",
and the dashboard showed "✅ Confirmed fixed".

---

## v1.19-summary - "How we're doing" for leadership

**What was missing (found in the Nov 13 audit):** the dashboard counted open, urgent, overdue and resolved
tickets, but couldn't say **how fast** anything happened (nothing recorded when a ticket was assigned or
fixed, until v1.18). All 6 sample tickets were waiting, so "Resolved" showed 0 in every demo.

**What:**
- A **How we're doing** row of four tiles, computed by `tickets.summary()`:
  - **⏱️ Avg response time:** from the student's report until a team is assigned.
  - **🔧 Avg time to fix:** from the report until staff mark it resolved.
  - **📅 Fixed on time:** resolved within the handbook deadline (2 days urgent/emergency, 2 weeks regular).
  - **👍 Confirmed by students:** resolved tickets where the student tapped "Yes, it's fixed".
  Hover the ⓘ on each tile for that explanation.
- **Requests by building**, open and resolved, for every ticket, not just open ones. It shows which hall
  keeps having problems.
- **Reset demo now loads 12 clearly-fake sample tickets with a history:** waiting, assigned, in progress,
  fixed, one fixed late, one the student hasn't confirmed, one reopened. After a reset: 7 open, 5 resolved,
  9.1-hour average response, 5.1 days to fix, 4 of 5 on time, 4 of 5 confirmed.
- Chart colours: Avila purple (open) and a darker Avila gold (resolved). They passed the dataviz skill's
  palette checker (colour-blind separation, normal vision, 3:1 contrast on white). The brighter Avila gold
  failed the contrast check, so it isn't used for chart bars.
- Fixed: the chart axis titles sat sideways next to the hall names (Streamlit swaps them on sideways bar
  charts). They now sit under the numbers.

**Tested:** 84 offline checks (4 new: the summary maths with known times, nothing-to-measure, the sample
history, the tiles on screen). Looked at it at projector size (1280x720) in the practice copy: tiles and
charts fit, and the legend reads Open / Resolved.

**After pulling this: press Reset demo once** to load the 12 new sample tickets.

---

## v1.20-no-duplicates - The same problem reported twice is one ticket

**What broke (found in the Nov 13 audit):** reporting the same problem twice in one chat made two tickets,
and so did two roommates reporting the same leak. Staff would send two people to one sink.

**Fix:** before saving a ticket, the agent looks for an **open** ticket in the **same hall and room** for
the **same problem** (`tickets.same_problem`): they share a key word ("sink", "heat"), have the same category,
and the new report names no fixture the open ticket doesn't.
- **The same student again:** "You already reported this: ticket AV-1001 ... is Submitted. No need to
  report it again." No second ticket, no second email.
- **A roommate:** "This was already reported: ticket AV-1001 ... I've added your report." The ticket counts
  **2 reports** (dashboard: "Sink leaking · 2 reports"), keeps their words for staff, and staff get one
  "Reported again (2 reports)" email. The roommate's chat then follows that ticket too.
- **More serious this time** ("outlet not working" is open, now "sparks from the outlet"): the safety
  message still comes first, and the same ticket goes up to EMERGENCY ("Reported again, now an EMERGENCY").
  Urgency only ever goes up.
- **Not duplicates:** a different problem in the same room (sink vs toilet), the same problem next door,
  or a problem coming back after its ticket was resolved. Each gets its own ticket.

**What broke along the way:** in the real-AI check, "Also our toilet is clogged" came back from the AI as
"toilet clogged and sink leaking" (it pulled in the earlier sink), so it was merged into the sink ticket and
the toilet was lost. **Fix:** a new fixture (toilet) always means a new problem, and the ANALYZE prompt now
says the issue is only the problem in the latest message.

**Tested:** 90 offline checks (6 new). With the real AI (temporary database, no emails): sink -> AV-1001;
"still leaking" -> "You already reported this"; roommate's dripping sink -> AV-1001, 2 reports; "Also our
toilet is clogged" -> its own ticket AV-1002; "outlet doesn't work" then "Sparks from the outlet!" -> safety
message first and the outlet ticket became EMERGENCY with 2 reports.

---

## v1.21-talk-to-a-person - When the agent can't help, a person can

**What was missing (found in the Nov 13 audit):** when the handbook didn't have the answer, the agent said
"email HOUSING@avila.edu" and the conversation stopped there. "Can I talk to a real person?" got "Hey! What
can I help with?".

**What:**
- **🙋 Talk to a person** is in the main menu. Typing it works too ("can I talk to a real person?", "speak
  with someone"), and in any language: the AI now flags `wants_person` ("¿Puedo hablar con una persona...?").
- The student's question goes to housing staff as a request (**Q-1001**), saved in the same database (new
  `handoffs` table) and emailed to staff. A question in another language is saved in English too, with the
  student's own words.
- If the message is only "talk to a person", the agent sends the question it just answered (or couldn't),
  or asks "What would you like to ask housing staff?".
- **Staff answer on the dashboard:** a live "🙋 1 student waiting for a person" alert at the top, and a
  **Questions for a person** box to type the answer. The answer appears in the student's chat by itself
  ("💬 Housing staff answered your question (Q-1001): ..."), the same way ticket updates do.
- When the handbook has nothing, or the answer says "not sure", **Talk to a person** is the first button,
  and one tap sends that exact question.
- Danger words still come first: "I need to talk to someone, there's smoke!" gets the safety message.
- Reset demo adds one clearly-fake waiting question, so the feature shows in a demo.

**What broke along the way:** the first version only understood English requests, so the Spanish one
was answered as a handbook question. Fix: the AI's `wants_person` flag (the English phrases stay as a
backup for when the AI is down).

**Tested:** 101 offline checks (11 new). With the real AI (temporary database, no emails): "Can I talk to a
real person?" -> asks -> Q-1001; a Spanish request -> Spanish reply, saved in English + Spanish; "Is there a
24-hour gym in Wylie?" -> "not sure" -> one tap -> Q-1003. In the practice copy: the waiting alert and the
answer box show on the dashboard.

---

## v1.22-safety-nets - Failed emails are visible, and Reset demo can be undone

**What was missing (found in the Nov 13 audit):**
1. When an email to staff failed (no internet, a wrong Gmail app password), the ticket was saved, but the
   only trace was a line in the conversation log. Nobody would notice that staff never got it.
2. Reset demo deleted every ticket for good. One wrong click on the real tickets and they were gone.

**Fix:**
- Every ticket and question remembers what happened to its email (`email_status`: "sent" or "failed: ...").
- The dashboard shows a yellow warning when any failed: "📧 The email to staff failed for AV-1013. They're saved
  here, but nobody was emailed. (no internet connection)". The reason is in plain words (no internet, Gmail
  rejected the login, timed out). **📧 Retry emails** sends them again.
- When email isn't set up at all, the sidebar says so.
- **Reset demo backs up first:** a full copy of the database goes to `data/backups/` (SQLite's own backup, safe
  while the app runs). The newest 10 are kept, and the folder is git-ignored. The success message names the
  backup. README: how to restore one.

**What broke along the way:** on Windows a file that's open can't be deleted, so deleting old backups could
have crashed Reset demo if a backup was open in another program. Now it just keeps that file.

**Tested:** 107 offline checks (6 new: failed/sent recorded on tickets and questions, the warning + Retry on
screen, the backup holds the old tickets, only 10 kept). Upgraded a copy of the real database (8 tickets, all
kept, "Open" -> "Submitted") and reset it: the backup held all 8. In the practice copy at projector size:
the warning and the Retry button show.

---

## v1.23-sign-in - Students sign in to report repairs; sample data says so

**Why (Sensei, Nov 20, for the Dec 9 presentation to the president, the dean and the faculty heads):**
"How do we make it so the student signs in as well?" Staff need to know who reported a repair, and leaders
will ask "can't anyone pretend to be a student?".

**What:**
- **Sign in** at the top of the chat: name + @avila.edu email, or one tap on a **made-up demo student**
  (Maria · Thompson 1230, Jordan · Wylie 1460, Sam · Buchanan 520). No password yet: the pilot would use Avila's
  Microsoft login, which needs Avila IT and a fixed web address.
- **Repairs need it; nothing else does.** A guest who reports a leak gets "please sign in", the sign-in box opens,
  and the report goes in the moment they sign in. Questions, emergencies (and no heat), and "Talk to a person"
  never need a sign-in, and the emergency numbers now sit on the page itself (phones hide the sidebar).
- Signed in: tickets show who reported them (dashboard "Student" column, staff email "Reported by"), "Check my
  ticket" finds their tickets on any device, and their room is one tap ("📍 Thompson Hall, room 1230").
- Privacy: names and emails are never sent to the AI, never written to the conversation log, and students are
  never emailed (a made-up address can't reach a real person).
- **"🧪 Sample data" note** on the dashboard whenever Reset demo's made-up tickets are showing, so nobody
  takes the sample numbers for real results.

**What broke along the way:** the README demo script used Wylie 1408 and Buchanan 502 - the same rooms as sample
tickets - so since v1.20 those demo repairs would have said "This was already reported". The demo script now
uses rooms with no sample ticket (checked by a test). The demo student buttons were cut off on narrow screens;
they now use first names.

**Tested:** 117 offline checks (10 new). A full dry run of the new demo script with the real AI (temporary
database, no emails): every step worked, from the guest question to "Did that fix it?" and Talk to a person.
In the practice copy: the sign-in box, Maria's one-tap sign-in, and the Sample data note.
