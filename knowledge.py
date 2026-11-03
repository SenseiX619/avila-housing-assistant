"""
knowledge.py - turns the Housing Handbook into something the agent can search.

PIPELINE:  PDF  ->  text per page  ->  small overlapping chunks  ->  BM25 search index

WHY BM25 (keyword search) instead of embeddings/vector DB?
- Zero downloads, zero GPU, runs instantly on any laptop -> nothing to break on demo day.
- A handbook is full of exact policy words ("lockout", "guests", "quiet hours"),
  which keyword search handles really well.
- The agent rewrites every student question into English keywords first
  (see agent.py), so even a Spanish or Urdu question finds the right page.
- Upgrade path for v2: swap the Retriever class for embeddings. Nothing else changes.
"""

import json
import re
from pathlib import Path

from pypdf import PdfReader
from rank_bm25 import BM25Okapi

CHUNK_SIZE = 900      # characters per chunk: big enough to hold one policy, small enough to stay focused
CHUNK_OVERLAP = 150   # overlap so a sentence cut at a boundary still appears whole in one chunk

STOPWORDS = set("""a an the and or of to in on for at by with is are was were be been it this that
these those i you your my we our they their he she his her as from can do does if not no yes
what when where who how which will would should could may might must about into than then
there here also any all each per its me am""".split())


def pdf_to_chunks(pdf_path: Path, source_name: str) -> list[dict]:
    """Read a PDF and split it into chunks that remember their page number (for citations).

    "page" is the number PRINTED on the page (what a student sees), read from the page's first line.
    In the 2026-27 handbook it is one less than the PDF page number (the cover has no number).
    Pages without a printed number keep the PDF page number. "pdf_page" is always the PDF's own count."""
    reader = PdfReader(str(pdf_path))
    chunks = []
    for page_index, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        text = re.sub(r"\.{4,}", " ", text)        # remove "......" leaders from the table of contents
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text).strip()
        printed = re.match(r"(\d{1,3})\s*\n", text)   # the printed page number sits on the first line
        if printed:
            text = text[printed.end():]
        if len(text) < 40:                          # skip empty / cover pages
            continue
        page_number = int(printed.group(1)) if printed else page_index + 1
        start = 0
        while start < len(text):
            piece = text[start:start + CHUNK_SIZE].strip()
            if piece:
                chunks.append({"source": source_name, "page": page_number, "pdf_page": page_index + 1,
                               "text": piece})
            start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


# How students talk  ->  how the handbook words it
PHRASES = {
    "locked out": "lockout", "lock out": "lockout", "lost my key": "lockout key",
    "visitor": "guest", "girlfriend": "guest", "boyfriend": "guest", "friend stay": "guest",
    "noise": "quiet", "loud": "quiet",
    "broken": "repair maintenance", "fix": "repair maintenance", "work order": "maintenance",
    "move out": "check-out", "moving out": "check-out",
}


def tokenize(text: str) -> list[str]:
    """Lowercase words, map student slang to handbook terms, drop filler words, cut plural 's'."""
    text = text.lower()
    for phrase, replacement in PHRASES.items():
        text = text.replace(phrase, f" {replacement} ")
    words = re.findall(r"[a-z0-9]+", text)
    tokens = []
    for w in words:
        if w in STOPWORDS or len(w) < 2:
            continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        tokens.append(w)
    return tokens


def save_chunks(chunks: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(chunks, indent=1, ensure_ascii=False), encoding="utf-8")


class Retriever:
    """Searches the handbook chunks and returns the best matches."""

    def __init__(self, knowledge_file: Path):
        self.chunks = []
        if Path(knowledge_file).exists():
            self.chunks = json.loads(Path(knowledge_file).read_text(encoding="utf-8"))
        self.bm25 = BM25Okapi([tokenize(c["text"]) for c in self.chunks]) if self.chunks else None

    @property
    def ready(self) -> bool:
        return self.bm25 is not None

    def search(self, query: str, k: int = 4) -> list[dict]:
        if not self.ready:
            return []
        scores = self.bm25.get_scores(tokenize(query))
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [dict(self.chunks[i], score=round(float(scores[i]), 2))
                for i in ranked[:k] if scores[i] > 0]
