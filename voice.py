"""
voice.py - speech in, speech out.

  ears:  student's recording  ->  Groq Whisper (free)  ->  text
  mouth: agent's reply text   ->  edge-tts (free)      ->  mp3 audio

WHY a separate file: voice is just another "door" into the same agent brain.
The agent never knows (or cares) whether a message was typed or spoken.
If the voice part ever fails, the app keeps working as a normal text chat.
"""

import asyncio
import re

import config
import llm


def transcribe(audio_bytes: bytes, filename: str = "speech.wav") -> str:
    """Turn a voice recording into text. Whisper detects the language automatically."""
    if config.FAKE_LLM:
        return "The heater in my room is not working"
    result = llm._groq().audio.transcriptions.create(
        file=(filename, audio_bytes), model=config.SPEECH_MODEL)
    return (result.text or "").strip()


def _speakable(text: str) -> str:
    """Clean text so it sounds natural read aloud."""
    text = re.sub(r"[*_#`>]", "", text)                       # markdown symbols
    text = re.sub(r"\(Handbook p\. ?(\d+)\)", r"(handbook page \1)", text)
    text = re.sub(r"\bAV-(\d)(\d)(\d)(\d)\b", r"A V \1 \2 \3 \4", text)   # say ticket IDs digit by digit
    return text


def speak(text: str) -> bytes | None:
    """Turn the agent's reply into mp3 audio. Returns None if it fails (the app then stays text-only)."""
    try:
        import edge_tts

        async def _run() -> bytes:
            audio = bytearray()
            async for chunk in edge_tts.Communicate(_speakable(text), config.TTS_VOICE).stream():
                if chunk["type"] == "audio":
                    audio.extend(chunk["data"])
            return bytes(audio)

        audio = asyncio.run(_run())
        return audio or None
    except Exception:
        return None
