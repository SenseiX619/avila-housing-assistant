"""
config.py - every setting for the Avila Housing Assistant lives here.

WHY one config file: when Avila (or a future client) wants a change - a new
building, a different phone number, another AI model - we edit ONE file
instead of hunting through the code. This is also what makes the agent
reusable: swap these values and it works for a different school or business.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Read secrets (API keys) from the hidden .env file. Never hard-code keys.
load_dotenv()

# ---------- Paths ----------
ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
DOCS_DIR = DATA_DIR / "docs"            # source PDFs (the housing handbook)
KNOWLEDGE_FILE = DATA_DIR / "knowledge.json"  # handbook split into searchable chunks
# SQLite database for maintenance tickets. AVILA_DB_FILE can point a practice copy of the app at a
# separate database, so trying things out never touches the real demo tickets.
DB_FILE = Path(os.getenv("AVILA_DB_FILE") or DATA_DIR / "tickets.db")

# ---------- Source documents (public Avila pages only) ----------
HANDBOOK_URL = "https://www.avila.edu/wp-content/uploads/2026/09/Housing-Handbook-2026-2027.pdf"
HANDBOOK_NAME = "Avila Housing Handbook 2026-2027"

# ---------- Avila housing facts (from the 2026-27 Housing Handbook) ----------
BUILDINGS = ["Buchanan Hall", "Wylie Hall", "Thompson Hall", "Villa Ventura"]
CAMPUS_SAFETY_PHONE = "816-985-6079"
HOUSING_EMAIL = "HOUSING@avila.edu"

# Handbook: regular repairs should be done within 2 weeks, emergency repairs within 2 days.
# The staff dashboard flags tickets that pass these deadlines.
SLA_DAYS = {"emergency": 2, "urgent": 2, "normal": 14}

CATEGORIES = ["Plumbing", "Electrical", "Heating/AC", "Appliance", "Door/Lock",
              "Furniture", "Pest", "Internet", "Other"]

# ---------- AI models (all on Groq's free tier) ----------
def _secret(name: str, default: str = "") -> str:
    """Read a secret from .env, or from Streamlit Cloud's secrets when deployed."""
    value = os.getenv(name)
    if value:
        return value
    try:
        import streamlit as st
        return st.secrets.get(name, default)
    except Exception:
        return default


GROQ_API_KEY = _secret("GROQ_API_KEY")
# Models to try, best first. On startup the app asks Groq which models this key can
# actually use and skips the rest - so if Groq renames or retires one, nothing breaks.
# (Nov 2026: Groq moved the Llama models to Enterprise-only, so free keys use gpt-oss.)
CHAT_MODELS = [
    "openai/gpt-oss-120b",        # main brain: smartest free model
    "openai/gpt-oss-20b",         # backup: smaller, used if the main one is busy
    "qwen/qwen3.8-27b",           # preview model, last resort
    "llama-3.3-70b-versatile",    # only works on Enterprise keys
]
SPEECH_MODEL = "whisper-large-v3-turbo"     # voice -> text

# FAKE_LLM=1 runs the app with a simple rule-based stand-in instead of Groq.
# We use it for testing without internet. Never use it for the real demo.
FAKE_LLM = os.getenv("FAKE_LLM", "0") == "1"
