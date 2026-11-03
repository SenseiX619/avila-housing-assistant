# Avila Housing Assistant - brief for Claude Code

You're joining an existing project: the Avila Housing Assistant, an AI agent for Avila
University (Kansas City) housing. My team presents it to the university on Wednesday,
Nov 4, 2026, to get approved as a student AI agency. Your job is to make it demo-proof,
not to rebuild it. I'm learning, so explain every change in plain English.

## What the app does
- Students type or speak (mic) to the assistant. It ALWAYS replies in writing (no text-to-speech).
- Click-first (my call, Nov 2): every reply has buttons for the next step (repair -> what's broken ->
  which hall -> room). Tapped buttons skip the AI (instant, no tokens). Keep replies short;
  handbook answers are 1-3 sentences.
- Answers housing questions ONLY from the official 2026-27 Avila Housing Handbook, with page citations.
- Creates maintenance tickets: asks only for missing details (issue, building, room), then saves a ticket like AV-1001.
- Emergencies: safety message FIRST, then an emergency ticket. Who to call follows the handbook
  (printed p. 23) and my call on Nov 2: a real fire, someone hurt, gas or carbon monoxide -> 911 first,
  then Campus Safety at 816-985-6079. Smoke, sparks, flooding, beeping -> Campus Safety first, 911 if
  there are flames or someone is hurt. Fire alarm sounding -> leave the building first. Both numbers
  are always shown. Rules questions ("Can I smoke in my room?") and a beeping smoke detector get a
  one-line safety reminder and a normal reply (the detector becomes an urgent repair), not an emergency.
- Checks ticket status ("what's happening with AV-1003?").
- Replies in the student's language (Spanish, Urdu, etc.); tickets are saved in English for staff.
- Staff Dashboard (second page) auto-refreshes every 5s. It flags overdue tickets using the
  handbook's deadlines (urgent/emergency: 2 days, regular: 2 weeks).

## Stack (everything must stay FREE)
Python, Streamlit, Groq free tier (chat: openai/gpt-oss-120b, backup openai/gpt-oss-20b;
speech-to-text: whisper-large-v3-turbo), SQLite, rank_bm25 keyword search, pypdf. Windows 11 PC.

## File map
- app.py: front door, st.navigation with two pages
- views/student_chat.py: chat screen (mic + text input, ticket cards)
- views/staff_dashboard.py: live staff dashboard
- agent.py: the brain. ANALYZE (one AI call returns JSON: intent, language, English search
  keywords, ticket fields) then ACT (plain Python decides). 5 intents: question,
  maintenance, emergency, ticket_status, smalltalk.
- knowledge.py / ingest.py: handbook PDF -> 900-char chunks with page numbers -> data/knowledge.json -> BM25 search
- tickets.py: SQLite store, ticket codes, handbook deadlines
- voice.py: Groq Whisper transcription + silence/"phantom phrase" detection
- llm.py: the ONLY file that calls the AI; picks the first model this key can use from config.CHAT_MODELS
- config.py: every setting (buildings, phone numbers, models, deadlines)
- fake_llm.py: offline stand-in AI for tests (set FAKE_LLM=1)
- tests/test_flows.py, test.bat: offline tests for the demo flows (temporary database and log, never the real ones)
- online.py, run_online.bat: secure https link (Cloudflare quick tunnel) so phones can use the app and their mic
- notify.py: emails every new ticket to TICKET_EMAIL_TO (Gmail app password in .env); "python notify.py" sends a test
- check_setup.py, setup.bat, run.bat: Windows setup and pre-flight check
- BUILD_LOG.md: what we built, why, and what broke, per version
- data/conversation_log.jsonl: every turn (what was heard, what the AI understood, what we did). READ THIS FIRST when debugging.

## Rules (don't break these)
1. AI understands, Python decides. Don't hand control flow to the AI.
2. Keep both safety nets in agent.py: EMERGENCY_PATTERN and REPAIR_PATTERN. An emergency can never be downgraded.
   The AI alone can't call off a danger word; only the AI and the NOT_AN_EMERGENCY wording together can.
