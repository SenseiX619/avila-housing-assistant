"""
llm.py - the ONLY file that talks to the AI model.

WHY keep it in one place: if we ever switch from Groq to Claude, Gemini or OpenAI,
we change this file and nothing else. The rest of the app just calls chat() / chat_json().
"""

import json
import re
import time

import config

_client = None


def _groq():
    global _client
    if _client is None:
        if not config.GROQ_API_KEY or "paste-your" in config.GROQ_API_KEY:
            raise RuntimeError("No Groq API key found. Add GROQ_API_KEY to your .env file.")
        from groq import Groq
        # max_retries=0: when a model has used up its free tokens for the minute, don't wait and retry
        # it (that caused 5-14 second replies) - chat() moves straight on to the next model instead.
        # timeout=15: a reply normally takes under 2 seconds, so don't hang a minute on a stuck call.
        _client = Groq(api_key=config.GROQ_API_KEY, max_retries=0, timeout=15)
    return _client


# Free tier: EACH model gets its own 8,000 tokens per minute. When one runs out, it "rests" until
# Groq says it's ready again, and the other models take over - about 3x the capacity of one model.
_resting_until: dict[str, float] = {}


def _rest_seconds(err: Exception) -> float:
    try:
        return min(60.0, float(err.response.headers.get("retry-after", 10)))
    except Exception:
        return 10.0


_available_models = None


def chat_models() -> list[str]:
    """The models from config.CHAT_MODELS that this API key can actually use (asked once, then cached)."""
    global _available_models
    if _available_models is None:
        try:
            live = {m.id for m in _groq().models.list().data}
            _available_models = [m for m in config.CHAT_MODELS if m in live] or list(config.CHAT_MODELS)
        except RuntimeError:
            raise
        except Exception:
            _available_models = list(config.CHAT_MODELS)   # couldn't check - just try them in order
    return _available_models


def chat(messages: list[dict], json_mode: bool = False,
         temperature: float = 0.2, max_tokens: int = 1500) -> str:
    """Send messages to the model and return its text. Falls back to the next model on errors."""
    if config.FAKE_LLM:
        import fake_llm
        return fake_llm.respond(messages, json_mode)

    last_error = None
    ready = [m for m in chat_models() if _resting_until.get(m, 0) <= time.time()] or chat_models()
    for model in ready:
        try:
            kwargs = dict(model=model, messages=messages,
                          temperature=temperature, max_tokens=max_tokens)
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            if model.startswith("openai/gpt-oss"):
                # gpt-oss "thinks" before answering. Low effort = faster replies, and the
                # thinking can't use up the token budget before the real answer is written.
                kwargs["extra_body"] = {"reasoning_effort": "low"}
            resp = _groq().chat.completions.create(**kwargs)
            text = resp.choices[0].message.content or ""
            if text.strip():
                return text
            last_error = f"{model} returned an empty reply"
        except RuntimeError:
            raise                      # missing key: no point trying another model
        except Exception as err:       # rate limit / model busy / model retired -> try the next one
            if getattr(err, "status_code", None) == 429:          # out of free tokens for this minute
                _resting_until[model] = time.time() + _rest_seconds(err)
            last_error = err
    raise RuntimeError(f"The AI service is unavailable right now: {last_error}")


class BadJSON(Exception):
    """The AI didn't return a JSON object, even after one retry."""


def _parse_json(raw: str) -> dict | None:
    """The JSON object in the reply (tolerates stray text around it), or None."""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.S)
        try:
            value = json.loads(match.group(0)) if match else None
        except json.JSONDecodeError:
            value = None
    return value if isinstance(value, dict) else None


def chat_json(messages: list[dict], problems: list | None = None) -> dict:
    """Ask for a JSON answer and return it as a dict. If the reply isn't a valid JSON object, ask once
    more; if that fails too, raise BadJSON. Anything that went wrong is added to `problems`."""
    problems = [] if problems is None else problems
    for _ in range(2):
        raw = chat(messages, json_mode=True, temperature=0)
        result = _parse_json(raw)
        if result is not None:
            return result
        problems.append(f"not a JSON object: {raw[:200]!r}")
    raise BadJSON("; ".join(problems))
