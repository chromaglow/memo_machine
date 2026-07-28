#!/usr/bin/env python3
"""Local Whisper transcription for recordings Apple never transcribed on-device.

Deliberately NOT part of the main pipeline. 211 of 243 recordings carry an
embedded `tsrp` transcript for free; this covers the handful that don't and
that are long enough to be worth recovering.

Usage:
    python whisper_fallback.py <audio> [<audio> ...] [--model large-v3] [--out DIR]

Writes, per input, into the output directory (default: manual-transcripts/):
    <stem>.txt         plain transcript text
    <stem>.words.json  [[word, start, end], ...]

The word-timing file mirrors what `parse_tsrp` yields for embedded transcripts,
so downstream stages consume both sources through one code path.
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_OUT = Path(r"C:\Users\ezras\memo-machine-data\manual-transcripts")

# Phrases Whisper emits over silence that come from its training data, not the
# recording. Dropping them outright is safe: nobody says "thank you for
# watching" into a private voice memo, and leaving them in would feed invented
# content to the summarizer.
HALLUCINATION_PHRASES = {
    "thank you for watching.", "thanks for watching.", "thank you for watching!",
    "please subscribe.", "like and subscribe.", "subtitles by the amara.org community",
    "www.mooji.org", "you", "bye.",
}


def clean_transcript(text: str) -> tuple[str, int]:
    """Strip Whisper's silence hallucinations. Returns (cleaned, chars_removed).

    Two artifacts, both concentrated in quiet stretches: stock phrases lifted
    from training data, and the same short sentence looping dozens of times.
    A single "Okay." is real speech, so only *consecutive* repeats collapse —
    a global unique-ify would eat genuine repeated agreement.
    """
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    kept, prev = [], None
    for sentence in sentences:
        if sentence.lower() in HALLUCINATION_PHRASES:
            continue
        if sentence == prev:  # loop artifact
            continue
        kept.append(sentence)
        prev = sentence
    cleaned = " ".join(kept)
    return cleaned, len(text) - len(cleaned)


def to_wav(src: Path, normalize: bool = True) -> Path:
    """Decode to 16 kHz mono WAV via ffmpeg.

    Pre-converting rather than letting the model open the file directly keeps
    `.qta` (QuickTime) inputs on the same path as `.m4a`, instead of relying on
    whatever container support the audio backend happens to ship with. It also
    pins stream selection: a `.qta` can carry a second multi-channel stream in a
    codec ffmpeg cannot identify, and `-map 0:a:0` keeps that from being chosen.

    Loudness normalization matters more than it looks. These recordings run as
    quiet as -42 dB mean (phone on a table, across a room); normalizing recovers
    materially more speech than feeding the raw levels to the model.
    """
    tmp = Path(tempfile.gettempdir()) / f"{src.stem}.whisper.wav"
    cmd = ["ffmpeg", "-nostdin", "-y", "-i", str(src), "-map", "0:a:0",
           "-ac", "1", "-ar", "16000", "-vn"]
    if normalize:
        cmd += ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]
    cmd.append(str(tmp))
    subprocess.run(cmd, check=True, capture_output=True)
    return tmp


def transcribe(model, src: Path, out_dir: Path, vad: bool = False,
               normalize: bool = True, keep_low_confidence: bool = False) -> dict:
    wav = to_wav(src, normalize=normalize)
    segments, info = model.transcribe(
        str(wav),
        language="en",          # every embedded transcript reports en_US
        beam_size=5,
        # VAD is off by default: on a quiet 86-minute recording it silently
        # discarded ~36% of recoverable speech versus normalize-and-decode-all.
        # It only saves compute, and these are one-off fallback runs.
        vad_filter=vad,
        word_timestamps=True,
        # Without this the model feeds its own output back as context, so one
        # hallucinated "Thank you." over silence begets a hundred more.
        condition_on_previous_text=False,
    )

    parts, words, dropped = [], [], 0
    for seg in segments:
        # Drop hallucinations at the source rather than pattern-matching them
        # later. Thresholds were tuned by dumping 404 segments of a very quiet
        # recording and measuring which statistics actually separate invented
        # stock phrases from real speech:
        #
        #   compression_ratio  junk median 0.56 vs real 1.48  <- strong signal
        #   no_speech_prob     junk median 0.89 vs real 0.79  <- weak, useful as a gate
        #   avg_logprob        junk median -0.61 vs real -0.49 <- useless, overlaps
        #
        # Note the direction: these hallucinations are *short* stock phrases, so
        # they compress poorly and score LOW. Filtering `compression_ratio > 2.4`
        # (the usual advice, aimed at long repetitive loops) catches none of them.
        # Requiring both conditions removes 81% of hallucinated segments while
        # costing 3% of genuine ones.
        if not keep_low_confidence and (
            seg.compression_ratio < 0.7 and seg.no_speech_prob > 0.8
        ):
            dropped += 1
            continue
        parts.append(seg.text)
        for w in seg.words or []:
            words.append([w.word, round(w.start, 2), round(w.end, 2)])
        mins, secs = divmod(int(seg.end), 60)
        print(f"    {mins:3d}m{secs:02d}s  {seg.text.strip()[:70]}", flush=True)

    raw = "".join(parts).strip()
    text, removed = clean_transcript(raw)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{src.stem}.txt").write_text(text, encoding="utf-8")
    (out_dir / f"{src.stem}.words.json").write_text(
        json.dumps(words, ensure_ascii=False), encoding="utf-8")

    wav.unlink(missing_ok=True)
    return {"file": src.name, "chars": len(text), "words": len(words),
            "removed": removed, "dropped_segments": dropped,
            "duration": round(info.duration, 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio", nargs="+", type=Path)
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--vad", action="store_true",
                    help="enable VAD silence filtering (faster, loses quiet speech)")
    ap.add_argument("--raw-levels", action="store_true",
                    help="skip loudness normalization")
    ap.add_argument("--keep-low-confidence", action="store_true",
                    help="keep low-confidence segments (usually hallucinations)")
    args = ap.parse_args()

    from faster_whisper import WhisperModel

    print(f"loading {args.model} on cuda/float16 ...", flush=True)
    model = WhisperModel(args.model, device="cuda", compute_type="float16")

    results = []
    for src in args.audio:
        if not src.is_file():
            print(f"SKIP (missing): {src}", flush=True)
            continue
        print(f"\n=== {src.name} ({src.stat().st_size/1e6:.1f} MB) ===", flush=True)
        try:
            results.append(transcribe(
                model, src, args.out, vad=args.vad,
                normalize=not args.raw_levels,
                keep_low_confidence=args.keep_low_confidence))
        except Exception as exc:  # one bad file must not kill the run
            print(f"FAILED {src.name}: {exc}", flush=True)
            results.append({"file": src.name, "error": str(exc)})

    print("\n--- summary ---", flush=True)
    for r in results:
        if "error" in r:
            print(f"  {r['file']:<34} ERROR: {r['error']}")
        else:
            print(f"  {r['file']:<34} {r['duration']:7.0f}s  "
                  f"{r['chars']:,} chars  {r['words']:,} words  "
                  f"({r['dropped_segments']} low-confidence segments dropped, "
                  f"{r['removed']:,} chars cleaned)")
    return 0 if all("error" not in r for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
