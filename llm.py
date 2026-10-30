"""
llm.py - the ONLY file that talks to the AI model.

WHY keep it in one place: if we ever switch from Groq to Claude, Gemini or OpenAI,
we change this file and nothing else. The rest of the app just calls chat() / chat_json().
"""

import json
import re

import config

_client = None


def _groq():
    global _client
    if _client is None:
        if not config.GROQ_API_KEY or "paste-your" in config.GROQ_API_KEY:
            raise RuntimeError("No Groq API key found. Add GROQ_API_KEY to your .env file.")
        from groq import Groq
        _client = Groq(api_key=config.GROQ_API_KEY)
    return _client


def chat(messages: list[dict], json_mode: bool = False,
         temperature: float = 0.2, max_tokens: int = 500) -> str:
    """Send messages to the model and return its text. Falls back to the backup model on errors."""
    if config.FAKE_LLM:
        import fake_llm
        return fake_llm.respond(messages, json_mode)

    last_error = None
    for model in (config.CHAT_MODEL, config.BACKUP_MODEL):
        try:
            kwargs = dict(model=model, messages=messages,
                          temperature=temperature, max_tokens=max_tokens)
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            resp = _groq().chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""
        except RuntimeError:
            raise                      # missing key: no point trying the backup
        except Exception as err:       # rate limit / model busy -> try the backup model
            last_error = err
    raise RuntimeError(f"The AI service is unavailable right now: {last_error}")


def chat_json(messages: list[dict]) -> dict:
    """Ask for a JSON answer and turn it into a Python dict (tolerates stray text around it)."""
    raw = chat(messages, json_mode=True, temperature=0)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.S)
        return json.loads(match.group(0)) if match else {}
