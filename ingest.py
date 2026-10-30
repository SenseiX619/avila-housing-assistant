"""
ingest.py - run this ONCE (and again whenever the handbook changes).

    python ingest.py

1. Downloads Avila's public Housing Handbook PDF into data/docs/
2. Also picks up any other PDF you drop into data/docs/ (e.g. a move-in guide)
3. Splits everything into searchable chunks -> data/knowledge.json
"""

import sys

import requests

import config
from knowledge import pdf_to_chunks, save_chunks


def download_handbook() -> None:
    target = config.DOCS_DIR / "housing-handbook.pdf"
    if target.exists():
        print(f"[ok] Handbook already downloaded: {target.name}")
        return
    print("[..] Downloading the Avila Housing Handbook ...")
    try:
        resp = requests.get(config.HANDBOOK_URL, timeout=30,
                            headers={"User-Agent": "Mozilla/5.0 (AvilaHousingAssistant student project)"})
        resp.raise_for_status()
        if not resp.content.startswith(b"%PDF"):
            raise ValueError("the download was not a PDF")
        target.write_bytes(resp.content)
        print(f"[ok] Saved {len(resp.content) // 1024} KB -> {target}")
    except Exception as err:
        print(f"[!!] Could not download the handbook automatically ({err}).")
        print("     Fix: open this link in your browser, save the PDF into the data/docs folder,")
        print(f"     then run ingest.py again:\n     {config.HANDBOOK_URL}")


def main() -> None:
    config.DOCS_DIR.mkdir(parents=True, exist_ok=True)
    download_handbook()

    pdfs = sorted(config.DOCS_DIR.glob("*.pdf"))
    if not pdfs:
        print("[!!] No PDFs in data/docs - nothing to index.")
        sys.exit(1)

    all_chunks = []
    for pdf in pdfs:
        name = config.HANDBOOK_NAME if pdf.name == "housing-handbook.pdf" else pdf.stem.replace("-", " ").title()
        chunks = pdf_to_chunks(pdf, name)
        print(f"[ok] {pdf.name}: {len(chunks)} chunks")
        all_chunks.extend(chunks)

    save_chunks(all_chunks, config.KNOWLEDGE_FILE)
    print(f"[done] {len(all_chunks)} chunks saved to {config.KNOWLEDGE_FILE}")


if __name__ == "__main__":
    main()