3. Never invent policies, fees or phone numbers. Answer only from handbook excerpts; otherwise point to HOUSING@avila.edu.
4. Never print, log, or commit the .env file or the Groq key.
5. Public info and fake demo data only. No real student records (FERPA).
6. Keep the app working at every step. Small changes, one Git commit each, clear messages.
   For each meaningful fix, add a BUILD_LOG.md entry (What / Why / What broke / Fix)
   and a git tag (v1.3-... and up).
7. Test before saying it's done: run the offline tests with FAKE_LLM=1 (streamlit.testing AppTest),
   then tell me exactly what to try in the real app.
   Command: `.venv\Scripts\python -m unittest discover -s tests -v` (or double-click test.bat).
   Add a test for every fix.

## Tasks, in order
Work through these one at a time and stop after each one so I can check it.
Ask me before anything big or hard to undo.

1. DONE (Nov 2): summarized how the app works in 10 bullet points for the presentation.
2. DONE except my own mic retest (see Current status): test the real flows with me and fix anything broken:
   a) "Can my friend stay over this weekend?" -> answer + page citation
   b) mic: "My heater isn't working" -> asks building + room -> ticket -> appears on the dashboard
   c) Spanish: "Mi baño tiene una fuga en Wylie 108" -> Spanish reply, English ticket
   d) "There's smoke coming from an outlet in Buchanan 302" -> Campus Safety first, 911 as the backup
   e) "What's the status of my ticket?" -> correct status
3. DONE (v1.5-printed-pages): citations use the number printed on each page (exactly 1 lower than
   the PDF page; Lockouts is printed p. 14, PDF p. 15). Chunks keep both: page and pdf_page.
4. DONE (v1.6-safe-json): gpt-oss reliability: make sure the ANALYZE call always returns valid JSON. If it doesn't,
   retry once, then fall back safely. Log failures.
5. DONE (v1.7-fast-failover): speed. Cause was waiting on a rate-limited model; now instant switch to the backup. 0.7-1.1 s per reply in a 10-question burst.
6. DONE (v1.8-demo-ready): Demo polish: a "Reset demo" button on the dashboard (clear tickets + load sample tickets),
   and make sure the dashboard looks clean on a projector.
7. DONE Nov 3 (Cloud copy freezes after a few minutes - use run_online.bat instead): LIVE at https://avila-housing.streamlit.app (public repo github.com/SenseiX619/avila-housing-assistant, key in Streamlit Secrets). Optional, only if 1-6 are solid: deploy free to Streamlit Community Cloud (key in Secrets,
   never in the repo) so teammates can open it on their phones.

## Current status
- Working: setup, handbook loading, Groq key, AI replies, chat screen, tickets, dashboard.
- Nov 2 (v1.3-safety-tiers): flows a-e all passed a dry run with the real AI and real Whisper
  (synthetic voice, temporary database). Emergency wording changed as described above.
- Nov 2 (v1.4-click-first): my first mic test failed with nothing logged. Mic checks fixed, every
  recording now logged (heard / loudness / mic_problem), buttons added for every step.
  This PC has two mics (laptop array + PS5 DualSense headset mic): check the browser's mic choice.
- Nov 3, 12 AM: a screenshot of my Chrome tab showed my first mic test DID record: the old (v1.2)
  warning "The recording was silent". The default mic is the laptop's Microphone Array (48%, not
  muted). The old check averaged loudness over the whole clip, so quiet speech with pauses looked
  silent; v1.4 uses the loudest moment. (Windows' mic history said Chrome hadn't used the mic since
  June; that turned out to be misleading.) Not retested yet: that tab still shows the old version
  ("File change - Rerun" at the top). Click Rerun or reload first.
- Nov 3: the LAPTOP mic itself is silent (Windows Sound meter does not move; recordings are exact 0.0). Not an app bug. Fix: headset, or deploy online and use a phone (QR code is built, v1.10-phone-qr). Deploy postponed.
- Known, for later tasks: (speed fixed in v1.7) free tier = 8,000 tokens/min PER MODEL; we switch models when one runs out.
