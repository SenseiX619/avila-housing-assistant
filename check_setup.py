"""
check_setup.py - checks every piece BEFORE you start the app, and tells you how to fix anything broken.

    python check_setup.py
"""

import sys

import config

ok = True


def report(passed: bool, label: str, fix: str = "") -> None:
    global ok
    print(("  [PASS] " if passed else "  [FAIL] ") + label)
    if not passed:
        ok = False
        print("         Fix: " + fix)


print("\nChecking the Avila Housing Assistant...\n")

# 1. Handbook knowledge base
report(config.KNOWLEDGE_FILE.exists(), "Housing Handbook is loaded",
       "run:  python ingest.py")

# 2. Groq key present
key = config.GROQ_API_KEY
report(bool(key) and "paste-your" not in key, "Groq API key found in .env",
       "open the .env file and paste your key after GROQ_API_KEY=  (get one free at console.groq.com)")

# 3. Groq actually answers
if ok:
    try:
        import llm
        answer = llm.chat([{"role": "user", "content": "Reply with just the word: ready"}], max_tokens=300)
        report("ready" in answer.lower(), f"Groq AI responds (using {llm.chat_models()[0]})",
               f"unexpected reply: {answer!r}")
    except Exception as err:
        report(False, "Groq AI responds", f"{err}  - check the key is correct and you're online")

# (No voice-output check: since v1.2 the assistant always answers in writing.)
print("  [TIP]  For the mic: make sure Windows uses the laptop microphone (Settings > System > Sound > Input)")

print("\nAll good! Starting the app...\n" if ok else "\nFix the [FAIL] items above, then try again.\n")
sys.exit(0 if ok else 1)
