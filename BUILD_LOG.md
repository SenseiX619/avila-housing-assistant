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
