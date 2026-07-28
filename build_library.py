#!/usr/bin/env python3
"""Build one self-contained folder holding every recording beside its transcript.

The pipeline keeps audio in `originals/` and text in `transcripts/` because the
stages need them separate. This assembles a single browsable folder where each
recording sits next to its own transcript:

    library/
        20260617 120121.m4a
        20260617 120121.txt
        _index.csv
        _README.txt

Audio is hard-linked, not copied, so the folder costs no additional disk space —
the same bytes appear under two names. Deleting either name leaves the other
intact. Files fall back to a real copy if hard-linking is unavailable.

Filenames are left exactly as the phone recorded them. Human-readable renaming
happens later, once the naming convention is approved.

Usage:
    python build_library.py [--data DIR] [--copy] [--clean]
"""

import argparse
import csv
import os
import shutil
import sys
from pathlib import Path

DEFAULT_DATA = Path(r"C:\Users\ezras\memo-machine-data")
AUDIO_EXTS = {".m4a", ".qta", ".wav", ".mp3", ".aifc", ".aiff", ".caf"}

README = """\
Voice Memo Library
==================

Every recording from the phone, each beside its transcript.

  <name>.m4a / .qta   the original audio, byte-for-byte off the device
  <name>.txt          its transcript
  _index.csv          one row per recording: title, date, duration, transcript source
  _README.txt         this file

Audio files here are hard links to ../originals/. They are the same bytes on
disk under two names, so this folder adds no extra disk usage, and deleting
files here does not free space while ../originals/ still exists.

Transcript sources:
  embedded  Apple's own on-device transcript, read out of the audio file itself
  whisper   transcribed locally, for recordings Apple never processed
  none      no transcript available (silent, zero-byte, or too short)

Every file was verified by SHA-256 against the device backup at extraction time.
Hashes are in ../inventory.csv.
"""


def link_or_copy(src: Path, dst: Path, force_copy: bool) -> str:
    if dst.exists():
        return "present"
    if not force_copy:
        try:
            os.link(src, dst)
            return "linked"
        except OSError:
            pass  # different volume, or filesystem without hard links
    shutil.copy2(src, dst)
    return "copied"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--copy", action="store_true",
                    help="copy audio instead of hard-linking (uses full disk space)")
    ap.add_argument("--clean", action="store_true",
                    help="empty the library folder first")
    args = ap.parse_args()

    originals = args.data / "originals"
    transcripts = args.data / "transcripts"
    library = args.data / "library"

    if args.clean and library.exists():
        shutil.rmtree(library)
    library.mkdir(parents=True, exist_ok=True)

    # Metadata gathered by the earlier stages, keyed by filename.
    inv = {}
    inv_path = args.data / "inventory.csv"
    if inv_path.is_file():
        with open(inv_path, encoding="utf-8") as fh:
            inv = {r["filename"]: r for r in csv.DictReader(fh)}
    tidx = {}
    tidx_path = args.data / "transcript_index.csv"
    if tidx_path.is_file():
        with open(tidx_path, encoding="utf-8") as fh:
            tidx = {r["filename"]: r for r in csv.DictReader(fh)}

    audio_files = sorted(p for p in originals.iterdir()
                         if p.suffix.lower() in AUDIO_EXTS)
    rows, stats = [], {"linked": 0, "copied": 0, "present": 0}
    with_text = 0

    for audio in audio_files:
        how = link_or_copy(audio, library / audio.name, args.copy)
        stats[how] += 1

        txt = transcripts / f"{audio.stem}.txt"
        has_text = txt.is_file()
        if has_text:
            shutil.copy2(txt, library / txt.name)
            with_text += 1

        meta = inv.get(audio.name, {})
        tmeta = tidx.get(audio.name, {})
        rows.append({
            "audio": audio.name,
            "transcript": txt.name if has_text else "",
            "title": meta.get("title", ""),
            "recorded": meta.get("recorded", ""),
            "duration_s": meta.get("duration_s", ""),
            "mb": round(audio.stat().st_size / 1e6, 2),
            "transcript_source": tmeta.get("source", "none"),
            "transcript_chars": tmeta.get("chars", 0),
            "sha256": meta.get("sha256", ""),
        })

    rows.sort(key=lambda r: r["recorded"] or "")
    with open(library / "_index.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    (library / "_README.txt").write_text(README, encoding="utf-8")

    total_mb = sum(r["mb"] for r in rows)
    hours = sum(float(r["duration_s"] or 0) for r in rows) / 3600
    print(f"library: {library}")
    print(f"  recordings       {len(rows)}  ({total_mb/1000:.2f} GB, {hours:.1f} hours)")
    print(f"  with transcript  {with_text}")
    print(f"  hard-linked      {stats['linked']}")
    print(f"  copied           {stats['copied']}")
    print(f"  already present  {stats['present']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
