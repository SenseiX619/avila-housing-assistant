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


# What Whisper tends to "hear" in silence or noise ("Thank you.", "you you", "¡Gracias!"). If a recording
# turns into nothing but these words, the mic didn't pick up speech.
GHOST_WORDS = {"thank", "thanks", "you", "for", "watching", "bye", "so", "okay", "ok", "gracias"}

# Words Whisper might not know - helps it spell building names right. Names only: example sentences
# here can be echoed back by Whisper when a recording is unclear.
WHISPER_HINT = "Avila University housing: Thompson Hall, Wylie Hall, Buchanan Hall, Villa Ventura, room, ticket AV."

SILENT = 0.003          # loudness below this = the mic heard nothing


class NoSpeechHeard(Exception):
    """The recording was silent, or Whisper only 'heard' noise."""


def loudness(wav_bytes: bytes) -> float:
    """How loud the loudest moment of the recording is, 0 (silent) to 1 (max). Measured in 0.1-second
    slices, so a pause before or after speaking doesn't count against the student."""
    try:
        import array, io, wave
        with wave.open(io.BytesIO(wav_bytes)) as w:
            if w.getsampwidth() != 2:
                return 1.0
            slice_size = max(1, w.getframerate() * w.getnchannels() // 10)
            samples = array.array("h", w.readframes(w.getnframes()))
        loudest = 0.0
        for start in range(0, len(samples), slice_size):
            part = samples[start:start + slice_size:4]
            if part:
                loudest = max(loudest, (sum(s * s for s in part) / len(part)) ** 0.5)
        return loudest / 32768
    except Exception:
        return 1.0                       # can't measure -> don't block


def is_ghost(text: str) -> bool:
    words = re.findall(r"[a-z']+", text.lower())
    return not words or all(w in GHOST_WORDS for w in words) or "amara.org" in text.lower()


def transcribe(audio_bytes: bytes, filename: str = "speech.wav") -> str:
    """Turn a voice recording into text. Whisper detects the language automatically."""
    if loudness(audio_bytes) < SILENT:
        raise NoSpeechHeard("I couldn't hear anything. Check that the right microphone is selected (click the "
                            "mic icon in your browser's address bar), then try again - or just type.")
    if config.FAKE_LLM:
        return "The heater in my room is not working"
    result = llm._groq().audio.transcriptions.create(
        file=(filename, audio_bytes), model=config.SPEECH_MODEL, prompt=WHISPER_HINT, temperature=0)
    text = (result.text or "").strip()
    if is_ghost(text):
        heard = f'only caught "{text}"' if text else "didn't catch any words"
        raise NoSpeechHeard(f"I {heard}. Check the right microphone is selected, speak close to it, "
                            f"then tap stop - or just type.")
    return text


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
