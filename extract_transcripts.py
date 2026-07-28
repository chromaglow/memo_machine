#!/usr/bin/env python3
"""Module 5 — write one transcript per recording, from whichever source has it.

Two sources, one output format:
  embedded  Apple's on-device transcript, read straight out of the audio file
  whisper   locally transcribed by whisper_fallback.py for the few Apple missed

Reads `originals/`, writes `transcripts/<stem>.txt` plus `<stem>.words.json`
(word-level timings), and an index recording which source each came from.

Recordings with no transcript from either source are listed in
`needs_attention.txt` and do not stop the run.

Usage:
    python extract_transcripts.py [--data DIR] [--force]
"""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase0_check_tsrp import (  # noqa: E402
    find_tsrp_payload, load_metadata_region, parse_tsrp,
)

DEFAULT_DATA = Path(r"C:\Users\ezras\memo-machine-data")
AUDIO_EXTS = {".m4a", ".qta", ".wav", ".mp3", ".aifc", ".aiff", ".caf"}


def embedded_transcript(audio: Path):
    """(text, timings) from the file's own metadata, or (None, None)."""
    payload, _ = find_tsrp_payload(load_metadata_region(audio))
    if payload is None:
        return None, None
    try:
        text, timings = parse_tsrp(payload)
    except Exception:
        return None, None
    return (text, timings) if text.strip() else (None, None)


def whisper_transcript(stem: str, manual_dir: Path):
    """(text, timings) from a whisper_fallback.py run, or (None, None)."""
    txt = manual_dir / f"{stem}.txt"
    if not txt.is_file():
        return None, None
    text = txt.read_text(encoding="utf-8")
    if not text.strip():
        return None, None
    words_file = manual_dir / f"{stem}.words.json"
    timings = []
    if words_file.is_file():
        try:
            timings = json.loads(words_file.read_text(encoding="utf-8"))
        except Exception:
            timings = []
    return text, timings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--force", action="store_true",
                    help="rewrite transcripts that already exist")
    args = ap.parse_args()

    originals = args.data / "originals"
    out_dir = args.data / "transcripts"
    manual_dir = args.data / "manual-transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)

    audio_files = sorted(p for p in originals.iterdir()
                         if p.suffix.lower() in AUDIO_EXTS)
    print(f"recordings: {len(audio_files)}\n")

    index, counts, missing = [], {"embedded": 0, "whisper": 0, "none": 0}, []
    total_chars = total_words = 0

    for i, audio in enumerate(audio_files, 1):
        stem = audio.stem
        try:
            text, timings = embedded_transcript(audio)
            source = "embedded"
            if text is None:
                text, timings = whisper_transcript(stem, manual_dir)
                source = "whisper"
            if text is None:
                source = "none"
                counts["none"] += 1
                missing.append(audio.name)
            else:
                counts[source] += 1
                total_chars += len(text)
                total_words += len(timings or [])
                txt_path = out_dir / f"{stem}.txt"
                if args.force or not txt_path.is_file():
                    txt_path.write_text(text, encoding="utf-8")
                    (out_dir / f"{stem}.words.json").write_text(
                        json.dumps(timings or [], ensure_ascii=False),
                        encoding="utf-8")
            index.append({
                "filename": audio.name,
                "transcript": f"{stem}.txt" if source != "none" else "",
                "source": source,
                "chars": len(text) if text else 0,
                "word_timings": len(timings or []),
            })
        except Exception as exc:  # never let one file end the run
            print(f"  FAILED {audio.name}: {exc}")
            index.append({"filename": audio.name, "transcript": "",
                          "source": "error", "chars": 0, "word_timings": 0})
        if i % 50 == 0 or i == len(audio_files):
            print(f"  {i}/{len(audio_files)}", flush=True)

    idx_path = args.data / "transcript_index.csv"
    with open(idx_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["filename", "transcript", "source", "chars", "word_timings"])
        writer.writeheader()
        writer.writerows(index)

    if missing:
        (args.data / "needs_attention.txt").write_text(
            "\n".join(missing), encoding="utf-8")

    print(f"\n--- transcript summary ---")
    print(f"  embedded (Apple)  {counts['embedded']}")
    print(f"  whisper (local)   {counts['whisper']}")
    print(f"  no transcript     {counts['none']}")
    print(f"  total text        {total_chars:,} chars")
    print(f"  word timings      {total_words:,}")
    print(f"  index             {idx_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
