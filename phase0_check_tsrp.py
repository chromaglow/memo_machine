#!/usr/bin/env python3
"""Phase 0 verification gate: does this .m4a carry an Apple `tsrp` transcript atom?

Usage:
    python phase0_check_tsrp.py path/to/memo.m4a

Walks the MPEG-4 box tree looking for a `tsrp` atom, then heuristically pulls
readable text out of its payload and prints the first 200 characters.

Exit codes: 0 = atom found with extractable text, 1 = atom found but no text,
2 = no atom, 3 = usage/IO error.
"""

import json
import sys
from pathlib import Path

# Container boxes whose payload is itself a sequence of boxes. `meta` is a
# full box: 4 bytes of version/flags precede its children.
CONTAINERS = {b"moov", b"udta", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"mvex"}
FULLBOX_CONTAINERS = {b"meta"}


def walk_boxes(data: bytes, start: int, end: int, depth: int = 0):
    """Yield (type, payload_start, payload_end, depth) for every box in [start, end)."""
    pos = start
    while pos + 8 <= end:
        size = int.from_bytes(data[pos : pos + 4], "big")
        box_type = data[pos + 4 : pos + 8]
        header = 8
        if size == 1:  # 64-bit largesize
            if pos + 16 > end:
                break
            size = int.from_bytes(data[pos + 8 : pos + 16], "big")
            header = 16
        elif size == 0:  # box extends to end of enclosing scope
            size = end - pos
        if size < header or pos + size > end:
            break  # malformed; stop walking this scope rather than looping
        payload_start = pos + header
        yield box_type, payload_start, pos + size, depth
        if box_type in CONTAINERS:
            yield from walk_boxes(data, payload_start, pos + size, depth + 1)
        elif box_type in FULLBOX_CONTAINERS:
            yield from walk_boxes(data, payload_start + 4, pos + size, depth + 1)
        pos += size


def parse_tsrp(payload: bytes):
    """Decode a tsrp payload into (text, word_timings).

    The payload is JSON:
        {"locale": {...},
         "attributedString": {"runs": [tok, idx, tok, idx, ...],
                              "attributeTable": [{"timeRange": [start, end]}, ...]}}

    `runs` alternates a text token with an index into `attributeTable`, so the
    transcript carries per-word timings. An untranscribed recording still gets a
    tsrp atom, but with `attributedString` as an empty string — atom presence
    alone is NOT evidence of a transcript.
    """
    obj = json.loads(payload.decode("utf-8", errors="replace"))
    attributed = obj.get("attributedString")

    if isinstance(attributed, str):  # empty / untranscribed
        return attributed, []
    if not isinstance(attributed, dict):
        return "", []

    runs = attributed.get("runs", [])
    table = attributed.get("attributeTable", [])
    tokens = [x for x in runs if isinstance(x, str)]

    timings = []
    for i in range(0, len(runs) - 1, 2):
        tok, idx = runs[i], runs[i + 1]
        if isinstance(tok, str) and isinstance(idx, int) and 0 <= idx < len(table):
            span = table[idx].get("timeRange")
            if span:
                timings.append((tok, span[0], span[1]))

    return "".join(tokens), timings


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 3
    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"ERROR: not a file: {path}")
        return 3

    data = path.read_bytes()
    print(f"File: {path.name} ({len(data):,} bytes)")

    tsrp_spans = []
    for box_type, p_start, p_end, depth in walk_boxes(data, 0, len(data)):
        label = box_type.decode("ascii", errors="replace")
        print(f"{'  ' * depth}{label}  ({p_end - p_start:,} bytes)")
        if box_type == b"tsrp":
            tsrp_spans.append((p_start, p_end))

    # Fallback: a tsrp atom hiding somewhere the walker didn't reach.
    if not tsrp_spans:
        idx = data.find(b"tsrp")
        if idx >= 4:
            size = int.from_bytes(data[idx - 4 : idx], "big")
            if 8 <= size <= len(data) - (idx - 4):
                print("note: tsrp found by raw scan outside the walked tree")
                tsrp_spans.append((idx + 4, idx - 4 + size))

    if not tsrp_spans:
        print("\nRESULT: FAIL — no tsrp atom in this file.")
        print("Either the transfer stripped it, the recording predates iOS 18,")
        print("or it was never opened in the Voice Memos app.")
        return 2

    start, endpos = tsrp_spans[0]
    print(f"\ntsrp atom present: {endpos - start:,} byte payload")
    try:
        text, timings = parse_tsrp(data[start:endpos])
    except Exception as exc:
        print(f"RESULT: PARTIAL — atom present but payload did not parse: {exc}")
        return 1

    if not text.strip():
        print("RESULT: PARTIAL — atom present but transcript is empty.")
        print("The recording was never transcribed on-device. Remedy: open it once")
        print("in the Voice Memos app to trigger transcription, then re-export.")
        return 1

    print(f"Extracted {len(text):,} chars, {len(timings):,} word timings. First 200:\n")
    print(text[:200])
    print("\nRESULT: PASS — transcript survived the transfer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
