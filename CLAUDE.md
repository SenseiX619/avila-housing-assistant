# Avila Housing Assistant - brief for Claude Code

You're joining an existing project: the Avila Housing Assistant, an AI agent for Avila
University (Kansas City) housing. My team presents it to the university on Wednesday,
Nov 4, 2026, to get approved as a student AI agency. Your job is to make it demo-proof,
not to rebuild it. I'm learning, so explain every change in plain English.

## What the app does
- Students type or speak (mic) to the assistant. It ALWAYS replies in writing (no text-to-speech).
- Answers housing questions ONLY from the official 2026-27 Avila Housing Handbook, with page citations.
- Creates maintenance tickets: asks only for missing details (issue, building, room), then saves a ticket like AV-1001.
- Emergencies (fire, smoke, gas, flooding, sparks, someone hurt): tells them to call 911 and
  Campus Safety at 816-985-6079 FIRST, then logs an emergency ticket.
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
- check_setup.py, setup.bat, run.bat: Windows setup and pre-flight check
- BUILD_LOG.md: what we built, why, and what broke, per version
- data/conversation_log.jsonl: every turn (what was heard, what the AI understood, what we did). READ THIS FIRST when debugging.

## Rules (don't break these)
1. AI understands, Python decides. Don't hand control flow to the AI.
2. Keep both safety nets in agent.py: EMERGENCY_PATTERN and REPAIR_PATTERN. An emergency can never be downgraded.
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
2. IN PROGRESS: test the real flows with me and fix anything broken:
   a) "Can my friend stay over this weekend?" -> answer + page citation
   b) mic: "My heater isn't working" -> asks building + room -> ticket -> appears on the dashboard
   c) Spanish: "Mi baño tiene una fuga en Wylie 108" -> Spanish reply, English ticket
   d) "There's smoke coming from an outlet in Buchanan 302" -> 911 + Campus Safety first
   e) "What's the status of my ticket?" -> correct status
3. Page numbers: citations currently use PDF page numbers, but the handbook's printed page
   numbers are about 2 lower (the Lockouts section is printed as p.13, but it's PDF page 15).
   Make citations match what a student sees, or say "PDF page", and explain the choice.
4. gpt-oss reliability: make sure the ANALYZE call always returns valid JSON. If it doesn't,
   retry once, then fall back safely. Log failures.
5. Speed: if a reply takes more than ~4 seconds, find out why and fix it.
6. Demo polish: a "Reset demo" button on the dashboard (clear tickets + load sample tickets),
   and make sure the dashboard looks clean on a projector.
7. Optional, only if 1-6 are solid: deploy free to Streamlit Community Cloud (key in Secrets,
   never in the repo) so teammates can open it on their phones.

## Current status
- Working: setup, handbook loading, Groq key, AI replies, chat screen, tickets, dashboard.
- v1.2 fixed voice messages getting the greeting instead of becoming tickets. NOT yet
  confirmed with a real voice test. Check data/conversation_log.jsonl after I test.
