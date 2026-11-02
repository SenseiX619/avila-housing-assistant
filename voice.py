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


# What Whisper tends to "hear" in silence or noise. If that's all we got, the mic didn't pick up speech.
WHISPER_GHOSTS = {"", "you", "thank you", "thank you.", "thanks for watching", "thanks for watching!",
                  "thank you for watching", "thank you for watching.", "bye", "bye.", ".", "so", "okay."}

# Words Whisper might not know - helps it spell building names right.
WHISPER_HINT = ("Avila University housing. Thompson Hall, Wylie Hall, Buchanan Hall, Villa Ventura. "
                "Room 214. My heater isn't working. The sink is leaking.")


class NoSpeechHeard(Exception):
    """The recording was silent, or Whisper only 'heard' noise."""


def loudness(wav_bytes: bytes) -> float:
    """How loud the recording is, 0 (silent) to 1 (max). Lets us tell the student their mic isn't picking up."""
    try:
        import array, io, wave
        with wave.open(io.BytesIO(wav_bytes)) as w:
            if w.getsampwidth() != 2:
                return 1.0
            samples = array.array("h", w.readframes(w.getnframes()))
        if not samples:
            return 0.0
        return (sum(s * s for s in samples[::4]) / len(samples[::4])) ** 0.5 / 32768
    except Exception:
        return 1.0                       # can't measure -> don't block


def transcribe(audio_bytes: bytes, filename: str = "speech.wav") -> str:
    """Turn a voice recording into text. Whisper detects the language automatically."""
    if config.FAKE_LLM:
        return "The heater in my room is not working"
    if loudness(audio_bytes) < 0.003:
        raise NoSpeechHeard("The recording was silent - your microphone may be muted or the wrong one is selected.")
    result = llm._groq().audio.transcriptions.create(
        file=(filename, audio_bytes), model=config.SPEECH_MODEL, prompt=WHISPER_HINT, temperature=0)
    text = (result.text or "").strip()
    if text.lower().strip(" !?") in {g.strip(" !?") for g in WHISPER_GHOSTS}:
        raise NoSpeechHeard(f'I only heard "{text}" - please try again and speak a bit closer to the mic.')
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
